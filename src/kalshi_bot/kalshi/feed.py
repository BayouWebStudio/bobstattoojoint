"""Market-data feeds.

A feed yields :class:`MarketSnapshot` objects over time. Two implementations:

- :class:`RestPollingFeed` polls the live Kalshi REST API.
- :class:`MockFeed` replays a synthetic random-walk price series so the bot can
  run end-to-end with no credentials and no network.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from dataclasses import dataclass

from .client import KalshiClient


@dataclass(frozen=True)
class MarketSnapshot:
    """A single observation of a market's top-of-book."""

    ticker: str
    # Best prices in cents (1-99). "yes_bid" is the best price to sell YES at,
    # "yes_ask" is the best price to buy YES at.
    yes_bid: int
    yes_ask: int
    ts: float

    @property
    def yes_mid(self) -> float:
        """Mid-price of the YES contract in cents."""
        return (self.yes_bid + self.yes_ask) / 2


class RestPollingFeed:
    """Polls the live REST order book for a set of tickers at a fixed interval."""

    def __init__(self, client: KalshiClient, tickers: list[str], interval_s: float = 2.0):
        self._client = client
        self._tickers = tickers
        self._interval = interval_s

    def stream(self, max_ticks: int | None = None) -> Iterator[MarketSnapshot]:
        ticks = 0
        while max_ticks is None or ticks < max_ticks:
            for ticker in self._tickers:
                ob = self._client.get_orderbook(ticker)
                snap = self._snapshot_from_orderbook(ticker, ob)
                if snap is not None:
                    yield snap
            ticks += 1
            if max_ticks is None or ticks < max_ticks:
                time.sleep(self._interval)

    @staticmethod
    def _snapshot_from_orderbook(ticker: str, ob: dict) -> MarketSnapshot | None:
        # Kalshi order book lists [price, size] levels for each side, best last.
        yes_levels = ob.get("yes") or []
        no_levels = ob.get("no") or []
        if not yes_levels or not no_levels:
            return None
        best_yes_bid = yes_levels[-1][0]
        # A NO bid at price p is equivalent to a YES ask at (100 - p).
        best_yes_ask = 100 - no_levels[-1][0]
        return MarketSnapshot(
            ticker=ticker,
            yes_bid=best_yes_bid,
            yes_ask=best_yes_ask,
            ts=time.time(),
        )


class MockFeed:
    """Deterministic synthetic feed for offline paper trading and tests.

    Generates a bounded random walk around a fair value using a seeded LCG so
    runs are reproducible without importing ``random`` (which the harness
    restricts in some contexts).
    """

    def __init__(
        self,
        ticker: str = "MOCK-DEMO",
        start: int = 50,
        spread: int = 2,
        seed: int = 1,
    ):
        self._ticker = ticker
        self._mid = start
        self._spread = spread
        self._state = seed & 0xFFFFFFFF

    def _next_step(self) -> int:
        # Linear congruential generator -> step in {-2, -1, 0, +1, +2}.
        self._state = (1103515245 * self._state + 12345) & 0x7FFFFFFF
        return (self._state % 5) - 2

    def stream(self, max_ticks: int | None = 100) -> Iterator[MarketSnapshot]:
        ticks = 0
        while max_ticks is None or ticks < max_ticks:
            self._mid = max(2 + self._spread, min(98 - self._spread, self._mid + self._next_step()))
            half = self._spread // 2 or 1
            yield MarketSnapshot(
                ticker=self._ticker,
                yes_bid=self._mid - half,
                yes_ask=self._mid + half,
                ts=float(ticks),
            )
            ticks += 1


class MockEventFeed:
    """Synthetic feed for a mutually-exclusive 3-outcome event (for arb demos).

    The outcomes' fair YES prices sum to 100c. Most ticks the book is "tight"
    (asks sum to > 100, no arb). Periodically a dislocation makes the combined
    ask dip below 100c, creating a detectable underpriced arbitrage.
    """

    EVENT = "MOCK-EVENT"
    TICKERS = ("MOCK-EVENT-A", "MOCK-EVENT-B", "MOCK-EVENT-C")

    def __init__(self, fair: tuple[int, int, int] = (45, 35, 20), half_spread: int = 1):
        self._fair = fair
        self._half = half_spread

    def stream(self, max_ticks: int | None = 40) -> Iterator[MarketSnapshot]:
        ticks = 0
        while max_ticks is None or ticks < max_ticks:
            # Every 5th tick, dislocate outcome A downward to open an arb.
            dislocate = -4 if ticks % 5 == 0 else 0
            for i, ticker in enumerate(self.TICKERS):
                mid = self._fair[i] + (dislocate if i == 0 else 0)
                yield MarketSnapshot(
                    ticker=ticker,
                    yes_bid=mid - self._half,
                    yes_ask=mid + self._half,
                    ts=float(ticks),
                )
            ticks += 1
