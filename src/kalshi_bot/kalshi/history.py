"""Convert Kalshi candlestick history into backtest snapshots.

The candlesticks endpoint returns OHLC bars with per-bar ``yes_bid`` and
``yes_ask`` sub-objects. We take each bar's closing bid/ask as one
:class:`~kalshi_bot.kalshi.feed.MarketSnapshot`, giving a real historical price
series to backtest a strategy on.
"""

from __future__ import annotations

from .feed import MarketSnapshot


def _close_cents(side: dict | None) -> int | None:
    """Closing price of a candle's bid/ask sub-object, in cents."""
    if not side:
        return None
    val = side.get("close_dollars")
    if val is None:
        return None
    cents = round(float(val) * 100)
    return cents if cents > 0 else None


def candlesticks_to_snapshots(candles: list[dict], ticker: str) -> list[MarketSnapshot]:
    """Build snapshots from candlesticks, skipping bars with no two-sided quote."""
    snapshots = []
    for c in candles:
        yes_bid = _close_cents(c.get("yes_bid"))
        yes_ask = _close_cents(c.get("yes_ask"))
        ts = c.get("end_period_ts")
        if yes_bid is None or yes_ask is None or yes_bid >= yes_ask:
            continue
        snapshots.append(
            MarketSnapshot(ticker=ticker, yes_bid=yes_bid, yes_ask=yes_ask, ts=float(ts or 0))
        )
    return snapshots
