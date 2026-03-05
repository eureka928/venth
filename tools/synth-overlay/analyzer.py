"""
EdgeAnalyzer: consolidated dual-horizon edge analysis with confidence scoring
and human-readable signal explanations using Synth forecast percentiles.
"""

from dataclasses import dataclass
from typing import Literal

from edge import (
    compute_edge_pct,
    signal_from_edge,
    strength_from_edge,
    signals_conflict,
    strength_from_horizons,
)


@dataclass
class HorizonEdge:
    horizon: str
    edge_pct: float
    signal: str
    synth_prob: float
    market_prob: float


@dataclass
class AnalysisResult:
    primary: HorizonEdge
    secondary: HorizonEdge | None
    strength: Literal["strong", "moderate", "none"]
    confidence_score: float
    no_trade: bool
    explanation: str
    invalidation: str


class EdgeAnalyzer:
    """Analyzes Synth vs Polymarket across horizons with percentile-based confidence."""

    def __init__(
        self,
        daily_data: dict | None = None,
        hourly_data: dict | None = None,
        percentiles_1h: dict | None = None,
        percentiles_24h: dict | None = None,
    ):
        self._daily = daily_data
        self._hourly = hourly_data
        self._pct_1h = percentiles_1h
        self._pct_24h = percentiles_24h

    def _extract_edge(self, data: dict, horizon: str) -> HorizonEdge:
        synth = float(data["synth_probability_up"])
        market = float(data["polymarket_probability_up"])
        edge_pct = compute_edge_pct(synth, market)
        return HorizonEdge(
            horizon=horizon,
            edge_pct=edge_pct,
            signal=signal_from_edge(edge_pct),
            synth_prob=synth,
            market_prob=market,
        )

    def _percentile_spread(self, pct_data: dict | None) -> float | None:
        """Relative spread (p95 - p05) / price. Returns None if data unavailable."""
        if not pct_data:
            return None
        try:
            steps = pct_data.get("forecast_future", {}).get("percentiles") or []
            if not steps:
                return None
            last = steps[-1]
            price = pct_data.get("current_price") or 1.0
            if price <= 0:
                return None
            p95 = float(last.get("0.95", 0))
            p05 = float(last.get("0.05", 0))
            return abs(p95 - p05) / price
        except (TypeError, KeyError, ValueError):
            return None

    def _directional_bias(self, pct_data: dict | None) -> float | None:
        """How much median deviates from current price: (p50 - price) / price."""
        if not pct_data:
            return None
        try:
            steps = pct_data.get("forecast_future", {}).get("percentiles") or []
            if not steps:
                return None
            last = steps[-1]
            price = pct_data.get("current_price") or 1.0
            if price <= 0:
                return None
            p50 = float(last.get("0.5", 0))
            return (p50 - price) / price
        except (TypeError, KeyError, ValueError):
            return None

    def compute_confidence(self, spread_1h: float | None, spread_24h: float | None) -> float:
        """
        Confidence score [0.0, 1.0] inversely proportional to forecast spread.
        Narrow distributions = high confidence; wide = low.
        """
        spreads = [s for s in (spread_1h, spread_24h) if s is not None]
        if not spreads:
            return 0.5
        avg_spread = sum(spreads) / len(spreads)
        if avg_spread <= 0.01:
            return 1.0
        if avg_spread >= 0.10:
            return 0.1
        return round(1.0 - (avg_spread - 0.01) / 0.09 * 0.9, 2)

    def _build_explanation(self, edge_1h: HorizonEdge, edge_24h: HorizonEdge, confidence: float) -> str:
        direction_1h = "higher" if edge_1h.edge_pct > 0 else "lower"
        direction_24h = "higher" if edge_24h.edge_pct > 0 else "lower"
        parts = []
        if signals_conflict(edge_1h.signal, edge_24h.signal):
            parts.append(
                f"Synth forecasts {direction_1h} on the 1h horizon "
                f"but {direction_24h} on the 24h horizon — signals conflict."
            )
        elif edge_1h.signal == "fair" and edge_24h.signal == "fair":
            parts.append("Synth and Polymarket agree closely on both horizons.")
        else:
            dominant = "up" if edge_24h.edge_pct > 0 else "down"
            parts.append(
                f"Synth forecasts {dominant} probability {direction_24h} than "
                f"Polymarket on both horizons: 1h by {abs(edge_1h.edge_pct)}pp, "
                f"24h by {abs(edge_24h.edge_pct)}pp."
            )
        if confidence >= 0.7:
            parts.append("Forecast distribution is narrow — high confidence.")
        elif confidence <= 0.3:
            parts.append("Forecast distribution is wide — low confidence, treat with caution.")
        return " ".join(parts)

    def _build_invalidation(self, edge_24h: HorizonEdge, bias_24h: float | None) -> str:
        parts = []
        if edge_24h.signal == "underpriced":
            parts.append(
                "This edge invalidates if price drops sharply, "
                "pushing Synth probability below market."
            )
        elif edge_24h.signal == "overpriced":
            parts.append(
                "This edge invalidates if price rallies, "
                "pushing Synth probability above market."
            )
        else:
            parts.append("No meaningful edge to invalidate — market is fairly priced.")
        if bias_24h is not None and abs(bias_24h) > 0.02:
            direction = "upward" if bias_24h > 0 else "downward"
            parts.append(f"Synth median shows a {direction} bias of {abs(bias_24h)*100:.1f}%.")
        return " ".join(parts)

    def analyze_range(
        self,
        selected_bracket: dict,
        all_brackets: list[dict],
        percentiles_24h: dict | None = None,
    ) -> AnalysisResult:
        """Analyze a range market bracket with context from all brackets."""
        synth = float(selected_bracket.get("synth_probability", 0))
        market = float(selected_bracket.get("polymarket_probability", 0))
        edge_pct = compute_edge_pct(synth, market)
        signal = signal_from_edge(edge_pct)
        strength = strength_from_edge(edge_pct)
        title = selected_bracket.get("title", "")

        spread_24h = self._percentile_spread(percentiles_24h)
        confidence = self.compute_confidence(None, spread_24h)
        high_uncertainty = spread_24h is not None and spread_24h > 0.05
        no_trade = strength == "none" or high_uncertainty

        mispriced = [
            b for b in all_brackets
            if abs(float(b.get("synth_probability", 0)) - float(b.get("polymarket_probability", 0))) > 0.005
        ]
        explanation = self._build_range_explanation(
            title, edge_pct, signal, len(mispriced), len(all_brackets), confidence
        )
        invalidation = self._build_range_invalidation(
            selected_bracket, signal
        )

        primary = HorizonEdge(
            horizon="24h",
            edge_pct=edge_pct,
            signal=signal,
            synth_prob=synth,
            market_prob=market,
        )
        return AnalysisResult(
            primary=primary,
            secondary=None,
            strength=strength,
            confidence_score=confidence,
            no_trade=no_trade,
            explanation=explanation,
            invalidation=invalidation,
        )

    def _build_range_explanation(
        self,
        title: str,
        edge_pct: float,
        signal: str,
        mispriced_count: int,
        total_count: int,
        confidence: float,
    ) -> str:
        parts = []
        if signal == "fair":
            parts.append(
                f"Bracket {title}: Synth and Polymarket agree closely "
                f"(edge {edge_pct:+.1f}pp)."
            )
        else:
            direction = "higher" if edge_pct > 0 else "lower"
            parts.append(
                f"Bracket {title}: Synth assigns {direction} probability "
                f"than Polymarket by {abs(edge_pct):.1f}pp."
            )
        if mispriced_count > 1:
            parts.append(
                f"{mispriced_count} of {total_count} brackets show mispricing."
            )
        if confidence >= 0.7:
            parts.append("Forecast distribution is narrow — high confidence.")
        elif confidence <= 0.3:
            parts.append("Forecast distribution is wide — low confidence, treat with caution.")
        return " ".join(parts)

    def _build_range_invalidation(self, bracket: dict, signal: str) -> str:
        title = bracket.get("title", "")
        if signal == "underpriced":
            return (
                f"Edge on {title} invalidates if price moves away from this range, "
                f"reducing the probability of landing here."
            )
        if signal == "overpriced":
            return (
                f"Edge on {title} invalidates if price moves toward this range, "
                f"increasing the probability of landing here."
            )
        return f"No meaningful edge on {title} — bracket is fairly priced."

    def analyze_single_horizon(self, data: dict, horizon: str = "24h") -> AnalysisResult:
        """Analyze a single up/down market with optional reference-horizon context."""
        edge = self._extract_edge(data, horizon)
        strength = strength_from_edge(edge.edge_pct)

        ref_edge = None
        if self._hourly:
            try:
                ref_edge = self._extract_edge(self._hourly, "ref")
            except (KeyError, ValueError):
                pass

        if ref_edge and signals_conflict(edge.signal, ref_edge.signal):
            strength = "none"

        spread_1h = self._percentile_spread(self._pct_1h)
        spread_24h = self._percentile_spread(self._pct_24h)
        confidence = self.compute_confidence(spread_1h, spread_24h)

        high_uncertainty = any(
            s is not None and s > 0.05 for s in (spread_1h, spread_24h)
        )
        conflict = ref_edge is not None and signals_conflict(edge.signal, ref_edge.signal)
        no_trade = conflict or strength == "none" or high_uncertainty

        bias = self._directional_bias(self._pct_24h) or self._directional_bias(self._pct_1h)
        explanation = self._build_single_explanation(edge, ref_edge, confidence, horizon)
        invalidation = self._build_short_invalidation(edge, bias, horizon)

        return AnalysisResult(
            primary=edge,
            secondary=ref_edge,
            strength=strength,
            confidence_score=confidence,
            no_trade=no_trade,
            explanation=explanation,
            invalidation=invalidation,
        )

    def _build_single_explanation(
        self, edge: HorizonEdge, ref_edge: HorizonEdge | None, confidence: float, horizon: str
    ) -> str:
        """Build explanation for single-horizon analysis with optional reference context."""
        parts = []
        if edge.signal == "fair":
            parts.append(f"Synth and Polymarket agree closely on this {horizon} market.")
        else:
            direction = "higher" if edge.edge_pct > 0 else "lower"
            parts.append(
                f"Synth forecasts {direction} probability than Polymarket "
                f"by {abs(edge.edge_pct):.1f}pp on the {horizon} horizon."
            )
        if ref_edge and ref_edge.signal != "fair":
            if signals_conflict(edge.signal, ref_edge.signal):
                parts.append("Reference horizon disagrees — signals conflict.")
            else:
                parts.append(
                    f"Reference horizon confirms with {abs(ref_edge.edge_pct):.1f}pp edge."
                )
        if confidence >= 0.7:
            parts.append("Forecast distribution is narrow — high confidence.")
        elif confidence <= 0.3:
            parts.append("Forecast distribution is wide — low confidence, treat with caution.")
        return " ".join(parts)

    def _build_short_invalidation(self, edge: HorizonEdge, bias: float | None, horizon: str) -> str:
        """Build invalidation text appropriate for short-term markets."""
        parts = []
        if edge.signal == "underpriced":
            parts.append(
                f"This {horizon} edge invalidates if price reverses, "
                f"pushing Synth probability below market."
            )
        elif edge.signal == "overpriced":
            parts.append(
                f"This {horizon} edge invalidates if price moves up, "
                f"pushing Synth probability above market."
            )
        else:
            parts.append("No meaningful edge to invalidate — market is fairly priced.")
        if bias is not None and abs(bias) > 0.02:
            direction = "upward" if bias > 0 else "downward"
            parts.append(f"Synth median shows a {direction} bias of {abs(bias)*100:.1f}%.")
        return " ".join(parts)

    def analyze(self, primary_horizon: str = "24h") -> AnalysisResult:
        if not self._daily or not self._hourly:
            raise ValueError("Both daily and hourly data required for analysis")

        edge_24h = self._extract_edge(self._daily, "24h")
        edge_1h = self._extract_edge(self._hourly, "1h")

        strength = strength_from_horizons(edge_1h.edge_pct, edge_24h.edge_pct)
        conflict = signals_conflict(edge_1h.signal, edge_24h.signal)

        spread_1h = self._percentile_spread(self._pct_1h)
        spread_24h = self._percentile_spread(self._pct_24h)
        confidence = self.compute_confidence(spread_1h, spread_24h)

        high_uncertainty = any(
            s is not None and s > 0.05 for s in (spread_1h, spread_24h)
        )
        no_trade = conflict or strength == "none" or high_uncertainty

        bias_24h = self._directional_bias(self._pct_24h)

        explanation = self._build_explanation(edge_1h, edge_24h, confidence)
        invalidation = self._build_invalidation(edge_24h, bias_24h)

        primary = edge_24h if primary_horizon == "24h" else edge_1h
        secondary = edge_1h if primary_horizon == "24h" else edge_24h

        return AnalysisResult(
            primary=primary,
            secondary=secondary,
            strength=strength,
            confidence_score=confidence,
            no_trade=no_trade,
            explanation=explanation,
            invalidation=invalidation,
        )
