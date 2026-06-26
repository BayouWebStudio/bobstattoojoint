"""Kalshi as a :class:`~kalshi_bot.venues.base.Venue`."""

from __future__ import annotations

from ..kalshi.client import KalshiClient
from .base import Quote


class KalshiVenue:
    name = "kalshi"

    def __init__(self, client: KalshiClient):
        self._client = client

    def quote(self, market_id: str) -> Quote | None:
        orderbook = self._client.get_orderbook(market_id)
        return Quote.from_kalshi_orderbook(self.name, market_id, orderbook)
