"""Live broker — routes orders to the real Kalshi API.

This places real orders for real money. It is only reachable when the operator
explicitly opts in (``--live`` plus a confirmation flag on the CLI) and a
:class:`~kalshi_bot.risk.guard.RiskGuard` is active. Orders are placed as limit
orders at the signal's price.

Realized P&L is *not* computed here (the exchange owns position accounting);
``Fill.realized_pnl_usd`` is left at 0.0 and the engine relies on the broker's
own reporting / the Kalshi portfolio endpoints for true P&L.
"""

from __future__ import annotations

import logging

from ..kalshi.client import KalshiClient
from .base import Fill

log = logging.getLogger("kalshi_bot.execution.live")


class LiveBroker:
    def __init__(self, client: KalshiClient):
        self._client = client

    def execute(
        self, ticker: str, side: str, action: str, contracts: int, price_cents: int
    ) -> Fill:
        order = self._client.create_order(
            ticker=ticker,
            side=side,
            action=action,
            count=contracts,
            price_cents=price_cents,
            order_type="limit",
        )
        log.info("LIVE order submitted: %s", order.get("order_id", order))
        return Fill(
            ticker=ticker,
            side=side,
            action=action,
            contracts=contracts,
            price_cents=price_cents,
            realized_pnl_usd=0.0,
        )
