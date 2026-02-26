"""
Options GPS decision pipeline: forecast fusion, strategy generation,
payoff/probability engine, ranking, and guardrails.
Uses Synth get_prediction_percentiles, get_option_pricing, get_volatility.
"""

from dataclasses import dataclass
from typing import Literal

ViewBias = Literal["bullish", "bearish", "neutral"]
RiskLevel = Literal["low", "medium", "high"]
FusionState = Literal["aligned_bullish", "aligned_bearish", "countermove", "unclear"]


@dataclass
class StrategyCandidate:
    strategy_type: str
    direction: Literal["bullish", "bearish", "neutral"]
    description: str
    strikes: list[float]
    cost: float
    max_loss: float


@dataclass
class ScoredStrategy:
    strategy: StrategyCandidate
    probability_of_profit: float
    expected_value: float
    tail_risk: float
    loss_profile: str
    invalidation_trigger: str
    reroute_rule: str
    review_again_at: str
    score: float
    rationale: str


def run_forecast_fusion(percentiles_1h: dict, percentiles_24h: dict, current_price: float) -> FusionState:
    """Classify market state from 1h and 24h forecast percentiles (last-step dict). Uses median vs current."""
    if not percentiles_1h or not percentiles_24h:
        return "unclear"
    p1h = percentiles_1h.get("0.5")
    p24h = percentiles_24h.get("0.5")
    if p1h is None or p24h is None:
        return "unclear"
    thresh = current_price * 0.002
    up_1h = p1h > current_price + thresh
    down_1h = p1h < current_price - thresh
    up_24h = p24h > current_price + thresh
    down_24h = p24h < current_price - thresh
    if up_1h and up_24h:
        return "aligned_bullish"
    if down_1h and down_24h:
        return "aligned_bearish"
    if (up_1h and down_24h) or (down_1h and up_24h):
        return "countermove"
    return "unclear"


def _parse_strikes(option_data: dict) -> list[float]:
    calls = option_data.get("call_options") or {}
    return sorted([float(k) for k in calls.keys()])


def generate_strategies(
    option_data: dict,
    view: ViewBias,
    risk: RiskLevel,
) -> list[StrategyCandidate]:
    """Build candidate strategies from option pricing and user view/risk."""
    current = float(option_data.get("current_price", 0))
    if current <= 0:
        return []
    strikes = _parse_strikes(option_data)
    if len(strikes) < 3:
        return []
    calls = {float(k): v for k, v in (option_data.get("call_options") or {}).items()}
    puts = {float(k): v for k, v in (option_data.get("put_options") or {}).items()}
    candidates: list[StrategyCandidate] = []
    atm = min(strikes, key=lambda s: abs(s - current))
    idx_atm = strikes.index(atm)
    otm_call = strikes[min(idx_atm + 2, len(strikes) - 1)] if idx_atm + 2 < len(strikes) else strikes[-1]
    otm_put = strikes[max(idx_atm - 2, 0)] if idx_atm >= 2 else strikes[0]
    if view == "bullish":
        if atm in calls:
            candidates.append(StrategyCandidate(
                "long_call", "bullish", "Long call (ATM)", [atm], float(calls[atm]), float(calls[atm])
            ))
        if otm_call in calls and otm_call != atm:
            candidates.append(StrategyCandidate(
                "long_call", "bullish", "Long call (OTM)", [otm_call], float(calls[otm_call]), float(calls[otm_call])
            ))
        if atm in calls and otm_call in calls:
            debit = float(calls[atm]) - float(calls[otm_call])
            if debit > 0:
                candidates.append(StrategyCandidate(
                    "call_debit_spread", "bullish", "Call debit spread", [atm, otm_call], debit, debit
                ))
        put_short = atm
        put_long = strikes[max(0, idx_atm - 1)]
        if put_short in puts and put_long in puts and put_short > put_long:
            credit = float(puts[put_short]) - float(puts[put_long])
            width = put_short - put_long
            if credit > 0:
                candidates.append(StrategyCandidate(
                    "bull_put_credit_spread", "bullish", "Bull put credit spread", [put_long, put_short], -credit, width - credit
                ))
    if view == "bearish":
        if atm in puts:
            candidates.append(StrategyCandidate(
                "long_put", "bearish", "Long put (ATM)", [atm], float(puts[atm]), float(puts[atm])
            ))
        if otm_put in puts and otm_put != atm:
            candidates.append(StrategyCandidate(
                "long_put", "bearish", "Long put (OTM)", [otm_put], float(puts[otm_put]), float(puts[otm_put])
            ))
        if atm in puts and otm_put in puts:
            debit = float(puts[atm]) - float(puts[otm_put])
            if debit > 0:
                candidates.append(StrategyCandidate(
                    "put_debit_spread", "bearish", "Put debit spread", [otm_put, atm], debit, debit
                ))
        call_short = atm
        call_long = strikes[min(len(strikes) - 1, idx_atm + 1)]
        if call_short in calls and call_long in calls and call_long > call_short:
            credit = float(calls[call_short]) - float(calls[call_long])
            width = call_long - call_short
            if credit > 0:
                candidates.append(StrategyCandidate(
                    "bear_call_credit_spread", "bearish", "Bear call credit spread", [call_short, call_long], -credit, width - credit
                ))
    if view == "neutral" or (view == "bullish" and risk == "low") or (view == "bearish" and risk == "low"):
        low_put = strikes[max(0, idx_atm - 3)]
        high_call = strikes[min(len(strikes) - 1, idx_atm + 3)]
        put_short = strikes[max(0, idx_atm - 1)]
        call_short = strikes[min(len(strikes) - 1, idx_atm + 1)]
        if low_put in puts and high_call in calls and put_short in puts and call_short in calls and low_put < current < high_call:
            credit_put = float(puts[put_short]) - float(puts[low_put])
            credit_call = float(calls[call_short]) - float(calls[high_call])
            credit = credit_put + credit_call
            if credit > 0:
                max_width = max(put_short - low_put, high_call - call_short)
                max_loss = max_width - credit
                candidates.append(StrategyCandidate(
                    "iron_condor", "neutral", "Iron condor (defined risk)", [put_short, call_short],
                    -credit, max_loss
                ))
    if view == "neutral":
        lower = strikes[max(0, idx_atm - 2)]
        upper = strikes[min(len(strikes) - 1, idx_atm + 2)]
        if lower in calls and atm in calls and upper in calls and lower < atm < upper:
            cost = float(calls[lower]) - 2 * float(calls[atm]) + float(calls[upper])
            if cost > 0:
                candidates.append(StrategyCandidate(
                    "long_call_butterfly", "neutral", "Long call butterfly", [lower, atm, upper], cost, cost
                ))
        if atm in calls:
            candidates.append(StrategyCandidate(
                "long_call", "bullish", "Long call (ATM)", [atm], float(calls[atm]), float(calls[atm])
            ))
        if atm in puts:
            candidates.append(StrategyCandidate(
                "long_put", "bearish", "Long put (ATM)", [atm], float(puts[atm]), float(puts[atm])
            ))
    if not candidates and view == "neutral":
        if atm in calls:
            candidates.append(StrategyCandidate("long_call", "bullish", "Long call (ATM)", [atm], float(calls[atm]), float(calls[atm])))
        if atm in puts:
            candidates.append(StrategyCandidate("long_put", "bearish", "Long put (ATM)", [atm], float(puts[atm]), float(puts[atm])))
    return candidates


def _outcome_prices(percentiles_last: dict) -> list[float]:
    """Ordered outcome prices from percentile dict (e.g. 0.05, 0.2, ..., 0.95)."""
    keys = ["0.05", "0.2", "0.35", "0.5", "0.65", "0.8", "0.95"]
    out = []
    for k in keys:
        if k in percentiles_last:
            out.append(float(percentiles_last[k]))
    return out if out else [float(percentiles_last.get("0.5", 0))]


def _payoff_long_call(s: float, strike: float) -> float:
    return max(0.0, s - strike)


def _payoff_long_put(s: float, strike: float) -> float:
    return max(0.0, strike - s)


def _payoff_call_spread(s: float, k1: float, k2: float) -> float:
    return max(0.0, min(s - k1, k2 - k1))


def _payoff_put_spread(s: float, k1: float, k2: float) -> float:
    return max(0.0, min(k2 - s, k2 - k1))


def strategy_pnl_values(strategy: StrategyCandidate, outcome_prices: list[float]) -> list[float]:
    """P/L values for each outcome price."""
    pnl_values: list[float] = []
    for s in outcome_prices:
        gross_payoff = 0.0
        if strategy.strategy_type == "long_call":
            gross_payoff = _payoff_long_call(s, strategy.strikes[0])
            pnl_values.append(gross_payoff - strategy.cost)
        elif strategy.strategy_type == "long_put":
            gross_payoff = _payoff_long_put(s, strategy.strikes[0])
            pnl_values.append(gross_payoff - strategy.cost)
        elif strategy.strategy_type == "call_debit_spread":
            gross_payoff = _payoff_call_spread(s, strategy.strikes[0], strategy.strikes[1])
            pnl_values.append(gross_payoff - strategy.cost)
        elif strategy.strategy_type == "put_debit_spread":
            gross_payoff = _payoff_put_spread(s, strategy.strikes[0], strategy.strikes[1])
            pnl_values.append(gross_payoff - strategy.cost)
        elif strategy.strategy_type == "bull_put_credit_spread":
            k_long, k_short = strategy.strikes[0], strategy.strikes[1]
            credit = -strategy.cost
            pnl_values.append(credit - max(0.0, k_short - s) + max(0.0, k_long - s))
        elif strategy.strategy_type == "bear_call_credit_spread":
            k_short, k_long = strategy.strikes[0], strategy.strikes[1]
            credit = -strategy.cost
            pnl_values.append(credit - max(0.0, s - k_short) + max(0.0, s - k_long))
        elif strategy.strategy_type == "iron_condor":
            k_put_short, k_call_short = strategy.strikes[0], strategy.strikes[1]
            p_put = max(0.0, k_put_short - s) if s < k_put_short else 0.0
            p_call = max(0.0, s - k_call_short) if s > k_call_short else 0.0
            credit = -strategy.cost
            pnl_values.append(credit - (p_put + p_call))
        elif strategy.strategy_type == "long_call_butterfly":
            k1, k2, k3 = strategy.strikes[0], strategy.strikes[1], strategy.strikes[2]
            gross_payoff = max(0.0, s - k1) - 2 * max(0.0, s - k2) + max(0.0, s - k3)
            pnl_values.append(gross_payoff - strategy.cost)
        else:
            pnl_values.append(0.0)
    return pnl_values


def _tail_risk_from_pnl(pnl_values: list[float]) -> float:
    """Expected loss in worst 20% scenarios (non-negative)."""
    if not pnl_values:
        return 0.0
    worst_n = max(1, len(pnl_values) // 5)
    worst = sorted(pnl_values)[:worst_n]
    avg_worst = sum(worst) / worst_n
    return max(0.0, -avg_worst)


def _loss_profile(strategy: StrategyCandidate) -> str:
    st = strategy.strategy_type
    if st in ("bull_put_credit_spread", "bear_call_credit_spread", "iron_condor", "call_debit_spread", "put_debit_spread", "long_call_butterfly"):
        return "defined risk"
    return "premium at risk"


def _risk_plan(strategy: StrategyCandidate) -> tuple[str, str, str]:
    st = strategy.strategy_type
    if st == "long_call":
        return (
            "Invalidate if underlying closes below entry zone and option loses ~50% premium.",
            "Convert to call spread or roll out in time if thesis remains.",
            "Recheck in 1h and at 24h close."
        )
    if st == "long_put":
        return (
            "Invalidate if underlying closes above entry zone and option loses ~50% premium.",
            "Convert to put spread or roll out in time if thesis remains.",
            "Recheck in 1h and at 24h close."
        )
    if st in ("call_debit_spread", "put_debit_spread"):
        return (
            "Invalidate if price moves through the short-strike side against thesis.",
            "Roll strikes one step toward current price if conviction persists.",
            "Recheck at 50% time-to-expiry."
        )
    if st in ("bull_put_credit_spread", "bear_call_credit_spread"):
        return (
            "Invalidate on short-strike breach with momentum against thesis.",
            "Close tested side or roll tested spread further OTM.",
            "Recheck each major price move (>1%)."
        )
    if st == "iron_condor":
        return (
            "Invalidate when either short strike is breached and trend continues.",
            "Close tested wing and keep untested wing; or roll whole condor out.",
            "Recheck every hour and at short-strike touch."
        )
    if st == "long_call_butterfly":
        return (
            "Invalidate if expected pin near center strike no longer plausible.",
            "Convert to directional debit spread toward observed drift.",
            "Recheck near midpoint and 25% time-to-expiry."
        )
    return (
        "Invalidate on thesis break.",
        "Use smaller risk structure if conviction remains.",
        "Recheck every 1h."
    )


def passes_hard_filters(strategy: StrategyCandidate, risk: RiskLevel, current_price: float) -> bool:
    """Guardrails for max loss and spread-quality quality checks."""
    if strategy.max_loss <= 0:
        return False
    max_loss_cap = {"low": 0.02, "medium": 0.04, "high": 0.08}[risk] * current_price
    if strategy.max_loss > max_loss_cap:
        return False
    st = strategy.strategy_type
    if st in ("call_debit_spread", "put_debit_spread"):
        width = abs(strategy.strikes[1] - strategy.strikes[0])
        max_debit_ratio = {"low": 0.80, "medium": 0.90, "high": 1.00}[risk]
        return strategy.cost <= width * max_debit_ratio
    if st in ("bull_put_credit_spread", "bear_call_credit_spread"):
        width = abs(strategy.strikes[1] - strategy.strikes[0])
        credit = -strategy.cost
        min_credit_ratio = {"low": 0.15, "medium": 0.10, "high": 0.05}[risk]
        return credit >= width * min_credit_ratio
    if st == "iron_condor":
        return (-strategy.cost) > 0
    if st == "long_call_butterfly":
        left = strategy.strikes[1] - strategy.strikes[0]
        right = strategy.strikes[2] - strategy.strikes[1]
        return left > 0 and right > 0 and strategy.cost <= max(left, right)
    return True


def compute_payoff_metrics(
    strategy: StrategyCandidate,
    outcome_prices: list[float],
) -> tuple[float, float]:
    """Return (probability_of_profit, expected_value) for strategy under outcome distribution."""
    n = len(outcome_prices)
    if n == 0:
        return 0.0, 0.0
    pnl_values = strategy_pnl_values(strategy, outcome_prices)
    ev = sum(pnl_values) / n
    pop = sum(1 for x in pnl_values if x > 0) / n
    return pop, ev


def rank_strategies(
    candidates: list[StrategyCandidate],
    fusion_state: FusionState,
    view: ViewBias,
    outcome_prices: list[float],
    risk: RiskLevel,
    current_price: float,
    confidence: float = 1.0,
) -> list[ScoredStrategy]:
    """Score and sort strategies. Returns list of ScoredStrategy sorted by score desc."""
    scored: list[ScoredStrategy] = []
    for c in candidates:
        if not passes_hard_filters(c, risk, current_price):
            continue
        pop, ev = compute_payoff_metrics(c, outcome_prices)
        pnl_values = strategy_pnl_values(c, outcome_prices)
        tail_risk = _tail_risk_from_pnl(pnl_values)
        view_match = 1.0 if c.direction == view else (0.4 if c.direction == "neutral" else 0.1)
        fusion_bonus = 0.0
        if fusion_state == "aligned_bullish" and c.direction == "bullish":
            fusion_bonus = 0.3
        elif fusion_state == "aligned_bearish" and c.direction == "bearish":
            fusion_bonus = 0.3
        elif fusion_state in ("countermove", "unclear") and c.direction == "neutral":
            fusion_bonus = 0.15
        fit = view_match + fusion_bonus
        w_pop = 0.4 if risk == "low" else (0.3 if risk == "medium" else 0.2)
        w_ev = 0.2 if risk == "low" else (0.3 if risk == "medium" else 0.4)
        score = fit * 0.4 + pop * w_pop + max(0, ev) * w_ev * 0.01
        tail_penalty = (1 - pop) * 0.1 + min(0.2, tail_risk * 0.0001)
        score -= tail_penalty
        score *= confidence
        invalidation, reroute, review_time = _risk_plan(c)
        rationale = f"Fit {fit:.0%}, PoP {pop:.0%}, EV ${ev:.0f}"
        scored.append(
            ScoredStrategy(
                strategy=c,
                probability_of_profit=pop,
                expected_value=ev,
                tail_risk=tail_risk,
                loss_profile=_loss_profile(c),
                invalidation_trigger=invalidation,
                reroute_rule=reroute,
                review_again_at=review_time,
                score=max(0, score),
                rationale=rationale,
            )
        )
    return sorted(scored, key=lambda x: -x.score)


def select_three_cards(scored: list[ScoredStrategy]) -> tuple[ScoredStrategy | None, ScoredStrategy | None, ScoredStrategy | None]:
    """Pick Best Match, Safer Alternative (higher PoP or lower max_loss), Higher Upside (higher EV)."""
    if not scored:
        return None, None, None
    best = scored[0]
    remaining = scored[1:]
    safer_candidates = [
        x for x in remaining
        if x.probability_of_profit > best.probability_of_profit
        or x.strategy.max_loss < best.strategy.max_loss
    ]
    if not safer_candidates:
        safer_candidates = remaining
    safer = max(safer_candidates, key=lambda x: x.probability_of_profit) if safer_candidates else None
    upside_candidates = [
        x for x in remaining
        if x is not safer and x.expected_value > best.expected_value
    ]
    if not upside_candidates:
        upside_candidates = [x for x in remaining if x is not safer]
    upside = max(upside_candidates, key=lambda x: x.expected_value) if upside_candidates else None
    return best, safer, upside


def forecast_confidence(percentiles_last: dict, current_price: float) -> float:
    """Confidence score 0-1 from percentile dispersion. Narrower spread = higher confidence."""
    p05 = percentiles_last.get("0.05")
    p95 = percentiles_last.get("0.95")
    if p05 is None or p95 is None or current_price <= 0:
        return 0.5
    spread = (float(p95) - float(p05)) / current_price
    if spread <= 0.02:
        return 1.0
    if spread >= 0.15:
        return 0.1
    return max(0.1, 1.0 - (spread - 0.02) / 0.13)


def should_no_trade(fusion_state: FusionState, view: ViewBias, volatility_high: bool, confidence: float = 1.0) -> bool:
    """Guardrail: no trade when confidence low or signals conflict."""
    if volatility_high:
        return True
    if confidence < 0.25:
        return True
    if fusion_state == "countermove" and view != "neutral":
        return True
    if fusion_state == "unclear" and view != "neutral":
        return True
    return False
