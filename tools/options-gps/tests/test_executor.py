"""Tests for executor.py: instrument names, plan build/validate, dry-run, execution flow, factory."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from executor import (
    OrderRequest,
    ExecutionPlan,
    DryRunExecutor,
    deribit_instrument_name,
    aevo_instrument_name,
    build_execution_plan,
    validate_plan,
    execute_plan,
    get_executor,
)
from exchange import _parse_instrument_key
from pipeline import ScoredStrategy


def _make_scored(strategy):
    """Wrap a StrategyCandidate into a ScoredStrategy for testing."""
    return ScoredStrategy(
        strategy=strategy,
        probability_of_profit=0.55,
        expected_value=100.0,
        tail_risk=50.0,
        loss_profile="premium at risk",
        invalidation_trigger="Close on break",
        reroute_rule="Roll out",
        review_again_at="Review at 50%",
        score=0.8,
        rationale="Test",
    )


class TestInstrumentNames:
    def test_deribit_instrument_name(self):
        name = deribit_instrument_name("BTC", "2026-02-26T08:00:00Z", 67500, "Call")
        assert name == "BTC-26FEB26-67500-C"

    def test_deribit_instrument_name_put(self):
        name = deribit_instrument_name("ETH", "2026-03-15T08:00:00Z", 4000, "Put")
        assert name == "ETH-15MAR26-4000-P"

    def test_aevo_instrument_name(self):
        name = aevo_instrument_name("BTC", 67500, "Call")
        assert name == "BTC-67500-C"

    def test_aevo_instrument_name_put(self):
        name = aevo_instrument_name("SOL", 150, "Put")
        assert name == "SOL-150-P"

    def test_deribit_roundtrip(self):
        name = deribit_instrument_name("BTC", "2026-02-26T08:00:00Z", 67500, "Call")
        parsed = _parse_instrument_key(name)
        assert parsed is not None
        strike, opt_type = parsed
        assert strike == 67500
        assert opt_type == "call"

    def test_aevo_roundtrip(self):
        name = aevo_instrument_name("BTC", 68000, "Put")
        parsed = _parse_instrument_key(name)
        assert parsed is not None
        strike, opt_type = parsed
        assert strike == 68000
        assert opt_type == "put"

    def test_deribit_empty_expiry(self):
        name = deribit_instrument_name("BTC", "", 67500, "Call")
        assert "UNKNOWN" in name


class TestBuildPlan:
    def test_single_leg(self, sample_strategy, sample_exchange_quotes, btc_option_data):
        scored = _make_scored(sample_strategy)
        plan = build_execution_plan(scored, "BTC", "deribit", sample_exchange_quotes, btc_option_data)
        assert len(plan.orders) == 1
        assert plan.orders[0].action == "BUY"
        assert plan.orders[0].exchange == "deribit"
        assert plan.orders[0].strike == 67500
        assert plan.orders[0].option_type == "call"
        assert "67500" in plan.orders[0].instrument
        assert plan.estimated_cost > 0

    def test_multi_leg(self, multi_leg_strategy, sample_exchange_quotes, btc_option_data):
        scored = _make_scored(multi_leg_strategy)
        plan = build_execution_plan(scored, "BTC", "deribit", sample_exchange_quotes, btc_option_data)
        assert len(plan.orders) == 2
        actions = [o.action for o in plan.orders]
        assert "BUY" in actions
        assert "SELL" in actions

    def test_exchange_override(self, multi_leg_strategy, sample_exchange_quotes, btc_option_data):
        scored = _make_scored(multi_leg_strategy)
        plan = build_execution_plan(scored, "BTC", "deribit", sample_exchange_quotes, btc_option_data)
        for order in plan.orders:
            assert order.exchange == "deribit"
            assert "BTC-" in order.instrument

    def test_aevo_names(self, sample_strategy, sample_exchange_quotes, btc_option_data):
        scored = _make_scored(sample_strategy)
        plan = build_execution_plan(scored, "BTC", "aevo", sample_exchange_quotes, btc_option_data)
        assert len(plan.orders) == 1
        assert plan.orders[0].instrument == "BTC-67500-C"

    def test_auto_route(self, sample_strategy, sample_exchange_quotes, btc_option_data):
        scored = _make_scored(sample_strategy)
        plan = build_execution_plan(scored, "BTC", None, sample_exchange_quotes, btc_option_data)
        assert plan.exchange == "auto"
        assert len(plan.orders) == 1
        assert plan.orders[0].exchange in ("deribit", "aevo")

    def test_estimated_cost_multi_leg(self, multi_leg_strategy, sample_exchange_quotes, btc_option_data):
        scored = _make_scored(multi_leg_strategy)
        plan = build_execution_plan(scored, "BTC", "deribit", sample_exchange_quotes, btc_option_data)
        buy_total = sum(o.price * o.quantity for o in plan.orders if o.action == "BUY")
        sell_total = sum(o.price * o.quantity for o in plan.orders if o.action == "SELL")
        assert plan.estimated_cost == pytest.approx(buy_total - sell_total)


class TestValidatePlan:
    def test_valid(self):
        plan = ExecutionPlan(
            strategy_description="Test", strategy_type="long_call",
            exchange="deribit", asset="BTC", expiry="",
            orders=[
                OrderRequest("BTC-26FEB26-67500-C", "BUY", 1, "limit", 660.0, "deribit", 0,
                            strike=67500, option_type="call"),
            ],
        )
        valid, err = validate_plan(plan)
        assert valid is True
        assert err == ""

    def test_empty_orders(self):
        plan = ExecutionPlan(
            strategy_description="Test", strategy_type="long_call",
            exchange="deribit", asset="BTC", expiry="",
        )
        valid, err = validate_plan(plan)
        assert valid is False
        assert "No orders" in err

    def test_zero_price(self):
        plan = ExecutionPlan(
            strategy_description="Test", strategy_type="long_call",
            exchange="deribit", asset="BTC", expiry="",
            orders=[
                OrderRequest("BTC-26FEB26-67500-C", "BUY", 1, "limit", 0.0, "deribit", 0,
                            strike=67500, option_type="call"),
            ],
        )
        valid, err = validate_plan(plan)
        assert valid is False
        assert "price" in err.lower()

    def test_zero_quantity(self):
        plan = ExecutionPlan(
            strategy_description="Test", strategy_type="long_call",
            exchange="deribit", asset="BTC", expiry="",
            orders=[
                OrderRequest("BTC-26FEB26-67500-C", "BUY", 0, "limit", 660.0, "deribit", 0,
                            strike=67500, option_type="call"),
            ],
        )
        valid, err = validate_plan(plan)
        assert valid is False
        assert "quantity" in err.lower()

    def test_empty_instrument(self):
        plan = ExecutionPlan(
            strategy_description="Test", strategy_type="long_call",
            exchange="deribit", asset="BTC", expiry="",
            orders=[
                OrderRequest("", "BUY", 1, "limit", 660.0, "deribit", 0,
                            strike=67500, option_type="call"),
            ],
        )
        valid, err = validate_plan(plan)
        assert valid is False
        assert "instrument" in err.lower()


class TestDryRunExecutor:
    def test_authenticate(self, sample_exchange_quotes):
        executor = DryRunExecutor(sample_exchange_quotes)
        assert executor.authenticate() is True

    def test_place_buy(self, sample_exchange_quotes):
        executor = DryRunExecutor(sample_exchange_quotes)
        order = OrderRequest("BTC-67500-C", "BUY", 1, "limit", 660.0, "dry_run", 0,
                             strike=67500, option_type="call")
        result = executor.place_order(order)
        assert result.status == "simulated"
        assert result.fill_quantity == 1
        assert result.fill_price == 655.0

    def test_place_sell(self, sample_exchange_quotes):
        executor = DryRunExecutor(sample_exchange_quotes)
        order = OrderRequest("BTC-67500-C", "SELL", 1, "limit", 620.0, "dry_run", 0,
                             strike=67500, option_type="call")
        result = executor.place_order(order)
        assert result.status == "simulated"
        assert result.fill_quantity == 1
        assert result.fill_price == 620.0

    def test_missing_strike(self, sample_exchange_quotes):
        executor = DryRunExecutor(sample_exchange_quotes)
        order = OrderRequest("INVALID", "BUY", 1, "limit", 100.0, "dry_run", 0)
        result = executor.place_order(order)
        assert result.status == "error"

    def test_no_matching_quote_uses_order_price(self, sample_exchange_quotes):
        executor = DryRunExecutor(sample_exchange_quotes)
        order = OrderRequest("BTC-99999-C", "BUY", 1, "limit", 100.0, "dry_run", 0,
                             strike=99999, option_type="call")
        result = executor.place_order(order)
        assert result.status == "simulated"
        assert result.fill_price == 100.0


class TestExecuteFlow:
    def test_single_leg(self, sample_strategy, sample_exchange_quotes, btc_option_data):
        scored = _make_scored(sample_strategy)
        plan = build_execution_plan(scored, "BTC", "deribit", sample_exchange_quotes, btc_option_data)
        plan.dry_run = True
        executor = DryRunExecutor(sample_exchange_quotes)
        report = execute_plan(plan, executor)
        assert report.all_filled is True
        assert len(report.results) == 1
        assert report.results[0].status == "simulated"

    def test_multi_leg(self, multi_leg_strategy, sample_exchange_quotes, btc_option_data):
        scored = _make_scored(multi_leg_strategy)
        plan = build_execution_plan(scored, "BTC", "deribit", sample_exchange_quotes, btc_option_data)
        plan.dry_run = True
        executor = DryRunExecutor(sample_exchange_quotes)
        report = execute_plan(plan, executor)
        assert report.all_filled is True
        assert len(report.results) == 2

    def test_net_cost(self, multi_leg_strategy, sample_exchange_quotes, btc_option_data):
        scored = _make_scored(multi_leg_strategy)
        plan = build_execution_plan(scored, "BTC", "deribit", sample_exchange_quotes, btc_option_data)
        plan.dry_run = True
        executor = DryRunExecutor(sample_exchange_quotes)
        report = execute_plan(plan, executor)
        buy_total = sum(r.fill_price * r.fill_quantity for r in report.results if r.action == "BUY")
        sell_total = sum(r.fill_price * r.fill_quantity for r in report.results if r.action == "SELL")
        assert report.net_cost == pytest.approx(buy_total - sell_total)

    def test_auto_routing_uses_factory(self, sample_strategy, sample_exchange_quotes, btc_option_data):
        """When plan.exchange is 'auto', execute_plan with callable factory uses per-order executor."""
        scored = _make_scored(sample_strategy)
        plan = build_execution_plan(scored, "BTC", None, sample_exchange_quotes, btc_option_data)
        plan.dry_run = True
        def factory(ex):
            return get_executor(ex, sample_exchange_quotes, dry_run=True)
        report = execute_plan(plan, factory)
        assert report.all_filled is True
        assert len(report.results) == 1
        assert report.results[0].status == "simulated"


class TestGetExecutor:
    def test_dry_run(self, sample_exchange_quotes):
        executor = get_executor("deribit", sample_exchange_quotes, dry_run=True)
        assert isinstance(executor, DryRunExecutor)

    def test_dry_run_ignores_exchange(self, sample_exchange_quotes):
        executor = get_executor("aevo", sample_exchange_quotes, dry_run=True)
        assert isinstance(executor, DryRunExecutor)

    def test_missing_deribit_creds(self, sample_exchange_quotes, monkeypatch):
        monkeypatch.delenv("DERIBIT_CLIENT_ID", raising=False)
        monkeypatch.delenv("DERIBIT_CLIENT_SECRET", raising=False)
        with pytest.raises(ValueError, match="DERIBIT_CLIENT_ID"):
            get_executor("deribit", sample_exchange_quotes, dry_run=False)

    def test_missing_aevo_creds(self, sample_exchange_quotes, monkeypatch):
        monkeypatch.delenv("AEVO_API_KEY", raising=False)
        monkeypatch.delenv("AEVO_API_SECRET", raising=False)
        with pytest.raises(ValueError, match="AEVO_API_KEY"):
            get_executor("aevo", sample_exchange_quotes, dry_run=False)

    def test_unknown_exchange(self, sample_exchange_quotes):
        with pytest.raises(ValueError, match="Unknown exchange"):
            get_executor("binance", sample_exchange_quotes, dry_run=False)
