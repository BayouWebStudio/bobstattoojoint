"""Shared execution types: the :class:`Fill` record and :class:`Broker` protocol.

Both the paper broker and the live broker implement :class:`Broker`, so the
engine can route orders to either without knowing which it holds.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass
class Fill:
    """The result of executing an order."""

    ticker: str
    side: str  # "yes" or "no"
    action: str  # "buy" or "sell"
    contracts: int
    price_cents: int
    realized_pnl_usd: float = 0.0


@runtime_checkable
class Broker(Protocol):
    """Anything that can execute an order and report the resulting fill."""

    def execute(
        self, ticker: str, side: str, action: str, contracts: int, price_cents: int
    ) -> Fill:
        ...
