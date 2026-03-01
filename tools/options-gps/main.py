"""
Options GPS: Turn a trader's view into one clear options decision.
Uses Synth get_prediction_percentiles, get_option_pricing, get_volatility.
"""

import json
import sys
import os
import warnings

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from synth_client import SynthClient

from pipeline import (
    run_forecast_fusion,
    generate_strategies,
    rank_strategies,
    select_three_cards,
    should_no_trade,
    forecast_confidence,
    is_volatility_elevated,
)
from pipeline import _outcome_prices, strategy_pnl_values

SUPPORTED_ASSETS = ["BTC", "ETH", "SOL", "XAU", "SPY", "NVDA", "TSLA", "AAPL", "GOOGL"]


def load_synth_data(client: SynthClient, asset: str) -> tuple[dict | None, dict, dict, dict, float] | None:
    """Fetch percentiles 1h/24h, option pricing, volatility.
    Returns (p1h_last_or_None, p24h_last, options, vol, current_price) or None.
    1h data is optional -- if missing, p1h is returned as None."""
    try:
        p24h = client.get_prediction_percentiles(asset, horizon="24h")
        options = client.get_option_pricing(asset)
        vol = client.get_volatility(asset, horizon="24h")
    except Exception:
        return None
    percentiles_list_24h = (p24h.get("forecast_future") or {}).get("percentiles") or []
    if not percentiles_list_24h:
        return None
    current = float((options.get("current_price") or p24h.get("current_price") or 0))
    if current <= 0:
        return None
    p1h_last = None
    try:
        p1h = client.get_prediction_percentiles(asset, horizon="1h")
        percentiles_list_1h = (p1h.get("forecast_future") or {}).get("percentiles") or []
        if percentiles_list_1h:
            p1h_last = percentiles_list_1h[-1]
    except Exception:
        pass
    return (
        p1h_last,
        percentiles_list_24h[-1],
        options,
        vol,
        current,
    )


BAR = "\u2502"
SEP = "\u2500"


def _header(title: str, width: int = 60) -> str:
    pad = max(0, width - len(title) - 4)
    return f"\n\u250c\u2500\u2500 {title} {SEP * pad}\u2510"


def _footer(width: int = 60) -> str:
    return f"\u2514{SEP * width}\u2518"


def _confidence_bar(confidence: float, width: int = 20) -> str:
    filled = int(confidence * width)
    empty = width - filled
    if confidence >= 0.6:
        label = "HIGH"
    elif confidence >= 0.35:
        label = "MED"
    else:
        label = "LOW"
    return f"[{'#' * filled}{'.' * empty}] {confidence:.0%} {label}"


def screen_view_setup() -> tuple[str, str, str]:
    """Screen 1: symbol, view, risk. Returns (symbol, view, risk)."""
    print(_header("Screen 1: View Setup"))
    print(f"{BAR} Assets: {', '.join(SUPPORTED_ASSETS)}")
    symbol = input(f"{BAR} Enter symbol [BTC]: ").strip().upper() or "BTC"
    if symbol not in SUPPORTED_ASSETS:
        symbol = "BTC"
    print(f"{BAR} Market view: bullish | bearish | neutral")
    view = input(f"{BAR} Enter view [bullish]: ").strip().lower() or "bullish"
    if view not in ("bullish", "bearish", "neutral"):
        view = "bullish"
    print(f"{BAR} Risk tolerance: low | medium | high")
    risk = input(f"{BAR} Enter risk [medium]: ").strip().lower() or "medium"
    if risk not in ("low", "medium", "high"):
        risk = "medium"
    level = "moderately " if view != "neutral" else ""
    print(f"{BAR}")
    print(f"{BAR} >> You are {level}{view} with {risk} risk.")
    print(_footer())
    return symbol, view, risk


def _print_strategy_card(label: str, card, icon: str, current_price: float = 0):
    s = card.strategy
    ev_pct = (card.expected_value / current_price * 100) if current_price > 0 else 0.0
    print(f"{BAR}")
    print(f"{BAR}  {icon} {label}: {s.description}")
    print(f"{BAR}    Rationale     : {card.rationale}")
    print(f"{BAR}    Profit chance  : {card.probability_of_profit:.0%}")
    print(f"{BAR}    Expected value : ${card.expected_value:,.0f} ({ev_pct:+.2f}% of price)")
    print(f"{BAR}    Tail risk      : ${card.tail_risk:,.0f} (worst 20% avg loss)")
    print(f"{BAR}    Risk profile   : {card.loss_profile}  |  Max loss: ${s.max_loss:,.0f}")
    print(f"{BAR}    Invalidation   : {card.invalidation_trigger}")
    print(f"{BAR}    Review at      : {card.review_again_at}")


def screen_top_plays(best, safer, upside, no_trade_reason: str | None, confidence: float = 0.0, current_price: float = 0):
    """Screen 2: Three strategy cards, or best tentative recommendation if no-trade."""
    print(_header("Screen 2: Top Plays"))
    if no_trade_reason:
        print(f"{BAR}  ** NO TRADE RECOMMENDED **")
        print(f"{BAR}  Reason: {no_trade_reason}")
        print(f"{BAR}  Confidence: {_confidence_bar(confidence)}")
        if best is not None:
            print(f"{BAR}")
            print(f"{BAR}  Tentative best pick (use with extreme caution):")
            _print_strategy_card("Tentative", best, "~", current_price)
        print(_footer())
        return
    icons = ["*", "#", "^"]
    for (label, card), icon in zip([("Best Match", best), ("Safer Alternative", safer), ("Higher Upside", upside)], icons):
        if card is None:
            continue
        _print_strategy_card(label, card, icon, current_price)
    print(_footer())


def _payoff_ascii(prices: list[float], pnl: list[float]) -> list[str]:
    if not prices or not pnl:
        return []
    max_abs = max(abs(x) for x in pnl) or 1.0
    lines = []
    for i, price in enumerate(prices):
        v = pnl[i]
        size = int((abs(v) / max_abs) * 15)
        if v >= 0:
            bar = "\u2588" * size
            sign = "+"
        else:
            bar = "\u2591" * size
            sign = "-"
        lines.append(f"{BAR}    ${price:>10,.0f} {bar:<16s} {sign}${abs(v):,.0f}")
    return lines


def screen_why_this_works(best, fusion_state: str, current_price: float, no_trade_reason: str | None, outcome_prices: list[float], p24h_last: dict | None = None, p1h_available: bool = True):
    """Screen 3: Why best match works — distribution view and explanation."""
    print(_header("Screen 3: Why This Works"))
    if best is None:
        print(f"{BAR}  No recommendation; see guardrails.")
        print(_footer())
        return
    s = best.strategy
    fusion_label = fusion_state.replace('_', ' ').title()
    data_note = "1h + 24h" if p1h_available else "24h only (1h unavailable)"
    print(f"{BAR}  Strategy     : {s.description}")
    print(f"{BAR}  Fusion state : {fusion_label} ({data_note})")
    print(f"{BAR}  Current price: ${current_price:,.2f}")
    if p24h_last:
        p05 = p24h_last.get("0.05")
        p50 = p24h_last.get("0.5")
        p95 = p24h_last.get("0.95")
        if p05 is not None and p50 is not None and p95 is not None:
            lo_pct = (float(p05) - current_price) / current_price * 100
            hi_pct = (float(p95) - current_price) / current_price * 100
            print(f"{BAR}  24h range    : ${float(p05):,.0f} ({lo_pct:+.1f}%) / ${float(p50):,.0f} / ${float(p95):,.0f} ({hi_pct:+.1f}%)")
    if no_trade_reason:
        print(f"{BAR}")
        print(f"{BAR}  !! Guardrail active: {no_trade_reason}")
        print(f"{BAR}     This is a tentative view only -- not a trade signal.")
    print(f"{BAR}")
    pnl_curve = strategy_pnl_values(s, outcome_prices)
    print(f"{BAR}  Payoff at forecast price levels:")
    for line in _payoff_ascii(outcome_prices, pnl_curve):
        print(line)
    if not no_trade_reason:
        print(f"{BAR}")
        print(f"{BAR}  Thesis: price moves in your favor by expiry (Synth distribution supports this).")
    print(_footer())


def screen_if_wrong(best, no_trade_reason: str | None):
    """Screen 4: If wrong — exit, convert/roll, reassessment rules."""
    print(_header("Screen 4: If Wrong"))
    if best is None:
        print(f"{BAR}  N/A")
        print(_footer())
        return
    if no_trade_reason:
        print(f"{BAR}  (Tentative -- no active trade recommended)")
    print(f"{BAR}  Exit rule      : Close or hedge if price moves against you beyond max loss.")
    print(f"{BAR}  Convert/roll    : {best.reroute_rule}")
    print(f"{BAR}  Reassess at     : {best.review_again_at}")
    print(_footer())


def main():
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        client = SynthClient()
    symbol, view, risk = screen_view_setup()
    data = load_synth_data(client, symbol)
    if data is None:
        print("Could not load Synth data for", symbol)
        return 1
    p1h_last, p24h_last, options, vol, current_price = data
    p1h_available = p1h_last is not None
    fusion_state = run_forecast_fusion(p1h_last, p24h_last, current_price)
    vol_future = (vol.get("forecast_future") or {}).get("average_volatility") or 0
    vol_realized = (vol.get("realized") or {}).get("average_volatility") or 0
    volatility_high = is_volatility_elevated(vol_future, vol_realized)
    vol_ratio = (vol_future / vol_realized) if vol_realized > 0 else 1.0
    confidence = forecast_confidence(p24h_last, current_price)
    no_trade_reason = should_no_trade(fusion_state, view, volatility_high, confidence)
    candidates = generate_strategies(options, view, risk)
    outcome_prices = _outcome_prices(p24h_last)
    scored = rank_strategies(candidates, fusion_state, view, outcome_prices, risk, current_price, confidence, vol_ratio) if candidates else []
    best, safer, upside = select_three_cards(scored)
    screen_top_plays(best, safer, upside, no_trade_reason, confidence, current_price)
    screen_why_this_works(best, fusion_state, current_price, no_trade_reason, outcome_prices, p24h_last, p1h_available)
    screen_if_wrong(best, no_trade_reason)
    decision_log = {
        "inputs": {"symbol": symbol, "view": view, "risk": risk},
        "fusion_state": fusion_state,
        "confidence": round(confidence, 3),
        "volatility_high": volatility_high,
        "vol_forecast": round(vol_future, 2),
        "vol_realized": round(vol_realized, 2),
        "1h_data_available": p1h_available,
        "no_trade": no_trade_reason is not None,
        "no_trade_reason": no_trade_reason,
        "candidates_generated": len(candidates),
        "candidates_after_filters": len(scored),
        "best_match": best.strategy.description if best else None,
        "safer_alt": safer.strategy.description if safer else None,
        "higher_upside": upside.strategy.description if upside else None,
    }
    print(_header("Decision Log"))
    for line in json.dumps(decision_log, indent=2, ensure_ascii=False).split("\n"):
        print(f"{BAR}  {line}")
    print(_footer())
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
