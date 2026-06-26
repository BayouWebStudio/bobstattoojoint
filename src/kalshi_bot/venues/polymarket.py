"""Polymarket CLOB client and venue adapter.

Polymarket runs a central limit order book (CLOB). Each binary market has two
outcome tokens (YES and NO), each with its own order book reachable at
``GET /book?token_id=...``. Prices are quoted in dollars (0-1).

Reading prices is unauthenticated; placing orders requires an EIP-712 signed
API key and an on-chain (Polygon) allowance, which is out of scope here — this
adapter is read-only, for cross-venue arbitrage *detection*.
"""

from __future__ import annotations

import requests

from .base import Quote

CLOB_BASE = "https://clob.polymarket.com"


class PolymarketClient:
    def __init__(self, base_url: str = CLOB_BASE, session: requests.Session | None = None):
        self._base = base_url
        self._session = session or requests.Session()

    def book(self, token_id: str) -> dict:
        """Fetch the order book for one outcome token."""
        resp = self._session.get(
            f"{self._base}/book", params={"token_id": token_id}, timeout=15
        )
        resp.raise_for_status()
        return resp.json()


class PolymarketVenue:
    """Quotes Polymarket markets given a map of market_id -> (yes_token, no_token)."""

    name = "polymarket"

    def __init__(self, client: PolymarketClient, token_map: dict[str, tuple[str, str]]):
        self._client = client
        self._token_map = token_map

    def quote(self, market_id: str) -> Quote | None:
        tokens = self._token_map.get(market_id)
        if tokens is None:
            return None
        yes_token, no_token = tokens
        yes_book = self._client.book(yes_token)
        no_book = self._client.book(no_token)
        return Quote.from_polymarket_books(self.name, market_id, yes_book, no_book)
