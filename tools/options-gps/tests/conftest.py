"""Shared fixtures for exchange/line-shopping tests.
Does NOT affect existing test_pipeline.py which uses module-level constants."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from exchange import ExchangeQuote
from pipeline import StrategyCandidate, StrategyLeg, generate_strategies, run_forecast_fusion, forecast_confidence

MOCK_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "..", "mock_data", "exchange_options")

BTC_OPTION_DATA = {
    "current_price": 67723,
    "call_options": {"67000": 987, "67500": 640, "68000": 373},
    "put_options": {"67000": 140, "67500": 291, "68000": 526},
}

P24H = {
    "0.05": 66000, "0.2": 67000, "0.35": 67400,
    "0.5": 67800, "0.65": 68200, "0.8": 68800, "0.95": 70000,
}


@pytest.fixture
def mock_exchange_dir():
    return MOCK_DIR


@pytest.fixture
def btc_option_data():
    return {
        "current_price": 67723.50,
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


@pytest.fixture
def sample_strategy():
    """A simple long call for testing divergence."""
    return StrategyCandidate(
        strategy_type="long_call", direction="bullish",
        description="Long 67500 Call", strikes=[67500],
        cost=638.43, max_loss=638.43,
        legs=[StrategyLeg(action="BUY", quantity=1, option_type="Call", strike=67500, premium=638.43)],
    )


@pytest.fixture
def multi_leg_strategy():
    """A call debit spread for testing multi-leg divergence."""
    return StrategyCandidate(
        strategy_type="call_debit_spread", direction="bullish",
        description="Call Debit Spread 67000/68000", strikes=[67000, 68000],
        cost=987.04 - 373.27, max_loss=987.04 - 373.27,
        legs=[
            StrategyLeg(action="BUY", quantity=1, option_type="Call", strike=67000, premium=987.04),
            StrategyLeg(action="SELL", quantity=1, option_type="Call", strike=68000, premium=373.27),
        ],
    )


@pytest.fixture
def sample_exchange_quotes():
    """Pre-built list of ExchangeQuote objects."""
    return [
        ExchangeQuote(exchange="deribit", asset="BTC", strike=67500, option_type="call",
                      bid=610.0, ask=660.0, mid=635.0, implied_vol=51.2),
        ExchangeQuote(exchange="aevo", asset="BTC", strike=67500, option_type="call",
                      bid=620.0, ask=655.0, mid=637.5, implied_vol=51.25),
        ExchangeQuote(exchange="deribit", asset="BTC", strike=67500, option_type="put",
                      bid=275.0, ask=310.0, mid=292.5, implied_vol=51.0),
        ExchangeQuote(exchange="aevo", asset="BTC", strike=67500, option_type="put",
                      bid=280.0, ask=305.0, mid=292.5, implied_vol=51.0),
        ExchangeQuote(exchange="deribit", asset="BTC", strike=67000, option_type="call",
                      bid=950.0, ask=1025.0, mid=987.5, implied_vol=51.8),
        ExchangeQuote(exchange="aevo", asset="BTC", strike=67000, option_type="call",
                      bid=960.0, ask=1010.0, mid=985.0, implied_vol=51.75),
        ExchangeQuote(exchange="deribit", asset="BTC", strike=68000, option_type="call",
                      bid=355.0, ask=390.0, mid=372.5, implied_vol=50.8),
        ExchangeQuote(exchange="aevo", asset="BTC", strike=68000, option_type="call",
                      bid=360.0, ask=385.0, mid=372.5, implied_vol=50.75),
    ]


@pytest.fixture
def ranking_context():
    """Shared setup for ranking integration tests: candidates, outcome_prices, fusion, confidence."""
    candidates = generate_strategies(BTC_OPTION_DATA, "bullish", "medium")
    outcome_prices = [float(P24H[k]) for k in sorted(P24H.keys())]
    fusion = run_forecast_fusion(None, P24H, 67723)
    confidence = forecast_confidence(P24H, 67723)
    return candidates, outcome_prices, fusion, confidence
