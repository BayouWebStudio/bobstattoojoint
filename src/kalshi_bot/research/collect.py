"""Build settlement samples from the live Kalshi API.

For each settled market in a set of series, fetch daily candlesticks and take the
mid-life quote as the entry price (avoiding the settlement convergence at the
end), paired with the realized outcome. Market-data endpoints are public, so no
credentials are needed.
"""

from __future__ import annotations

import datetime as dt
import logging

from ..kalshi.client import KalshiClient
from .calibration import SettlementSample

log = logging.getLogger("kalshi_bot.research.collect")

# Liquid, recurring multi-candidate series that span the full probability range.
DEFAULT_SERIES = [
    "KXPRESNOMD", "KXPRESNOMR", "KXPRESPERSON", "KXCITRINI", "KXVOTEPRIMARY",
    "KXLAMAYORMATCHUP", "KXCAGOVPRIMARY1ST", "KXPRIMARYMOV", "KXNFLMVP",
    "KXNEXTAG", "KXGREENLAND", "KXLAMAYORADVANCE", "KXMOVVAREDISTRICT",
]


def _close_ts(market: dict) -> float | None:
    try:
        return dt.datetime.fromisoformat(market["close_time"].replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def collect_settled_samples(
    client: KalshiClient,
    series: list[str] | None = None,
    *,
    min_volume: float = 100.0,
    max_candlestick_calls: int = 700,
    lookback_days: int = 400,
) -> list[SettlementSample]:
    """Collect (entry quote, outcome) samples from settled markets in ``series``."""
    series = series or DEFAULT_SERIES
    markets: list[str] = []
    series_of: dict[str, str] = {}

    for ser in series:
        cursor = None
        for _ in range(5):
            params = {"series_ticker": ser, "status": "settled", "limit": 200}
            if cursor:
                params["cursor"] = cursor
            data = client._request("GET", "/markets", signed=False, params=params)
            for m in data.get("markets", []):
                try:
                    vol = float(m.get("volume_fp") or 0)
                except (TypeError, ValueError):
                    vol = 0
                if m.get("result") in ("yes", "no") and vol > min_volume:
                    markets.append(m["ticker"])
                    series_of[m["ticker"]] = ser
                    series_of[m["ticker"] + "::result"] = m["result"]
                    series_of[m["ticker"] + "::close"] = m.get("close_time", "")
            cursor = data.get("cursor")
            if not cursor:
                break

    samples: list[SettlementSample] = []
    calls = 0
    for ticker in markets:
        if calls >= max_candlestick_calls:
            break
        ser = series_of[ticker]
        close = _close_ts({"close_time": series_of[ticker + "::close"]})
        if close is None:
            continue
        try:
            candles = client.get_candlesticks(
                ser, ticker, int(close - lookback_days * 86400), int(close), 1440
            )
            calls += 1
        except Exception:
            continue
        quotes = []
        for c in candles:
            yb = (c.get("yes_bid") or {}).get("close_dollars")
            ya = (c.get("yes_ask") or {}).get("close_dollars")
            try:
                yb, ya = float(yb), float(ya)
            except (TypeError, ValueError):
                continue
            if 0 < yb < ya < 1:
                quotes.append((round(yb * 100), round(ya * 100)))
        if len(quotes) < 2:
            continue
        yb, ya = quotes[len(quotes) // 2]  # mid-life entry
        samples.append(
            SettlementSample(
                ticker=ticker,
                yes_bid=yb,
                yes_ask=ya,
                result_yes=series_of[ticker + "::result"] == "yes",
            )
        )

    log.info("collected %d samples from %d candlestick calls", len(samples), calls)
    return samples
