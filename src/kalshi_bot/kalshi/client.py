"""Minimal REST client for the Kalshi trade API."""

from __future__ import annotations

import time
from typing import Any
from urllib.parse import urlparse

import requests

from ..config import Settings
from . import auth


class KalshiError(RuntimeError):
    """Raised when the Kalshi API returns an error response."""


def _dollars_to_cents(value) -> int:
    """Convert a price like ``"0.7500"`` or ``0.75`` (dollars) to integer cents."""
    return round(float(value) * 100)


def normalize_orderbook(payload: dict) -> dict:
    """Normalize a raw orderbook response to ``{"yes": [[cents, size], ...], "no": [...]}``.

    Accepts the current ``orderbook_fp`` schema (dollar-string levels) and the
    legacy ``orderbook`` schema (integer-cent levels). Empty/missing sides
    become empty lists.
    """
    fp = payload.get("orderbook_fp")
    if fp is not None:
        return {
            "yes": [[_dollars_to_cents(p), float(s)] for p, s in (fp.get("yes_dollars") or [])],
            "no": [[_dollars_to_cents(p), float(s)] for p, s in (fp.get("no_dollars") or [])],
        }
    ob = payload.get("orderbook") or {}
    return {
        "yes": [[int(p), s] for p, s in (ob.get("yes") or [])],
        "no": [[int(p), s] for p, s in (ob.get("no") or [])],
    }


class KalshiClient:
    """Thin wrapper over the Kalshi REST endpoints we need.

    Public market-data endpoints work without credentials; portfolio and order
    endpoints require an API key configured in :class:`Settings`.
    """

    def __init__(self, settings: Settings, session: requests.Session | None = None):
        self._settings = settings
        self._session = session or requests.Session()
        self._private_key = None
        if settings.has_credentials:
            self._private_key = auth.load_private_key(settings.private_key_path)  # type: ignore[arg-type]

    # -- internal helpers -------------------------------------------------

    def _request(self, method: str, path: str, *, signed: bool, **kwargs: Any) -> dict[str, Any]:
        url = f"{self._settings.base_url}{path}"
        headers: dict[str, str] = {"Accept": "application/json"}

        if signed:
            if not self._private_key or not self._settings.api_key_id:
                raise KalshiError(f"{method} {path} requires API credentials")
            # The signed path must include the full path of the URL, not just
            # the endpoint suffix.
            full_path = urlparse(url).path
            headers.update(
                auth.auth_headers(
                    self._settings.api_key_id,
                    self._private_key,
                    int(time.time() * 1000),
                    method,
                    full_path,
                )
            )

        resp = self._session.request(method, url, headers=headers, timeout=15, **kwargs)
        if not resp.ok:
            raise KalshiError(f"{method} {path} -> {resp.status_code}: {resp.text}")
        return resp.json() if resp.content else {}

    # -- market data (public) --------------------------------------------

    def get_markets(self, *, limit: int = 100, status: str = "open", **params: Any) -> list[dict]:
        """List markets. Returns the ``markets`` array of the response."""
        params = {"limit": limit, "status": status, **params}
        data = self._request("GET", "/markets", signed=False, params=params)
        return data.get("markets", [])

    def get_market(self, ticker: str) -> dict:
        data = self._request("GET", f"/markets/{ticker}", signed=False)
        return data.get("market", {})

    def get_candlesticks(
        self, series_ticker: str, ticker: str, start_ts: int, end_ts: int,
        period_interval: int = 1440,
    ) -> list[dict]:
        """Historical OHLC candlesticks for a market (public).

        ``period_interval`` is in minutes (1, 60, or 1440 for daily). Returns the
        ``candlesticks`` array; each entry has ``yes_bid``/``yes_ask`` sub-objects
        with ``close_dollars`` etc.
        """
        data = self._request(
            "GET", f"/series/{series_ticker}/markets/{ticker}/candlesticks", signed=False,
            params={"start_ts": start_ts, "end_ts": end_ts, "period_interval": period_interval},
        )
        return data.get("candlesticks", [])

    def get_orderbook(self, ticker: str, *, depth: int = 10) -> dict:
        """Return the order book normalized to ``{"yes": [[cents, size], ...], "no": [...]}``.

        Handles both the current Kalshi schema (``orderbook_fp`` with
        ``yes_dollars``/``no_dollars`` as ``["0.7500", "75.00"]`` dollar-string
        levels) and the older ``orderbook`` schema with integer-cent levels.
        Levels are returned best-price-last, matching the feed's expectations.
        """
        data = self._request(
            "GET", f"/markets/{ticker}/orderbook", signed=False, params={"depth": depth}
        )
        return normalize_orderbook(data)

    # -- portfolio (signed) ----------------------------------------------

    def get_balance(self) -> int:
        """Account balance in cents."""
        data = self._request("GET", "/portfolio/balance", signed=True)
        return int(data.get("balance", 0))

    def get_positions(self) -> list[dict]:
        data = self._request("GET", "/portfolio/positions", signed=True)
        return data.get("market_positions", [])

    def create_order(
        self,
        *,
        ticker: str,
        side: str,
        action: str,
        count: int,
        price_cents: int | None = None,
        order_type: str = "limit",
        client_order_id: str | None = None,
    ) -> dict:
        """Place an order. ``side`` is "yes"/"no"; ``action`` is "buy"/"sell"."""
        body: dict[str, Any] = {
            "ticker": ticker,
            "side": side,
            "action": action,
            "count": count,
            "type": order_type,
        }
        if order_type == "limit":
            if price_cents is None:
                raise ValueError("limit orders require price_cents")
            # Kalshi limit price field is named per-side.
            body[f"{side}_price"] = price_cents
        if client_order_id:
            body["client_order_id"] = client_order_id
        data = self._request("POST", "/portfolio/orders", signed=True, json=body)
        return data.get("order", {})
