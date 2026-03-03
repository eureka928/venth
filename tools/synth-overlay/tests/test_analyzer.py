"""Tests for EdgeAnalyzer: dual-horizon analysis, confidence, and explanations."""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from analyzer import EdgeAnalyzer, AnalysisResult, HorizonEdge


def _daily(synth_up, market_up):
    return {"synth_probability_up": synth_up, "polymarket_probability_up": market_up}


def _pct(price, p05, p50, p95):
    return {
        "current_price": price,
        "forecast_future": {
            "percentiles": [{"0.05": p05, "0.5": p50, "0.95": p95}],
        },
    }


class TestEdgeAnalyzer:
    def test_analyze_returns_analysis_result(self):
        a = EdgeAnalyzer(_daily(0.50, 0.40), _daily(0.45, 0.40))
        r = a.analyze()
        assert isinstance(r, AnalysisResult)
        assert isinstance(r.primary, HorizonEdge)
        assert isinstance(r.secondary, HorizonEdge)

    def test_analyze_primary_horizon_daily(self):
        daily = _daily(0.50, 0.40)
        hourly = _daily(0.45, 0.40)
        r = EdgeAnalyzer(daily, hourly).analyze(primary_horizon="24h")
        assert r.primary.horizon == "24h"
        assert r.secondary.horizon == "1h"

    def test_analyze_primary_horizon_hourly(self):
        daily = _daily(0.50, 0.40)
        hourly = _daily(0.45, 0.40)
        r = EdgeAnalyzer(daily, hourly).analyze(primary_horizon="1h")
        assert r.primary.horizon == "1h"
        assert r.secondary.horizon == "24h"

    def test_aligned_strong_edge(self):
        daily = _daily(0.60, 0.50)
        hourly = _daily(0.58, 0.50)
        r = EdgeAnalyzer(daily, hourly).analyze()
        assert r.strength == "strong"
        assert r.primary.signal == "underpriced"
        assert not r.no_trade

    def test_conflicting_signals_no_trade(self):
        daily = _daily(0.55, 0.50)
        hourly = _daily(0.45, 0.50)
        r = EdgeAnalyzer(daily, hourly).analyze()
        assert r.strength == "none"
        assert r.no_trade is True
        assert "conflict" in r.explanation.lower()

    def test_fair_on_both_horizons(self):
        daily = _daily(0.50, 0.50)
        hourly = _daily(0.50, 0.50)
        r = EdgeAnalyzer(daily, hourly).analyze()
        assert r.primary.signal == "fair"
        assert "agree" in r.explanation.lower()

    def test_missing_data_raises(self):
        with pytest.raises(ValueError):
            EdgeAnalyzer(None, None).analyze()

    def test_no_trade_on_high_uncertainty(self):
        daily = _daily(0.55, 0.50)
        hourly = _daily(0.54, 0.50)
        pct_wide = _pct(100, 80, 100, 120)
        r = EdgeAnalyzer(daily, hourly, pct_wide, pct_wide).analyze()
        assert r.no_trade is True

    def test_confidence_high_with_narrow_spread(self):
        pct_narrow = _pct(100, 99.5, 100, 100.5)
        a = EdgeAnalyzer(_daily(0.55, 0.50), _daily(0.54, 0.50), pct_narrow, pct_narrow)
        r = a.analyze()
        assert r.confidence_score >= 0.7

    def test_confidence_low_with_wide_spread(self):
        pct_wide = _pct(100, 85, 100, 115)
        a = EdgeAnalyzer(_daily(0.55, 0.50), _daily(0.54, 0.50), pct_wide, pct_wide)
        r = a.analyze()
        assert r.confidence_score <= 0.3


class TestConfidenceScoring:
    def test_no_percentiles_returns_default(self):
        a = EdgeAnalyzer(_daily(0.5, 0.4), _daily(0.5, 0.4))
        assert a.compute_confidence(None, None) == 0.5

    def test_very_narrow_returns_one(self):
        a = EdgeAnalyzer(_daily(0.5, 0.4), _daily(0.5, 0.4))
        assert a.compute_confidence(0.005, 0.008) == 1.0

    def test_very_wide_returns_low(self):
        a = EdgeAnalyzer(_daily(0.5, 0.4), _daily(0.5, 0.4))
        assert a.compute_confidence(0.15, 0.12) == 0.1

    def test_moderate_spread(self):
        a = EdgeAnalyzer(_daily(0.5, 0.4), _daily(0.5, 0.4))
        score = a.compute_confidence(0.03, 0.04)
        assert 0.3 < score < 0.9


class TestExplanations:
    def test_explanation_contains_direction(self):
        r = EdgeAnalyzer(_daily(0.55, 0.50), _daily(0.54, 0.50)).analyze()
        assert "higher" in r.explanation

    def test_invalidation_for_underpriced(self):
        r = EdgeAnalyzer(_daily(0.60, 0.50), _daily(0.58, 0.50)).analyze()
        assert "drops" in r.invalidation.lower() or "invalidat" in r.invalidation.lower()

    def test_invalidation_for_overpriced(self):
        r = EdgeAnalyzer(_daily(0.40, 0.50), _daily(0.42, 0.50)).analyze()
        assert "rall" in r.invalidation.lower() or "invalidat" in r.invalidation.lower()

    def test_invalidation_for_fair(self):
        r = EdgeAnalyzer(_daily(0.50, 0.50), _daily(0.50, 0.50)).analyze()
        assert "no meaningful edge" in r.invalidation.lower()

    def test_bias_mentioned_when_significant(self):
        pct = _pct(100, 98, 105, 112)
        r = EdgeAnalyzer(_daily(0.55, 0.50), _daily(0.54, 0.50), pct, pct).analyze()
        assert "bias" in r.invalidation.lower()


def _bracket(title, synth_prob, market_prob):
    return {
        "slug": "bitcoin-price-on-february-26",
        "title": title,
        "synth_probability": synth_prob,
        "polymarket_probability": market_prob,
        "current_time": "2026-02-25T23:45:00+00:00",
    }


class TestRangeAnalysis:
    def test_analyze_range_returns_result(self):
        sel = _bracket("[66000, 68000]", 0.38, 0.40)
        r = EdgeAnalyzer().analyze_range(sel, [sel])
        assert isinstance(r, AnalysisResult)
        assert r.primary.horizon == "24h"
        assert r.secondary is None

    def test_range_underpriced(self):
        sel = _bracket("[68000, 70000]", 0.34, 0.32)
        r = EdgeAnalyzer().analyze_range(sel, [sel])
        assert r.primary.signal == "underpriced"
        assert "higher" in r.explanation.lower()

    def test_range_overpriced(self):
        sel = _bracket("[66000, 68000]", 0.35, 0.40)
        r = EdgeAnalyzer().analyze_range(sel, [sel])
        assert r.primary.signal == "overpriced"
        assert "lower" in r.explanation.lower()

    def test_range_fair(self):
        sel = _bracket("[66000, 68000]", 0.40, 0.40)
        r = EdgeAnalyzer().analyze_range(sel, [sel])
        assert r.primary.signal == "fair"
        assert "agree" in r.explanation.lower()

    def test_range_has_explanation_and_invalidation(self):
        sel = _bracket("[68000, 70000]", 0.34, 0.32)
        r = EdgeAnalyzer().analyze_range(sel, [sel])
        assert len(r.explanation) > 10
        assert len(r.invalidation) > 10

    def test_range_confidence_with_percentiles(self):
        sel = _bracket("[66000, 68000]", 0.38, 0.40)
        pct = _pct(67000, 66500, 67000, 67500)
        r = EdgeAnalyzer().analyze_range(sel, [sel], pct)
        assert r.confidence_score >= 0.7

    def test_range_no_trade_on_weak_edge(self):
        sel = _bracket("[66000, 68000]", 0.40, 0.40)
        r = EdgeAnalyzer().analyze_range(sel, [sel])
        assert r.no_trade is True


class TestSingleHorizonAnalysis:
    def test_basic_single_horizon(self):
        data = _daily(0.55, 0.50)
        r = EdgeAnalyzer(data, None).analyze_single_horizon(data, horizon="15min")
        assert isinstance(r, AnalysisResult)
        assert r.primary.horizon == "15min"
        assert r.primary.edge_pct == 5.0
        assert r.primary.signal == "underpriced"

    def test_single_horizon_with_reference(self):
        primary = _daily(0.55, 0.50)
        ref = _daily(0.54, 0.50)
        r = EdgeAnalyzer(primary, ref).analyze_single_horizon(primary, horizon="5min")
        assert r.secondary is not None
        assert "confirms" in r.explanation.lower() or "higher" in r.explanation.lower()

    def test_single_horizon_conflict_with_reference(self):
        primary = _daily(0.55, 0.50)
        ref = _daily(0.44, 0.50)
        r = EdgeAnalyzer(primary, ref).analyze_single_horizon(primary, horizon="15min")
        assert r.no_trade is True
        assert r.strength == "none"
        assert "conflict" in r.explanation.lower()

    def test_single_horizon_no_reference(self):
        data = _daily(0.50, 0.50)
        r = EdgeAnalyzer(data, None).analyze_single_horizon(data, horizon="5min")
        assert r.secondary is None
        assert r.primary.signal == "fair"
        assert "agree" in r.explanation.lower()

    def test_single_horizon_invalidation_uses_horizon(self):
        data = _daily(0.55, 0.50)
        r = EdgeAnalyzer(data, None).analyze_single_horizon(data, horizon="5min")
        assert "5min" in r.invalidation

    def test_single_horizon_confidence_with_percentiles(self):
        data = _daily(0.55, 0.50)
        pct_narrow = _pct(100, 99.5, 100, 100.5)
        r = EdgeAnalyzer(data, None, pct_narrow, pct_narrow).analyze_single_horizon(data)
        assert r.confidence_score >= 0.7
