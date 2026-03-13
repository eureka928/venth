"""Tests for executor.py: autonomous execution — instrument names, plan building,
validation, dry-run simulation, execution flow, and executor factory."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import requests
import pytest
from unittest.mock import Mock, patch
from executor import (
    OrderRequest,
    OrderResult,
    ExecutionPlan,
    ExecutionReport,
    BaseExecutor,
    DryRunExecutor,
    AevoExecutor,
    deribit_instrument_name,
    aevo_instrument_name,
    build_execution_plan,
    validate_plan,
    execute_plan,
    get_executor,
    save_execution_log,
    compute_execution_savings,
    check_slippage,
    _compute_slippage,
    _is_retryable,
    _monitor_order,
    _cancel_filled_orders,
    _now_iso,
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
        """Build a Deribit name then parse it back — should recover strike and type."""
        name = deribit_instrument_name("BTC", "2026-02-26T08:00:00Z", 67500, "Call")
        parsed = _parse_instrument_key(name)
        assert parsed is not None
        strike, opt_type = parsed
        assert strike == 67500
        assert opt_type == "call"

    def test_aevo_roundtrip(self):
        """Build an Aevo name then parse it back — should recover strike and type."""
        name = aevo_instrument_name("BTC", 68000, "Put")
        parsed = _parse_instrument_key(name)
        assert parsed is not None
        strike, opt_type = parsed
        assert strike == 68000
        assert opt_type == "put"

    def test_deribit_empty_expiry(self):
        """Empty expiry falls back to UNKNOWN date part."""
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
        """--exchange deribit → all orders use Deribit names."""
        scored = _make_scored(multi_leg_strategy)
        plan = build_execution_plan(scored, "BTC", "deribit", sample_exchange_quotes, btc_option_data)
        for order in plan.orders:
            assert order.exchange == "deribit"
            assert "BTC-" in order.instrument

    def test_aevo_names(self, sample_strategy, sample_exchange_quotes, btc_option_data):
        """--exchange aevo → Aevo instrument names (no date)."""
        scored = _make_scored(sample_strategy)
        plan = build_execution_plan(scored, "BTC", "aevo", sample_exchange_quotes, btc_option_data)
        assert len(plan.orders) == 1
        assert plan.orders[0].instrument == "BTC-67500-C"

    def test_auto_route(self, sample_strategy, sample_exchange_quotes, btc_option_data):
        """exchange=None → auto-routes via leg_divergences."""
        scored = _make_scored(sample_strategy)
        plan = build_execution_plan(scored, "BTC", None, sample_exchange_quotes, btc_option_data)
        assert plan.exchange == "auto"
        assert len(plan.orders) == 1
        # Should pick a valid exchange
        assert plan.orders[0].exchange in ("deribit", "aevo")

    def test_estimated_cost_multi_leg(self, multi_leg_strategy, sample_exchange_quotes, btc_option_data):
        """estimated_cost = buy prices - sell prices."""
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
            orders=[OrderRequest("BTC-26FEB26-67500-C", "BUY", 1, "limit", 660.0, "deribit", 0,
                                 strike=67500, option_type="call")],
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
            orders=[OrderRequest("BTC-26FEB26-67500-C", "BUY", 1, "limit", 0.0, "deribit", 0,
                                 strike=67500, option_type="call")],
        )
        valid, err = validate_plan(plan)
        assert valid is False
        assert "price" in err.lower()

    def test_zero_quantity(self):
        plan = ExecutionPlan(
            strategy_description="Test", strategy_type="long_call",
            exchange="deribit", asset="BTC", expiry="",
            orders=[OrderRequest("BTC-26FEB26-67500-C", "BUY", 0, "limit", 660.0, "deribit", 0,
                                 strike=67500, option_type="call")],
        )
        valid, err = validate_plan(plan)
        assert valid is False
        assert "quantity" in err.lower()

    def test_empty_instrument(self):
        plan = ExecutionPlan(
            strategy_description="Test", strategy_type="long_call",
            exchange="deribit", asset="BTC", expiry="",
            orders=[OrderRequest("", "BUY", 1, "limit", 660.0, "deribit", 0,
                                 strike=67500, option_type="call")],
        )
        valid, err = validate_plan(plan)
        assert valid is False
        assert "instrument" in err.lower()


class TestDryRunExecutor:
    def test_authenticate(self, sample_exchange_quotes):
        executor = DryRunExecutor(sample_exchange_quotes)
        assert executor.authenticate() is True

    def test_place_buy(self, sample_exchange_quotes):
        """BUY fills at best ask from quotes."""
        executor = DryRunExecutor(sample_exchange_quotes)
        order = OrderRequest("BTC-67500-C", "BUY", 1, "limit", 660.0, "dry_run", 0,
                             strike=67500, option_type="call")
        result = executor.place_order(order)
        assert result.status == "simulated"
        assert result.fill_quantity == 1
        assert result.fill_price == 655.0  # best ask from aevo

    def test_place_sell(self, sample_exchange_quotes):
        """SELL fills at best bid from quotes."""
        executor = DryRunExecutor(sample_exchange_quotes)
        order = OrderRequest("BTC-67500-C", "SELL", 1, "limit", 620.0, "dry_run", 0,
                             strike=67500, option_type="call")
        result = executor.place_order(order)
        assert result.status == "simulated"
        assert result.fill_quantity == 1
        assert result.fill_price == 620.0  # best bid from aevo

    def test_missing_strike(self, sample_exchange_quotes):
        """Order without strike/option_type returns error."""
        executor = DryRunExecutor(sample_exchange_quotes)
        order = OrderRequest("INVALID", "BUY", 1, "limit", 100.0, "dry_run", 0)
        result = executor.place_order(order)
        assert result.status == "error"

    def test_no_matching_quote_uses_order_price(self, sample_exchange_quotes):
        """When no exchange quote matches, falls back to order limit price."""
        executor = DryRunExecutor(sample_exchange_quotes)
        order = OrderRequest("BTC-99999-C", "BUY", 1, "limit", 100.0, "dry_run", 0,
                             strike=99999, option_type="call")
        result = executor.place_order(order)
        assert result.status == "simulated"
        assert result.fill_price == 100.0

    def test_get_order_status(self, sample_exchange_quotes):
        executor = DryRunExecutor(sample_exchange_quotes)
        assert executor.get_order_status("any-id") == "simulated"

    def test_cancel_order(self, sample_exchange_quotes):
        executor = DryRunExecutor(sample_exchange_quotes)
        assert executor.cancel_order("any-id") is True


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
        """net_cost = sum(buy fills) - sum(sell fills)."""
        scored = _make_scored(multi_leg_strategy)
        plan = build_execution_plan(scored, "BTC", "deribit", sample_exchange_quotes, btc_option_data)
        plan.dry_run = True
        executor = DryRunExecutor(sample_exchange_quotes)
        report = execute_plan(plan, executor)
        buy_total = sum(r.fill_price * r.fill_quantity for r in report.results if r.action == "BUY")
        sell_total = sum(r.fill_price * r.fill_quantity for r in report.results if r.action == "SELL")
        assert report.net_cost == pytest.approx(buy_total - sell_total)

    def test_summary_message(self, sample_strategy, sample_exchange_quotes, btc_option_data):
        scored = _make_scored(sample_strategy)
        plan = build_execution_plan(scored, "BTC", "deribit", sample_exchange_quotes, btc_option_data)
        plan.dry_run = True
        executor = DryRunExecutor(sample_exchange_quotes)
        report = execute_plan(plan, executor)
        assert "simulated" in report.summary
        assert "Net cost" in report.summary


class TestGetExecutor:
    def test_dry_run(self, sample_exchange_quotes):
        executor = get_executor("deribit", sample_exchange_quotes, dry_run=True)
        assert isinstance(executor, DryRunExecutor)

    def test_dry_run_ignores_exchange(self, sample_exchange_quotes):
        """dry_run=True always returns DryRunExecutor regardless of exchange."""
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

    def test_auto_route_returns_factory(self, sample_exchange_quotes):
        """exchange=None with dry_run=False returns a callable factory."""
        result = get_executor(None, sample_exchange_quotes, dry_run=False)
        assert callable(result)
        assert not isinstance(result, DryRunExecutor)

    def test_auto_route_dry_run_returns_executor(self, sample_exchange_quotes):
        """exchange=None with dry_run=True still returns DryRunExecutor."""
        result = get_executor(None, sample_exchange_quotes, dry_run=True)
        assert isinstance(result, DryRunExecutor)


class TestExecuteWithFactory:
    def test_factory_routes_per_leg(self, sample_exchange_quotes):
        """execute_plan with a callable factory creates executors per exchange."""
        plan = ExecutionPlan(
            strategy_description="Test spread", strategy_type="call_debit_spread",
            exchange="auto", asset="BTC", expiry="",
            orders=[
                OrderRequest("BTC-67500-C", "BUY", 1, "limit", 655.0, "aevo", 0,
                             strike=67500, option_type="call"),
                OrderRequest("BTC-26FEB26-68000-C", "SELL", 1, "limit", 385.0, "deribit", 1,
                             strike=68000, option_type="call"),
            ],
            dry_run=True,
        )

        def _factory(exchange: str) -> DryRunExecutor:
            return DryRunExecutor(sample_exchange_quotes)

        report = execute_plan(plan, _factory)
        assert report.all_filled is True
        assert len(report.results) == 2

    def test_factory_auth_failure(self, sample_exchange_quotes):
        """Factory executor that fails auth stops execution."""
        plan = ExecutionPlan(
            strategy_description="Test", strategy_type="long_call",
            exchange="auto", asset="BTC", expiry="",
            orders=[
                OrderRequest("BTC-67500-C", "BUY", 1, "limit", 655.0, "bad_exchange", 0,
                             strike=67500, option_type="call"),
            ],
            dry_run=False,
        )

        class FailAuthExecutor(DryRunExecutor):
            def authenticate(self):
                return False

        def _factory(exchange: str):
            return FailAuthExecutor(sample_exchange_quotes)

        report = execute_plan(plan, _factory)
        assert report.all_filled is False
        assert "Authentication failed" in report.summary


class TestSlippage:
    def test_compute_slippage_buy_worse(self):
        """BUY at higher price = positive slippage."""
        slip = _compute_slippage(100.0, 102.0, "BUY")
        assert slip == pytest.approx(2.0)

    def test_compute_slippage_buy_better(self):
        """BUY at lower price = negative slippage (favorable)."""
        slip = _compute_slippage(100.0, 98.0, "BUY")
        assert slip == pytest.approx(-2.0)

    def test_compute_slippage_sell_worse(self):
        """SELL at lower price = positive slippage."""
        slip = _compute_slippage(100.0, 98.0, "SELL")
        assert slip == pytest.approx(2.0)

    def test_compute_slippage_zero_limit(self):
        """Zero limit price returns 0 slippage."""
        assert _compute_slippage(0.0, 100.0, "BUY") == 0.0

    def test_check_slippage_within(self):
        from executor import OrderResult
        r = OrderResult("id", "filled", 102.0, 1, "X", "BUY", "test", slippage_pct=1.5)
        assert check_slippage(r, 2.0) is True

    def test_check_slippage_exceeded(self):
        from executor import OrderResult
        r = OrderResult("id", "filled", 105.0, 1, "X", "BUY", "test", slippage_pct=5.0)
        assert check_slippage(r, 2.0) is False

    def test_execute_plan_halts_on_slippage(self, sample_exchange_quotes):
        """execute_plan with max_slippage_pct halts when exceeded."""
        plan = ExecutionPlan(
            strategy_description="Test", strategy_type="long_call",
            exchange="deribit", asset="BTC", expiry="",
            orders=[
                OrderRequest("BTC-67500-C", "BUY", 1, "limit", 600.0, "dry_run", 0,
                             strike=67500, option_type="call"),
            ],
            dry_run=True,
        )
        executor = DryRunExecutor(sample_exchange_quotes)
        # Fill will be at 655.0 (aevo ask), limit is 600 -> slippage ~9.2%
        report = execute_plan(plan, executor, max_slippage_pct=1.0)
        assert report.all_filled is False
        assert "Slippage exceeded" in report.summary

    def test_execute_plan_ok_slippage(self, sample_exchange_quotes):
        """execute_plan passes when slippage is within limit."""
        plan = ExecutionPlan(
            strategy_description="Test", strategy_type="long_call",
            exchange="deribit", asset="BTC", expiry="",
            orders=[
                OrderRequest("BTC-67500-C", "BUY", 1, "limit", 655.0, "dry_run", 0,
                             strike=67500, option_type="call"),
            ],
            dry_run=True,
        )
        executor = DryRunExecutor(sample_exchange_quotes)
        # Fill at 655.0, limit 655.0 -> 0% slippage
        report = execute_plan(plan, executor, max_slippage_pct=5.0)
        assert report.all_filled is True


class TestMaxLossBudget:
    def test_validate_plan_within_budget(self):
        plan = ExecutionPlan(
            strategy_description="Test", strategy_type="long_call",
            exchange="deribit", asset="BTC", expiry="",
            orders=[OrderRequest("BTC-67500-C", "BUY", 1, "limit", 660.0, "deribit", 0,
                                 strike=67500, option_type="call")],
            estimated_max_loss=500.0,
        )
        valid, err = validate_plan(plan, max_loss_budget=1000.0)
        assert valid is True

    def test_validate_plan_exceeds_budget(self):
        plan = ExecutionPlan(
            strategy_description="Test", strategy_type="long_call",
            exchange="deribit", asset="BTC", expiry="",
            orders=[OrderRequest("BTC-67500-C", "BUY", 1, "limit", 660.0, "deribit", 0,
                                 strike=67500, option_type="call")],
            estimated_max_loss=1500.0,
        )
        valid, err = validate_plan(plan, max_loss_budget=1000.0)
        assert valid is False
        assert "budget" in err.lower()

    def test_validate_plan_no_budget(self):
        """No budget = no check."""
        plan = ExecutionPlan(
            strategy_description="Test", strategy_type="long_call",
            exchange="deribit", asset="BTC", expiry="",
            orders=[OrderRequest("BTC-67500-C", "BUY", 1, "limit", 660.0, "deribit", 0,
                                 strike=67500, option_type="call")],
            estimated_max_loss=99999.0,
        )
        valid, err = validate_plan(plan)
        assert valid is True


class TestSizeMultiplier:
    def test_size_doubles_quantity(self, sample_strategy, sample_exchange_quotes, btc_option_data):
        scored = _make_scored(sample_strategy)
        plan = build_execution_plan(scored, "BTC", "deribit", sample_exchange_quotes, btc_option_data,
                                    size_multiplier=3)
        assert plan.orders[0].quantity == 3

    def test_size_default_one(self, sample_strategy, sample_exchange_quotes, btc_option_data):
        scored = _make_scored(sample_strategy)
        plan = build_execution_plan(scored, "BTC", "deribit", sample_exchange_quotes, btc_option_data)
        assert plan.orders[0].quantity == 1

    def test_size_scales_max_loss(self, sample_strategy, sample_exchange_quotes, btc_option_data):
        scored = _make_scored(sample_strategy)
        plan1 = build_execution_plan(scored, "BTC", "deribit", sample_exchange_quotes, btc_option_data,
                                     size_multiplier=1)
        plan2 = build_execution_plan(scored, "BTC", "deribit", sample_exchange_quotes, btc_option_data,
                                     size_multiplier=5)
        assert plan2.estimated_max_loss == pytest.approx(plan1.estimated_max_loss * 5)


class TestExecutionLog:
    def test_save_and_load(self, sample_strategy, sample_exchange_quotes, btc_option_data, tmp_path):
        import json
        scored = _make_scored(sample_strategy)
        plan = build_execution_plan(scored, "BTC", "deribit", sample_exchange_quotes, btc_option_data)
        plan.dry_run = True
        executor = DryRunExecutor(sample_exchange_quotes)
        report = execute_plan(plan, executor)

        log_path = str(tmp_path / "exec_log.json")
        save_execution_log(report, log_path)

        with open(log_path) as f:
            log = json.load(f)

        assert log["mode"] == "dry_run"
        assert log["asset"] == "BTC"
        assert log["all_filled"] is True
        assert len(log["fills"]) == 1
        assert log["fills"][0]["status"] == "simulated"
        assert "timestamp" in log
        assert "slippage_total_pct" in log


class TestRetryable:
    def test_timeout_is_retryable(self):
        assert _is_retryable(requests.Timeout()) is True

    def test_connection_error_is_retryable(self):
        assert _is_retryable(requests.ConnectionError()) is True

    def test_value_error_not_retryable(self):
        assert _is_retryable(ValueError("bad")) is False

    def test_http_429_retryable(self):
        from unittest.mock import Mock
        resp = Mock()
        resp.status_code = 429
        err = requests.HTTPError(response=resp)
        assert _is_retryable(err) is True

    def test_http_400_not_retryable(self):
        from unittest.mock import Mock
        resp = Mock()
        resp.status_code = 400
        err = requests.HTTPError(response=resp)
        assert _is_retryable(err) is False


class TestTimestampsAndLatency:
    """Verify that OrderResult and ExecutionReport include timing data."""

    def test_dry_run_result_has_timestamp(self, sample_exchange_quotes):
        executor = DryRunExecutor(sample_exchange_quotes)
        order = OrderRequest("BTC-67500-C", "BUY", 1, "limit", 660.0, "dry_run", 0,
                             strike=67500, option_type="call")
        result = executor.place_order(order)
        assert result.timestamp != ""
        assert "T" in result.timestamp  # ISO 8601 format

    def test_dry_run_result_has_latency(self, sample_exchange_quotes):
        executor = DryRunExecutor(sample_exchange_quotes)
        order = OrderRequest("BTC-67500-C", "BUY", 1, "limit", 660.0, "dry_run", 0,
                             strike=67500, option_type="call")
        result = executor.place_order(order)
        assert result.latency_ms >= 0

    def test_error_result_has_timestamp(self, sample_exchange_quotes):
        executor = DryRunExecutor(sample_exchange_quotes)
        order = OrderRequest("INVALID", "BUY", 1, "limit", 100.0, "dry_run", 0)
        result = executor.place_order(order)
        assert result.timestamp != ""

    def test_report_has_started_finished(self, sample_strategy, sample_exchange_quotes, btc_option_data):
        scored = _make_scored(sample_strategy)
        plan = build_execution_plan(scored, "BTC", "deribit", sample_exchange_quotes, btc_option_data)
        plan.dry_run = True
        executor = DryRunExecutor(sample_exchange_quotes)
        report = execute_plan(plan, executor)
        assert report.started_at != ""
        assert report.finished_at != ""
        assert "T" in report.started_at
        assert "T" in report.finished_at

    def test_now_iso_format(self):
        ts = _now_iso()
        assert "T" in ts
        assert "+" in ts or "Z" in ts  # timezone info


class TestOrderMonitoring:
    """Test _monitor_order polling and timeout behavior."""

    def test_immediate_fill(self, sample_exchange_quotes):
        """DryRunExecutor always returns 'simulated' = terminal."""
        executor = DryRunExecutor(sample_exchange_quotes)
        status = _monitor_order(executor, "dry-123", timeout_seconds=5.0)
        assert status == "simulated"

    def test_timeout_triggers_cancel(self):
        """Executor that always returns 'open' should timeout and cancel."""
        class StuckExecutor(BaseExecutor):
            def authenticate(self): return True
            def place_order(self, order): pass
            def get_order_status(self, order_id): return "open"
            def cancel_order(self, order_id): return True

        executor = StuckExecutor()
        status = _monitor_order(executor, "stuck-123", timeout_seconds=0.1, poll_interval=0.05)
        assert status == "timeout"

    def test_delayed_fill(self):
        """Executor that fills after a few polls."""
        call_count = {"n": 0}

        class DelayedExecutor(BaseExecutor):
            def authenticate(self): return True
            def place_order(self, order): pass
            def get_order_status(self, order_id):
                call_count["n"] += 1
                if call_count["n"] >= 3:
                    return "filled"
                return "open"
            def cancel_order(self, order_id): return True

        executor = DelayedExecutor()
        status = _monitor_order(executor, "delay-123", timeout_seconds=5.0, poll_interval=0.01)
        assert status == "filled"
        assert call_count["n"] >= 3

    def test_rejected_is_terminal(self):
        """Executor returning 'rejected' should stop immediately."""
        class RejectExecutor(BaseExecutor):
            def authenticate(self): return True
            def place_order(self, order): pass
            def get_order_status(self, order_id): return "rejected"
            def cancel_order(self, order_id): return True

        executor = RejectExecutor()
        status = _monitor_order(executor, "rej-123", timeout_seconds=5.0)
        assert status == "rejected"


class TestAutoCancel:
    """Test _cancel_filled_orders cancellation logic."""

    def test_cancels_filled_orders(self):
        results = [
            OrderResult("id-1", "filled", 100.0, 1, "X", "BUY", "deribit"),
            OrderResult("id-2", "filled", 200.0, 1, "Y", "SELL", "aevo"),
        ]
        cancel_log = []

        class TrackingExecutor(BaseExecutor):
            def authenticate(self): return True
            def place_order(self, order): pass
            def get_order_status(self, order_id): return "filled"
            def cancel_order(self, order_id):
                cancel_log.append(order_id)
                return True

        tracker = TrackingExecutor()
        cancelled = _cancel_filled_orders(results, lambda ex: tracker)
        assert cancelled == ["id-1", "id-2"]
        assert cancel_log == ["id-1", "id-2"]

    def test_skips_non_filled(self):
        results = [
            OrderResult("id-1", "error", 0.0, 0, "X", "BUY", "deribit"),
            OrderResult("id-2", "filled", 200.0, 1, "Y", "SELL", "aevo"),
        ]
        tracker = DryRunExecutor([])
        cancelled = _cancel_filled_orders(results, lambda ex: tracker)
        assert cancelled == ["id-2"]

    def test_empty_results(self):
        cancelled = _cancel_filled_orders([], lambda ex: DryRunExecutor([]))
        assert cancelled == []

    def test_cancel_failure_skips(self):
        """If cancel_order returns False, order ID not in cancelled list."""
        results = [
            OrderResult("id-1", "filled", 100.0, 1, "X", "BUY", "deribit"),
        ]

        class FailCancelExecutor(BaseExecutor):
            def authenticate(self): return True
            def place_order(self, order): pass
            def get_order_status(self, order_id): return "filled"
            def cancel_order(self, order_id): return False

        cancelled = _cancel_filled_orders(results, lambda ex: FailCancelExecutor())
        assert cancelled == []


class TestExecutionSavings:
    """Test compute_execution_savings comparison logic."""

    def test_savings_when_cheaper(self, btc_option_data):
        """When execution price < Synth price, savings should be positive."""
        plan = ExecutionPlan(
            strategy_description="Test", strategy_type="long_call",
            exchange="auto", asset="BTC", expiry="",
            orders=[OrderRequest("BTC-67500-C", "BUY", 1, "limit", 600.0, "aevo", 0,
                                 strike=67500, option_type="call")],
        )
        savings = compute_execution_savings(plan, btc_option_data)
        # Synth price for 67500 call = 638.43, exec = 600 → savings = 38.43
        assert savings["savings_usd"] > 0
        assert savings["synth_theoretical_cost"] == pytest.approx(638.43)
        assert savings["execution_cost"] == pytest.approx(600.0)

    def test_no_savings_at_synth_price(self, btc_option_data):
        """When execution price = Synth price, savings = 0."""
        plan = ExecutionPlan(
            strategy_description="Test", strategy_type="long_call",
            exchange="auto", asset="BTC", expiry="",
            orders=[OrderRequest("BTC-67500-C", "BUY", 1, "limit", 638.43, "deribit", 0,
                                 strike=67500, option_type="call")],
        )
        savings = compute_execution_savings(plan, btc_option_data)
        assert savings["savings_usd"] == pytest.approx(0.0)

    def test_multi_leg_savings(self, btc_option_data):
        """Multi-leg spread: savings on net cost."""
        plan = ExecutionPlan(
            strategy_description="Test spread", strategy_type="call_debit_spread",
            exchange="auto", asset="BTC", expiry="",
            orders=[
                OrderRequest("BTC-67000-C", "BUY", 1, "limit", 950.0, "aevo", 0,
                             strike=67000, option_type="call"),
                OrderRequest("BTC-68000-C", "SELL", 1, "limit", 390.0, "deribit", 1,
                             strike=68000, option_type="call"),
            ],
        )
        savings = compute_execution_savings(plan, btc_option_data)
        # Synth: 987.04 - 373.27 = 613.77, Exec: 950 - 390 = 560 → savings ~53.77
        assert savings["savings_usd"] > 0
        assert "savings_pct" in savings


class TestExecutePlanWithTimeout:
    """Test execute_plan timeout_seconds parameter."""

    def test_timeout_not_used_for_simulated(self, sample_exchange_quotes):
        """Dry-run fills are 'simulated', never 'open', so timeout monitoring doesn't trigger."""
        plan = ExecutionPlan(
            strategy_description="Test", strategy_type="long_call",
            exchange="deribit", asset="BTC", expiry="",
            orders=[OrderRequest("BTC-67500-C", "BUY", 1, "limit", 655.0, "dry_run", 0,
                                 strike=67500, option_type="call")],
            dry_run=True,
        )
        executor = DryRunExecutor(sample_exchange_quotes)
        report = execute_plan(plan, executor, timeout_seconds=5.0)
        assert report.all_filled is True
        # Status should still be simulated, not changed by monitoring
        assert report.results[0].status == "simulated"

    def test_timeout_triggers_for_open_orders(self):
        """Executor that returns 'open' status should trigger monitoring → timeout."""
        class OpenExecutor(BaseExecutor):
            def authenticate(self): return True
            def place_order(self, order):
                return OrderResult(
                    order_id="open-123", status="open", fill_price=0.0,
                    fill_quantity=0, instrument=order.instrument,
                    action=order.action, exchange="test",
                )
            def get_order_status(self, order_id): return "open"
            def cancel_order(self, order_id): return True

        plan = ExecutionPlan(
            strategy_description="Test", strategy_type="long_call",
            exchange="test", asset="BTC", expiry="",
            orders=[OrderRequest("BTC-67500-C", "BUY", 1, "limit", 655.0, "test", 0,
                                 strike=67500, option_type="call")],
        )
        report = execute_plan(plan, OpenExecutor(), timeout_seconds=0.1)
        # Should timeout and fail
        assert report.all_filled is False
        assert report.results[0].status == "timeout"

    def test_plan_timeout_default(self):
        """ExecutionPlan has default timeout_seconds=30."""
        plan = ExecutionPlan(
            strategy_description="Test", strategy_type="long_call",
            exchange="deribit", asset="BTC", expiry="",
        )
        assert plan.timeout_seconds == 30.0


class TestAevoSigning:
    """Test Aevo HMAC-SHA256 4-part signing."""

    def test_sign_uses_four_parts(self):
        """Signature message = timestamp + method + path + body."""
        import hashlib, hmac as hmac_mod
        executor = AevoExecutor("test-key", "test-secret", testnet=True)
        sig = executor._sign("12345", "POST", "/orders", '{"side":"buy"}')
        expected_msg = '12345POST/orders{"side":"buy"}'
        expected_sig = hmac_mod.new(
            b"test-secret", expected_msg.encode(), hashlib.sha256,
        ).hexdigest()
        assert sig == expected_sig

    def test_headers_include_all_fields(self):
        executor = AevoExecutor("my-key", "my-secret", testnet=True)
        headers = executor._headers("POST", "/orders", '{"side":"buy"}')
        assert headers["AEVO-KEY"] == "my-key"
        assert "AEVO-TIMESTAMP" in headers
        assert "AEVO-SIGNATURE" in headers
        assert headers["Content-Type"] == "application/json"

    def test_sign_empty_body(self):
        """GET request with empty body still produces valid signature."""
        executor = AevoExecutor("k", "s", testnet=True)
        sig = executor._sign("999", "GET", "/orders/123", "")
        assert len(sig) == 64  # SHA-256 hex digest


class TestExecutionLogEnhanced:
    """Verify execution log includes new fields."""

    def test_log_contains_timestamps_and_latency(self, sample_strategy, sample_exchange_quotes,
                                                  btc_option_data, tmp_path):
        import json
        scored = _make_scored(sample_strategy)
        plan = build_execution_plan(scored, "BTC", "deribit", sample_exchange_quotes, btc_option_data)
        plan.dry_run = True
        executor = DryRunExecutor(sample_exchange_quotes)
        report = execute_plan(plan, executor)

        log_path = str(tmp_path / "enhanced_log.json")
        save_execution_log(report, log_path)

        with open(log_path) as f:
            log = json.load(f)

        assert log["started_at"] != ""
        assert log["finished_at"] != ""
        assert isinstance(log["cancelled_orders"], list)
        assert log["fills"][0]["timestamp"] != ""
        assert log["fills"][0]["latency_ms"] >= 0


class TestPartialFillAutoCancel:
    """Test that execute_plan auto-cancels on partial failure."""

    def test_second_leg_failure_cancels_first(self, sample_exchange_quotes):
        """When second order fails, first filled order gets cancelled."""
        call_count = {"n": 0}

        class PartialExecutor(BaseExecutor):
            def authenticate(self): return True
            def place_order(self, order):
                call_count["n"] += 1
                if call_count["n"] == 1:
                    return OrderResult(
                        order_id="fill-1", status="filled", fill_price=100.0,
                        fill_quantity=1, instrument=order.instrument,
                        action=order.action, exchange="test",
                    )
                return OrderResult(
                    order_id="", status="error", fill_price=0.0,
                    fill_quantity=0, instrument=order.instrument,
                    action=order.action, exchange="test", error="rejected",
                )
            def get_order_status(self, order_id): return "filled"
            def cancel_order(self, order_id): return True

        plan = ExecutionPlan(
            strategy_description="Test spread", strategy_type="call_debit_spread",
            exchange="test", asset="BTC", expiry="",
            orders=[
                OrderRequest("BTC-67500-C", "BUY", 1, "limit", 655.0, "test", 0,
                             strike=67500, option_type="call"),
                OrderRequest("BTC-68000-C", "SELL", 1, "limit", 385.0, "test", 1,
                             strike=68000, option_type="call"),
            ],
        )
        report = execute_plan(plan, PartialExecutor())
        assert report.all_filled is False
        assert "Partial fill" in report.summary
        assert len(report.cancelled_orders) > 0
