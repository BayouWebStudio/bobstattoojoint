"""Fade-longshot strategy — the favorite-longshot bias (FLB).

Across betting and prediction markets, low-probability "longshot" contracts are
systematically *overpriced*: a contract trading at 5c YES resolves YES less than
5% of the time. The edge is to **fade** them — buy NO on cheap YES contracts and
hold to settlement.

Empirically (see ``research/`` and the calibration command), on liquid Kalshi
election markets this shows ~+2-4% ROI on deep longshots (YES ask <= ~15c),
strongest with passive entry. It is a behavioral bias, not a free arbitrage, so
it persists — but it is modest and carries rare large losses (the occasional
longshot that wins), so size it small.

The strategy emits a single NO-buy signal per qualifying market. It does not
model settlement itself; in live/paper trading the position is held until the
market resolves. For edge validation on settled data, use
``research.calibration.fade_longshot_backtest``.
"""

from __future__ import annotations

from ..kalshi.feed import MarketSnapshot
from .base import Signal


class FadeLongshotStrategy:
    name = "fade-longshot"

    def __init__(self, max_yes_ask_cents: int = 15, min_yes_ask_cents: int = 2):
        """Fade YES longshots priced in ``[min_yes_ask_cents, max_yes_ask_cents]``.

        The floor avoids near-zero "dead" contracts where the spread dominates
        and fills are unrealistic.
        """
        self._max = max_yes_ask_cents
        self._min = min_yes_ask_cents
        self._traded: set[str] = set()

    def on_snapshot(self, snapshot: MarketSnapshot) -> Signal | None:
        # One entry per market — this is a hold-to-settlement position.
        if snapshot.ticker in self._traded:
            return None
        if not self._min <= snapshot.yes_ask <= self._max:
            return None

        self._traded.add(snapshot.ticker)
        # Buy NO at the NO ask, which equals 100 - best YES bid.
        no_ask = 100 - snapshot.yes_bid
        return Signal(
            ticker=snapshot.ticker,
            side="no",
            action="buy",
            price_cents=no_ask,
            reason=f"fade longshot: YES ask {snapshot.yes_ask}c <= {self._max}c",
        )
