"""The trading engine: feed -> strategy -> risk -> broker.

The engine is mode-agnostic. In paper mode it routes fills to a
:class:`PaperBroker`; in live mode it would route to the Kalshi client (gated
behind an explicit flag and not enabled by default).
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from dataclasses import dataclass

from .kalshi.feed import MarketSnapshot
from .paper.broker import PaperBroker
from .risk.sizing import Sizer
from .strategy.base import Signal, Strategy

log = logging.getLogger("kalshi_bot.engine")


@dataclass
class Engine:
    strategy: Strategy
    sizer: Sizer
    broker: PaperBroker

    def _size(self, signal: Signal) -> int:
        if signal.fair_value_cents is not None:
            prob = signal.fair_value_cents / 100.0
            n = self.sizer.contracts_for(prob, signal.price_cents)
            if n > 0:
                return n
        return self.sizer.flat_contracts(signal.price_cents)

    def run(self, feed: Iterator[MarketSnapshot]) -> PaperBroker:
        """Consume a feed to exhaustion, paper-trading the strategy's signals."""
        marks: dict[str, float] = {}
        for snap in feed:
            marks[snap.ticker] = snap.yes_mid
            signal = self.strategy.on_snapshot(snap)
            if signal is None:
                continue

            contracts = self._size(signal)
            if contracts <= 0:
                continue

            fill = self.broker.execute(
                ticker=signal.ticker,
                side=signal.side,
                action=signal.action,
                contracts=contracts,
                price_cents=signal.price_cents,
            )
            log.info(
                "%s %s %s x%d @ %dc  (%s)  realized=$%+.2f",
                fill.action,
                fill.side,
                fill.ticker,
                fill.contracts,
                fill.price_cents,
                signal.reason,
                fill.realized_pnl_usd,
            )

        log.info("done: %s", self.broker.summary(marks))
        return self.broker
