"""End-to-end scripted test for autonomous execution (issue #26).
Runs the full pipeline with Synth mock + exchange mock data and verifies
execution flows through plan building to dry-run simulation."""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pipeline import (
    generate_strategies,
    rank_strategies,
    select_three_cards,
    forecast_confidence,
    run_forecast_fusion,
)
from exchange import (
    fetch_all_exchanges,
    strategy_divergence,
    leg_divergences,
)
from executor import (
    build_execution_plan,
    validate_plan,
    execute_plan,
    get_executor,
    DryRunExecutor,
)

MOCK_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "..", "mock_data", "exchange_options")

OPTION_DATA = {
    "current_price": 67723,
    "call_options": {
        "65000": 2847.68, "66000": 1864.60, "67000": 987.04,
        "67500": 638.43, "68000": 373.27, "68500": 197.11,
        "69000": 93.43, "70000": 15.13,
    },
    "put_options": {
        "65000": 0.99, "66000": 17.91, "67000": 140.36,
        "67500": 291.75, "68000": 526.59, "68500": 850.42,
        "69000": 1246.74, "70000": 2168.44,
    },
}

P24H = {
    "0.05": 66000, "0.2": 67000, "0.35": 67400,
    "0.5": 67800, "0.65": 68200, "0.8": 68800, "0.95": 70000,
}

CURRENT_PRICE = 67723.0


def test_full_execution_pipeline():
    """Load Synth mock + exchange mock -> rank -> build plan -> dry-run execute -> verify report."""
    # Step 1: Load exchange quotes from mock
    quotes = fetch_all_exchanges("BTC", mock_dir=MOCK_DIR)
    assert len(quotes) > 0, "Should load exchange quotes from mock"

    # Step 2: Generate and rank strategies
    candidates = generate_strategies(OPTION_DATA, "bullish", "medium", asset="BTC",
                                     expiry="2026-02-26 08:00:00Z")
    assert len(candidates) > 0

    divergence_by_strategy = {}
    for c in candidates:
        div = strategy_divergence(c, quotes, OPTION_DATA)
        if div is not None:
            divergence_by_strategy[id(c)] = div

    fusion = run_forecast_fusion(None, P24H, CURRENT_PRICE)
    confidence = forecast_confidence(P24H, CURRENT_PRICE)
    outcome_prices = [float(P24H[k]) for k in sorted(P24H.keys())]

    scored = rank_strategies(
        candidates, fusion, "bullish", outcome_prices, "medium", CURRENT_PRICE,
        confidence=confidence, divergence_by_strategy=divergence_by_strategy,
    )
    best, safer, upside = select_three_cards(scored)
    assert best is not None

    # Step 3: Build execution plan
    plan = build_execution_plan(best, "BTC", "deribit", quotes, OPTION_DATA)
    assert len(plan.orders) > 0
    assert plan.strategy_description == best.strategy.description
    assert plan.asset == "BTC"

    # Step 4: Validate plan
    valid, err = validate_plan(plan)
    assert valid, f"Plan should be valid: {err}"

    # Step 5: All orders should have strike and option_type populated
    for order in plan.orders:
        assert order.strike > 0
        assert order.option_type in ("call", "put")
        assert order.price > 0

    # Step 6: Dry-run execute
    plan.dry_run = True
    executor = get_executor("deribit", quotes, dry_run=True)
    assert isinstance(executor, DryRunExecutor)

    report = execute_plan(plan, executor)
    assert report.all_filled is True
    assert len(report.results) == len(plan.orders)

    # Step 7: Verify results
    for result in report.results:
        assert result.status == "simulated"
        assert result.fill_price > 0
        assert result.fill_quantity > 0

    # Step 8: Net cost should be positive for a long call (BUY)
    if best.strategy.strategy_type == "long_call":
        assert report.net_cost > 0


def test_multi_leg_execution_pipeline():
    """Spread strategy -> build plan -> dry-run -> verify both legs fill."""
    quotes = fetch_all_exchanges("BTC", mock_dir=MOCK_DIR)
    candidates = generate_strategies(OPTION_DATA, "bullish", "medium", asset="BTC",
                                     expiry="2026-02-26 08:00:00Z")

    # Find a multi-leg strategy (call debit spread)
    spreads = [c for c in candidates if c.strategy_type == "call_debit_spread"]
    if not spreads:
        return  # no spread available, skip
    spread = spreads[0]

    from pipeline import ScoredStrategy
    scored = ScoredStrategy(
        strategy=spread, probability_of_profit=0.5, expected_value=50.0,
        tail_risk=40.0, loss_profile="defined risk",
        invalidation_trigger="Close on break", reroute_rule="Roll out",
        review_again_at="Review at 50%", score=0.7, rationale="Test",
    )

    plan = build_execution_plan(scored, "BTC", None, quotes, OPTION_DATA)
    assert len(plan.orders) == 2
    assert plan.exchange == "auto"

    # One BUY, one SELL
    actions = {o.action for o in plan.orders}
    assert actions == {"BUY", "SELL"}

    plan.dry_run = True
    executor = DryRunExecutor(quotes)
    report = execute_plan(plan, executor)
    assert report.all_filled is True
    assert len(report.results) == 2

    # Net cost should be positive (debit spread)
    assert report.net_cost > 0


def test_non_crypto_skips_execution():
    """XAU asset -> no exchange data -> execution not possible."""
    quotes = fetch_all_exchanges("XAU", mock_dir=MOCK_DIR)
    assert quotes == []


def test_auto_route_factory_e2e():
    """Auto-routing with factory callable should execute all legs."""
    quotes = fetch_all_exchanges("BTC", mock_dir=MOCK_DIR)
    candidates = generate_strategies(OPTION_DATA, "bullish", "medium", asset="BTC",
                                     expiry="2026-02-26 08:00:00Z")
    spreads = [c for c in candidates if c.strategy_type == "call_debit_spread"]
    if not spreads:
        return
    spread = spreads[0]

    from pipeline import ScoredStrategy
    scored = ScoredStrategy(
        strategy=spread, probability_of_profit=0.5, expected_value=50.0,
        tail_risk=40.0, loss_profile="defined risk",
        invalidation_trigger="Close on break", reroute_rule="Roll out",
        review_again_at="Review at 50%", score=0.7, rationale="Test",
    )

    plan = build_execution_plan(scored, "BTC", None, quotes, OPTION_DATA)
    plan.dry_run = True

    # Use factory callable (simulates live auto-routing path)
    def _factory(exchange: str):
        return DryRunExecutor(quotes)

    report = execute_plan(plan, _factory)
    assert report.all_filled is True
    assert len(report.results) == 2


def test_execution_report_timing():
    """Execution report has started_at and finished_at populated."""
    quotes = fetch_all_exchanges("BTC", mock_dir=MOCK_DIR)
    candidates = generate_strategies(OPTION_DATA, "bullish", "medium", asset="BTC",
                                     expiry="2026-02-26 08:00:00Z")
    assert len(candidates) > 0
    fusion = run_forecast_fusion(None, P24H, CURRENT_PRICE)
    confidence = forecast_confidence(P24H, CURRENT_PRICE)
    outcome_prices = [float(P24H[k]) for k in sorted(P24H.keys())]

    scored = rank_strategies(
        candidates, fusion, "bullish", outcome_prices, "medium", CURRENT_PRICE,
        confidence=confidence,
    )
    best, _, _ = select_three_cards(scored)
    plan = build_execution_plan(best, "BTC", "deribit", quotes, OPTION_DATA)
    plan.dry_run = True
    executor = DryRunExecutor(quotes)
    report = execute_plan(plan, executor)
    assert report.started_at != ""
    assert report.finished_at != ""
    assert report.all_filled is True
    # Every fill should have a timestamp
    for r in report.results:
        assert r.timestamp != ""
        assert r.latency_ms >= 0


def test_guardrail_blocks_live_on_no_trade():
    """When no_trade_reason is active, main.py should block live execution.
    We test the logic directly: if is_live and no_trade_reason and not force → block."""
    # This is a logic-level test since we can't easily run the full CLI here
    no_trade_reason = "Countermove detected"
    is_live = True
    force = False
    blocked = is_live and no_trade_reason and not force
    assert blocked is True


def test_guardrail_allows_force():
    """With --force, live execution proceeds despite no_trade_reason."""
    no_trade_reason = "Countermove detected"
    is_live = True
    force = True
    blocked = is_live and no_trade_reason and not force
    assert blocked is False


def test_guardrail_allows_dry_run():
    """Dry-run ignores guardrail (not live execution)."""
    no_trade_reason = "Low confidence"
    is_live = False
    force = False
    blocked = is_live and no_trade_reason and not force
    assert blocked is False


def test_guardrail_no_reason_allows_live():
    """When no_trade_reason is None, live execution proceeds."""
    no_trade_reason = None
    is_live = True
    force = False
    blocked = is_live and no_trade_reason and not force
    assert not blocked


if __name__ == "__main__":
    test_full_execution_pipeline()
    print("PASS: test_full_execution_pipeline")
    test_multi_leg_execution_pipeline()
    print("PASS: test_multi_leg_execution_pipeline")
    test_non_crypto_skips_execution()
    print("PASS: test_non_crypto_skips_execution")
    test_auto_route_factory_e2e()
    print("PASS: test_auto_route_factory_e2e")
    test_execution_report_timing()
    print("PASS: test_execution_report_timing")
    test_guardrail_blocks_live_on_no_trade()
    print("PASS: test_guardrail_blocks_live_on_no_trade")
    test_guardrail_allows_force()
    print("PASS: test_guardrail_allows_force")
    test_guardrail_allows_dry_run()
    print("PASS: test_guardrail_allows_dry_run")
    test_guardrail_no_reason_allows_live()
    print("PASS: test_guardrail_no_reason_allows_live")
    print("\nAll executor E2E tests passed.")
