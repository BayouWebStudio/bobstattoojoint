"""WebSocket streaming feed for lower-latency market data.

Kalshi's streaming API pushes an ``orderbook_snapshot`` when you subscribe, then
incremental ``orderbook_delta`` messages as levels change. Maintaining a local
book from these is far lower latency than polling REST.

The book-maintenance logic lives in :class:`OrderBookState` (pure, no I/O) so it
can be unit-tested without a network. :class:`WebSocketFeed` wraps it with an
actual socket connection.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Iterator

from ..config import Settings
from . import auth
from .feed import MarketSnapshot

log = logging.getLogger("kalshi_bot.kalshi.ws")


class OrderBookState:
    """Local order book for one or more markets, fed by snapshot/delta messages.

    Levels are stored as ``{price_cents: resting_size}`` per side. A level with
    size <= 0 is removed. Prices are in cents (1-99).
    """

    def __init__(self) -> None:
        # ticker -> side ("yes"/"no") -> {price: size}
        self._books: dict[str, dict[str, dict[int, int]]] = {}

    def _book(self, ticker: str) -> dict[str, dict[int, int]]:
        return self._books.setdefault(ticker, {"yes": {}, "no": {}})

    def apply_message(self, message: dict) -> MarketSnapshot | None:
        """Apply one parsed WS message; return a fresh snapshot if it changed a book."""
        mtype = message.get("type")
        msg = message.get("msg", {})
        ticker = msg.get("market_ticker")
        if not ticker:
            return None

        if mtype == "orderbook_snapshot":
            book = {"yes": {}, "no": {}}
            for price, size in msg.get("yes", []):
                book["yes"][int(price)] = int(size)
            for price, size in msg.get("no", []):
                book["no"][int(price)] = int(size)
            self._books[ticker] = book
        elif mtype == "orderbook_delta":
            side = msg.get("side")
            price = int(msg["price"])
            delta = int(msg["delta"])
            levels = self._book(ticker)[side]
            new_size = levels.get(price, 0) + delta
            if new_size > 0:
                levels[price] = new_size
            else:
                levels.pop(price, None)
        else:
            return None

        return self.snapshot(ticker)

    def snapshot(self, ticker: str) -> MarketSnapshot | None:
        """Top-of-book snapshot for ``ticker`` (None if either side is empty)."""
        book = self._books.get(ticker)
        if not book or not book["yes"] or not book["no"]:
            return None
        best_yes_bid = max(book["yes"])
        best_no_bid = max(book["no"])
        # A NO bid at price p implies a YES offer (ask) at (100 - p).
        return MarketSnapshot(
            ticker=ticker,
            yes_bid=best_yes_bid,
            yes_ask=100 - best_no_bid,
            ts=time.time(),
        )


class WebSocketFeed:
    """Subscribes to ``orderbook_delta`` and yields snapshots as the book updates.

    Requires API credentials (the WS handshake is signed). Import is cheap;
    ``websocket-client`` is only imported when :meth:`stream` is called.
    """

    def __init__(self, settings: Settings, tickers: list[str]):
        if not settings.has_credentials:
            raise ValueError("WebSocketFeed requires API credentials")
        self._settings = settings
        self._tickers = tickers
        self._state = OrderBookState()

    def _connect(self):
        from websocket import create_connection  # lazy import

        private_key = auth.load_private_key(self._settings.private_key_path)  # type: ignore[arg-type]
        headers = auth.auth_headers(
            self._settings.api_key_id,  # type: ignore[arg-type]
            private_key,
            int(time.time() * 1000),
            "GET",
            "/trade-api/ws/v2",
        )
        header_list = [f"{k}: {v}" for k, v in headers.items()]
        return create_connection(self._settings.ws_url, header=header_list)

    def stream(self, max_messages: int | None = None) -> Iterator[MarketSnapshot]:
        ws = self._connect()
        try:
            ws.send(json.dumps({
                "id": 1,
                "cmd": "subscribe",
                "params": {"channels": ["orderbook_delta"], "market_tickers": self._tickers},
            }))
            seen = 0
            while max_messages is None or seen < max_messages:
                raw = ws.recv()
                seen += 1
                if not raw:
                    continue
                snap = self._state.apply_message(json.loads(raw))
                if snap is not None:
                    yield snap
        finally:
            ws.close()
