"""Map Polymarket URL/slug to Synth market type and supported asset."""

import re
from typing import Literal

MARKET_DAILY = "daily"
MARKET_HOURLY = "hourly"
MARKET_15MIN = "15min"
MARKET_5MIN = "5min"
MARKET_RANGE = "range"

_HOURLY_TIME_PATTERN = re.compile(r"\d{1,2}(am|pm)")
_15MIN_PATTERN = re.compile(r"(updown|up-down)-15m-|(?<!\d)15-?min")
_5MIN_PATTERN = re.compile(r"(updown|up-down)-5m-|(?<!1)5-?min")

_ASSET_PREFIXES = {
    "bitcoin": "BTC",
    "ethereum": "ETH",
    "solana": "SOL",
    "xrp": "XRP",
    "btc": "BTC",
    "eth": "ETH",
    "sol": "SOL",
}


def asset_from_slug(slug: str) -> str | None:
    """Extract the asset ticker (BTC, ETH, …) from a Polymarket slug prefix."""
    if not slug:
        return None
    slug_lower = slug.lower()
    for prefix, ticker in _ASSET_PREFIXES.items():
        if slug_lower.startswith(prefix + "-"):
            return ticker
    return None


def normalize_slug(url_or_slug: str) -> str | None:
    """Extract market slug from Polymarket URL or return slug as-is if already a slug."""
    if not url_or_slug or not isinstance(url_or_slug, str):
        return None
    s = url_or_slug.strip()
    m = re.search(r"polymarket\.com/(?:event/|market/)?([a-zA-Z0-9-]+)", s)
    if m:
        return m.group(1)
    if re.match(r"^[a-zA-Z0-9-]+$", s):
        return s
    return None


def get_market_type(slug: str) -> Literal["daily", "hourly", "15min", "5min", "range"] | None:
    """Infer Synth market type from slug. Returns None if not recognizable."""
    if not slug:
        return None
    slug_lower = slug.lower()
    if _5MIN_PATTERN.search(slug_lower):
        return MARKET_5MIN
    if _15MIN_PATTERN.search(slug_lower):
        return MARKET_15MIN
    if "up-or-down" in slug_lower and _HOURLY_TIME_PATTERN.search(slug_lower):
        return MARKET_HOURLY
    if "up-or-down" in slug_lower and "on-" in slug_lower:
        return MARKET_DAILY
    if "price-on" in slug_lower:
        return MARKET_RANGE
    return None


def is_supported(slug: str) -> bool:
    """True if slug maps to a Synth-supported market (daily, hourly, or range)."""
    return get_market_type(slug) is not None
