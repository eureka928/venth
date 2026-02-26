"""Tests for Options GPS pipeline: fusion, strategies, payoff, ranking."""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pipeline import (
    run_forecast_fusion,
    generate_strategies,
    compute_payoff_metrics,
    strategy_pnl_values,
    rank_strategies,
    select_three_cards,
    should_no_trade,
    forecast_confidence,
    StrategyCandidate,
)

CURRENT = 67600.0
P1H_BULL = {"0.5": 67800, "0.05": 67400, "0.95": 68200}
P24H_BULL = {"0.5": 67900, "0.05": 67300, "0.95": 68500}
P1H_BEAR = {"0.5": 67400, "0.05": 67000, "0.95": 67800}
P24H_BEAR = {"0.5": 67300, "0.05": 66900, "0.95": 67700}
P1H_NEUTRAL = {"0.5": 67600, "0.05": 67400, "0.95": 67800}
P24H_NEUTRAL = {"0.5": 67620, "0.05": 67450, "0.95": 67800}


def test_fusion_aligned_bullish():
    state = run_forecast_fusion(P1H_BULL, P24H_BULL, CURRENT)
    assert state == "aligned_bullish"


def test_fusion_aligned_bearish():
    state = run_forecast_fusion(P1H_BEAR, P24H_BEAR, CURRENT)
    assert state == "aligned_bearish"


def test_fusion_countermove():
    state = run_forecast_fusion(P1H_BULL, P24H_BEAR, CURRENT)
    assert state == "countermove"


def test_fusion_unclear():
    state = run_forecast_fusion(P1H_NEUTRAL, P24H_NEUTRAL, CURRENT)
    assert state == "unclear"


def test_fusion_empty_returns_unclear():
    assert run_forecast_fusion({}, P24H_BULL, CURRENT) == "unclear"
    assert run_forecast_fusion(P1H_BULL, {}, CURRENT) == "unclear"


def test_generate_strategies_bullish():
    option_data = {
        "current_price": 67723,
        "call_options": {"67000": 1000, "67500": 640, "68000": 373, "68500": 197},
        "put_options": {"67000": 140, "67500": 291, "68000": 526},
    }
    candidates = generate_strategies(option_data, "bullish", "medium")
    assert len(candidates) >= 1
    types = [c.strategy_type for c in candidates]
    assert "long_call" in types or "call_debit_spread" in types
    assert "bull_put_credit_spread" in types


def test_generate_strategies_bearish():
    option_data = {
        "current_price": 67723,
        "call_options": {"66500": 1400, "67000": 987, "67500": 640, "68000": 373},
        "put_options": {"66500": 57, "67000": 140, "67500": 291, "68000": 526},
    }
    candidates = generate_strategies(option_data, "bearish", "medium")
    assert len(candidates) >= 1
    assert any(c.direction == "bearish" for c in candidates)
    assert any(c.strategy_type == "bear_call_credit_spread" for c in candidates)


def test_generate_strategies_neutral_has_butterfly():
    option_data = {
        "current_price": 67723,
        "call_options": {"66500": 1400, "67000": 987, "67500": 640, "68000": 373, "68500": 197},
        "put_options": {"66500": 57, "67000": 140, "67500": 291, "68000": 526, "68500": 850},
    }
    candidates = generate_strategies(option_data, "neutral", "medium")
    assert any(c.strategy_type == "long_call_butterfly" for c in candidates)


def test_compute_payoff_long_call():
    strat = StrategyCandidate("long_call", "bullish", "Long call", [68000], 400, 400)
    outcomes = [67000, 67500, 68000, 68500, 69000]
    pop, ev = compute_payoff_metrics(strat, outcomes)
    assert 0 <= pop <= 1
    assert ev == -100.0


def test_iron_condor_max_loss_uses_wider_wing():
    option_data = {
        "current_price": 100.0,
        "call_options": {"90": 15.0, "97": 9.0, "100": 7.0, "104": 5.0, "112": 3.0},
        "put_options": {"90": 3.0, "97": 6.0, "100": 8.0, "104": 12.0, "112": 18.0},
    }
    candidates = generate_strategies(option_data, "neutral", "medium")
    condor = next(c for c in candidates if c.strategy_type == "iron_condor")
    assert condor.max_loss == 3.0


def test_rank_and_select_three():
    strat1 = StrategyCandidate("long_call", "bullish", "A", [68000], 400, 400)
    strat2 = StrategyCandidate("long_put", "bearish", "B", [67000], 300, 300)
    strat3 = StrategyCandidate("call_debit_spread", "bullish", "C", [67500, 68500], 300, 300)
    outcomes = [67000, 67500, 68000, 68500, 69000]
    scored = rank_strategies([strat1, strat2, strat3], "aligned_bullish", "bullish", outcomes, "medium", 68000)
    assert len(scored) == 3
    best, safer, upside = select_three_cards(scored)
    assert best is not None
    assert best.strategy.direction == "bullish"
    assert safer is not None
    assert upside is not None
    assert safer is not upside


def test_should_no_trade_countermove_bullish():
    assert should_no_trade("countermove", "bullish", False) is True


def test_should_no_trade_unclear_neutral():
    assert should_no_trade("unclear", "neutral", False) is False


def test_should_no_trade_volatility_high():
    assert should_no_trade("aligned_bullish", "bullish", True) is True


def test_credit_spread_pnl_positive_inside_spread():
    strat = StrategyCandidate("bull_put_credit_spread", "bullish", "Bull put", [66000, 67000], -120, 880)
    pnl = strategy_pnl_values(strat, [67500, 67000, 66500, 66000])
    assert pnl[0] > 0
    assert pnl[-1] < 0


def test_confidence_narrow_spread():
    pct = {"0.05": 67000, "0.5": 67500, "0.95": 68000}
    conf = forecast_confidence(pct, 67500)
    assert conf > 0.7


def test_confidence_wide_spread():
    pct = {"0.05": 60000, "0.5": 67500, "0.95": 80000}
    conf = forecast_confidence(pct, 67500)
    assert conf < 0.3


def test_should_no_trade_low_confidence():
    assert should_no_trade("aligned_bullish", "bullish", False, confidence=0.1) is True


def test_should_no_trade_ok_confidence():
    assert should_no_trade("aligned_bullish", "bullish", False, confidence=0.8) is False
