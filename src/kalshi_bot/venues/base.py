"""A venue-agnostic two-sided quote.

Kalshi and Polymarket price the same event differently (Kalshi in cents on a
single YES/NO book; Polymarket in dollars across two separate outcome-token
books). :class:`Quote` normalizes both to a common two-sided form in **cents**,
so the cross-venue arbitrage detector can compare them directly.

Conventions (all integers, cents, 1-99):
  yes_bid  best price you can SELL YES for
  yes_ask  best price you can BUY  YES for
  no_bid   best price you can SELL NO  for
  no_ask   best price you can BUY  NO  for
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Quote:
    venue: str
    market_id: str
    yes_bid: int
    yes_ask: int
    no_bid: int
    no_ask: int

    @classmethod
    def from_kalshi_orderbook(cls, venue: str, market_id: str, orderbook: dict) -> "Quote | None":
        """Build a quote from a Kalshi order book.

        Kalshi lists resting YES bids and NO bids (best level last). Buying YES
        is filled by NO bids, so ``yes_ask = 100 - best_no_bid`` (and symmetric).
        """
        yes_levels = orderbook.get("yes") or []
        no_levels = orderbook.get("no") or []
        if not yes_levels or not no_levels:
            return None
        best_yes_bid = int(yes_levels[-1][0])
        best_no_bid = int(no_levels[-1][0])
        return cls(
            venue=venue,
            market_id=market_id,
            yes_bid=best_yes_bid,
            yes_ask=100 - best_no_bid,
            no_bid=best_no_bid,
            no_ask=100 - best_yes_bid,
        )

    @classmethod
    def from_polymarket_books(
        cls, venue: str, market_id: str, yes_book: dict, no_book: dict
    ) -> "Quote | None":
        """Build a quote from Polymarket's two outcome-token order books.

        Each book has ``bids``/``asks`` as ``[{"price": "0.42", "size": ...}]``
        with prices in dollars (0-1). YES and NO are independent tokens, so each
        contributes its own bid/ask directly.
        """
        yb = _best_bid_ask_cents(yes_book)
        nb = _best_bid_ask_cents(no_book)
        if yb is None or nb is None:
            return None
        yes_bid, yes_ask = yb
        no_bid, no_ask = nb
        return cls(
            venue=venue,
            market_id=market_id,
            yes_bid=yes_bid,
            yes_ask=yes_ask,
            no_bid=no_bid,
            no_ask=no_ask,
        )


def _best_bid_ask_cents(book: dict) -> tuple[int, int] | None:
    """Return ``(best_bid_cents, best_ask_cents)`` from a Polymarket token book."""
    bids = book.get("bids") or []
    asks = book.get("asks") or []
    if not bids or not asks:
        return None
    best_bid = max(float(level["price"]) for level in bids)
    best_ask = min(float(level["price"]) for level in asks)
    return round(best_bid * 100), round(best_ask * 100)


class Venue(Protocol):
    """Anything that can quote a market by id."""

    name: str

    def quote(self, market_id: str) -> Quote | None:
        ...


class MockVenue:
    """A venue that returns preset quotes, for tests and offline demos."""

    def __init__(self, name: str, quotes: dict[str, Quote]):
        self.name = name
        self._quotes = quotes

    def quote(self, market_id: str) -> Quote | None:
        return self._quotes.get(market_id)
