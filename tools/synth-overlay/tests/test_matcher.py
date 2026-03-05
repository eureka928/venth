"""Tests for market slug / URL matcher."""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from matcher import asset_from_slug, normalize_slug, get_market_type, is_supported


def test_normalize_slug_from_url():
    assert normalize_slug("https://polymarket.com/event/bitcoin-up-or-down-on-february-26") == "bitcoin-up-or-down-on-february-26"
    assert normalize_slug("https://polymarket.com/market/bitcoin-price-on-february-26") == "bitcoin-price-on-february-26"


def test_normalize_slug_passthrough():
    assert normalize_slug("bitcoin-up-or-down-on-february-26") == "bitcoin-up-or-down-on-february-26"


def test_normalize_slug_invalid():
    assert normalize_slug("") is None
    assert normalize_slug(None) is None


def test_get_market_type_daily():
    assert get_market_type("bitcoin-up-or-down-on-february-26") == "daily"
    assert get_market_type("btc-up-or-down-on-march-1") == "daily"


def test_get_market_type_hourly():
    assert get_market_type("bitcoin-up-or-down-february-25-6pm-et") == "hourly"
    assert get_market_type("bitcoin-up-or-down-february-26-10am-et") == "hourly"
    assert get_market_type("btc-up-or-down-march-1-3pm-et") == "hourly"


def test_get_market_type_15min():
    assert get_market_type("btc-updown-15m-1772204400") == "15min"
    assert get_market_type("eth-updown-15m-1772204400") == "15min"
    assert get_market_type("sol-up-down-15m-1772204400") == "15min"
    assert get_market_type("bitcoin-15min-market") == "15min"


def test_get_market_type_5min():
    assert get_market_type("btc-updown-5m-1772205000") == "5min"
    assert get_market_type("eth-updown-5m-1772205000") == "5min"
    assert get_market_type("sol-up-down-5m-1772205000") == "5min"
    assert get_market_type("bitcoin-5min-market") == "5min"


def test_get_market_type_range():
    assert get_market_type("bitcoin-price-on-february-26") == "range"


def test_get_market_type_unsupported():
    assert get_market_type("random-slug") is None


def test_is_supported():
    assert is_supported("bitcoin-up-or-down-on-february-26") is True
    assert is_supported("bitcoin-price-on-february-26") is True
    assert is_supported("btc-updown-15m-1772204400") is True
    assert is_supported("eth-updown-5m-1772205000") is True
    assert is_supported("unknown-market") is False


def test_asset_from_slug():
    assert asset_from_slug("bitcoin-up-or-down-on-february-26") == "BTC"
    assert asset_from_slug("ethereum-up-or-down-on-february-28") == "ETH"
    assert asset_from_slug("solana-up-or-down-on-march-1") == "SOL"
    assert asset_from_slug("xrp-up-or-down-on-march-1") == "XRP"


def test_asset_from_slug_short_prefixes():
    assert asset_from_slug("btc-up-or-down-on-march-1") == "BTC"
    assert asset_from_slug("eth-updown-15m-1772204400") == "ETH"
    assert asset_from_slug("sol-updown-5m-1772205000") == "SOL"


def test_asset_from_slug_unknown():
    assert asset_from_slug("random-slug") is None
    assert asset_from_slug("") is None
    assert asset_from_slug(None) is None
