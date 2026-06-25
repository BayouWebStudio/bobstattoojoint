"""A simple mean-reversion threshold strategy (example/reference).

It maintains a running estimate of fair value (an exponential moving average of
the mid-price) and trades when the market deviates from it by more than a
threshold: buy YES when the market is cheap relative to fair value, sell when
rich. This is intentionally simple — a baseline to validate the plumbing, not a
profitable edge.
"""

from __future__ import annotations

from ..kalshi.feed import MarketSnapshot
from .base import Signal


class ThresholdStrategy:
    name = "threshold-mean-reversion"

    def __init__(self, alpha: float = 0.2, threshold_cents: float = 3.0):
        """``alpha`` is the EMA weight; ``threshold_cents`` the trigger band."""
        self._alpha = alpha
        self._threshold = threshold_cents
        self._fair: float | None = None

    def on_snapshot(self, snapshot: MarketSnapshot) -> Signal | None:
        mid = snapshot.yes_mid
        if self._fair is None:
            self._fair = mid
            return None
        self._fair = self._alpha * mid + (1 - self._alpha) * self._fair

        edge = self._fair - mid
        if edge > self._threshold:
            # Market cheap vs fair value -> buy YES at the ask.
            return Signal(
                ticker=snapshot.ticker,
                side="yes",
                action="buy",
                price_cents=snapshot.yes_ask,
                fair_value_cents=self._fair,
                reason=f"mid {mid:.1f} < fair {self._fair:.1f} (edge {edge:.1f})",
            )
        if -edge > self._threshold:
            # Market rich vs fair value -> sell YES at the bid.
            return Signal(
                ticker=snapshot.ticker,
                side="yes",
                action="sell",
                price_cents=snapshot.yes_bid,
                fair_value_cents=self._fair,
                reason=f"mid {mid:.1f} > fair {self._fair:.1f} (edge {edge:.1f})",
            )
        return None
