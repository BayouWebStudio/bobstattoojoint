"""Cross-venue arbitrage execution.

A cross-venue arb has two legs on two independent venues (buy YES on one, buy NO
on the other). The two order books cannot be hit atomically, so the central
risk is **leg risk**: the first leg fills, the second fails, and you are left
holding a naked position instead of a hedged $1-payout set.

:class:`CrossVenueExecutor` mitigates this the standard way:

1. Pre-flight both legs against the :class:`~kalshi_bot.risk.guard.RiskGuard`.
2. Place the first leg. If it fails, abort with no exposure.
3. Place the second leg. If it fails, immediately **unwind** the first leg.
4. If the unwind itself fails, surface a critical "exposed" status so an
   operator can intervene.

True cross-venue *live* trading additionally needs order placement on both
venues — Kalshi is supported via :class:`~kalshi_bot.execution.live.LiveBroker`,
while Polymarket order placement (EIP-712 signing + Polygon settlement) is not
yet implemented, so live runs pair a live Kalshi broker with a paper Polymarket
broker only for simulation.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from ..risk.guard import RiskGuard
from ..strategy.crossvenue import CrossVenueArb
from .base import Broker, Fill

log = logging.getLogger("kalshi_bot.execution.crossvenue")


@dataclass
class ExecutionResult:
    arb: CrossVenueArb
    contracts: int
    status: str  # "filled" | "unwound" | "failed" | "exposed" | "halted"
    fills: list[Fill]
    locked_profit_usd: float


@dataclass
class CrossVenueExecutor:
    brokers: dict[str, Broker]
    guard: RiskGuard | None = None
    locked_profit_usd: float = 0.0
    results: list[ExecutionResult] = field(default_factory=list)

    def _record(self, fills: list[Fill]) -> None:
        if self.guard is None:
            return
        for f in fills:
            self.guard.record_fill(f.ticker, f.contracts, f.action, f.side, f.realized_pnl_usd)

    def execute(self, arb: CrossVenueArb, contracts: int = 1) -> ExecutionResult:
        yes_broker = self.brokers.get(arb.yes_venue)
        no_broker = self.brokers.get(arb.no_venue)
        if yes_broker is None or no_broker is None:
            result = ExecutionResult(arb, 0, "failed", [], 0.0)
            self.results.append(result)
            log.error("no broker for %s/%s", arb.yes_venue, arb.no_venue)
            return result

        # 1. Pre-flight both legs against the risk guard.
        if self.guard is not None:
            if self.guard.tripped:
                return self._halted(arb)
            ok_yes, reason_yes = self.guard.check(arb.yes_market_id, contracts, arb.yes_price_cents)
            ok_no, reason_no = self.guard.check(arb.no_market_id, contracts, arb.no_price_cents)
            if not (ok_yes and ok_no):
                log.warning("xarb blocked by guard: %s / %s", reason_yes, reason_no)
                return self._halted(arb)

        # 2. First leg (YES). No exposure if it fails.
        try:
            yes_fill = yes_broker.execute(
                arb.yes_market_id, "yes", "buy", contracts, arb.yes_price_cents
            )
        except Exception as exc:  # noqa: BLE001 - broker errors are opaque
            log.error("YES leg failed on %s (%s) — no exposure taken", arb.yes_venue, exc)
            result = ExecutionResult(arb, 0, "failed", [], 0.0)
            self.results.append(result)
            return result

        # 3. Second leg (NO). Unwind the first leg if it fails.
        try:
            no_fill = no_broker.execute(
                arb.no_market_id, "no", "buy", contracts, arb.no_price_cents
            )
        except Exception as exc:  # noqa: BLE001
            log.error("NO leg failed on %s (%s) — unwinding YES leg", arb.no_venue, exc)
            return self._unwind(arb, contracts, yes_broker, [yes_fill])

        # 4. Both legs filled — the set is hedged and the spread is locked.
        fills = [yes_fill, no_fill]
        locked = arb.profit_cents / 100.0 * contracts
        self.locked_profit_usd += locked
        self._record(fills)
        log.info("XARB filled x%d — locked $%.2f (%s)", contracts, locked, arb.reason)
        result = ExecutionResult(arb, contracts, "filled", fills, locked)
        self.results.append(result)
        return result

    def _unwind(
        self, arb: CrossVenueArb, contracts: int, yes_broker: Broker, fills: list[Fill]
    ) -> ExecutionResult:
        status = "unwound"
        try:
            unwind = yes_broker.execute(
                arb.yes_market_id, "yes", "sell", contracts, arb.yes_price_cents
            )
            fills = [*fills, unwind]
        except Exception as exc:  # noqa: BLE001
            log.critical(
                "UNWIND FAILED — NAKED YES EXPOSURE on %s (%s): %s",
                arb.yes_venue, arb.yes_market_id, exc,
            )
            status = "exposed"
            if self.guard is not None:
                self.guard.stop()  # latch the kill-switch on naked exposure
        self._record(fills)
        result = ExecutionResult(arb, contracts, status, fills, 0.0)
        self.results.append(result)
        return result

    def _halted(self, arb: CrossVenueArb) -> ExecutionResult:
        result = ExecutionResult(arb, 0, "halted", [], 0.0)
        self.results.append(result)
        return result

    def summary(self) -> str:
        by_status: dict[str, int] = {}
        for r in self.results:
            by_status[r.status] = by_status.get(r.status, 0) + 1
        return f"locked=${self.locked_profit_usd:+.2f} executions={by_status}"
