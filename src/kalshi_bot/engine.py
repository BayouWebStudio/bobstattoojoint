"""The trading engine: feed -> strategy -> risk -> broker.

The engine is mode-agnostic. In paper mode it routes fills to a
:class:`PaperBroker`; in live mode it would route to the Kalshi client (gated
behind an explicit flag and not enabled by default).
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from dataclasses import dataclass

from .execution.base import Broker, Fill
from .kalshi.feed import MarketSnapshot
from .risk.guard import RiskGuard
from .risk.sizing import Sizer
from .strategy.base import Signal, Strategy

log = logging.getLogger("kalshi_bot.engine")


class KillSwitchTripped(RuntimeError):
    """Raised by :meth:`Engine.step` when the kill-switch latches."""


@dataclass
class Engine:
    strategy: Strategy
    sizer: Sizer
    broker: Broker
    guard: RiskGuard | None = None

    def _size(self, signal: Signal) -> int:
        if signal.fair_value_cents is not None:
            prob = signal.fair_value_cents / 100.0
            n = self.sizer.contracts_for(prob, signal.price_cents)
            if n > 0:
                return n
        return self.sizer.flat_contracts(signal.price_cents)

    def step(self, snap: MarketSnapshot) -> Fill | None:
        """Process one snapshot. Returns the fill if a trade was made.

        Raises :class:`KillSwitchTripped` if a hard risk limit latches.
        """
        signal = self.strategy.on_snapshot(snap)
        if signal is None:
            return None

        contracts = self._size(signal)
        if contracts <= 0:
            return None

        if self.guard is not None:
            allowed, reason = self.guard.check(signal.ticker, contracts, signal.price_cents)
            if not allowed:
                if self.guard.tripped:
                    raise KillSwitchTripped(reason)
                log.warning("blocked by risk guard: %s", reason)
                return None

        fill = self.broker.execute(
            ticker=signal.ticker,
            side=signal.side,
            action=signal.action,
            contracts=contracts,
            price_cents=signal.price_cents,
        )
        if self.guard is not None:
            self.guard.record_fill(
                fill.ticker, contracts, signal.action, signal.side, fill.realized_pnl_usd
            )
        log.info(
            "%s %s %s x%d @ %dc  (%s)  realized=$%+.2f",
            fill.action, fill.side, fill.ticker, fill.contracts, fill.price_cents,
            signal.reason, fill.realized_pnl_usd,
        )
        return fill

    def run(self, feed: Iterator[MarketSnapshot]) -> Broker:
        """Consume a feed to exhaustion, trading the strategy's signals."""
        marks: dict[str, float] = {}
        for snap in feed:
            marks[snap.ticker] = snap.yes_mid
            try:
                self.step(snap)
            except KillSwitchTripped as exc:
                log.error("kill-switch tripped — halting: %s", exc)
                break

        summary = getattr(self.broker, "summary", None)
        if callable(summary):
            log.info("done: %s", summary(marks))
        return self.broker
