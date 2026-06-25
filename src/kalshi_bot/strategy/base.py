"""Strategy interface and the signal type strategies emit."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ..kalshi.feed import MarketSnapshot


@dataclass(frozen=True)
class Signal:
    """A strategy's desired action for one market.

    A strategy never sizes or places orders itself — it only expresses intent.
    The engine applies risk limits and routes the order to the broker.
    """

    ticker: str
    side: str  # "yes" or "no"
    action: str  # "buy" or "sell"
    price_cents: int  # limit price the strategy is willing to trade at
    # Strategy's estimated fair value (cents) for the YES contract; used by the
    # engine for Kelly sizing. None falls back to a flat default size.
    fair_value_cents: float | None = None
    reason: str = ""


class Strategy(Protocol):
    """A strategy consumes snapshots and optionally emits a :class:`Signal`."""

    name: str

    def on_snapshot(self, snapshot: MarketSnapshot) -> Signal | None:
        ...
