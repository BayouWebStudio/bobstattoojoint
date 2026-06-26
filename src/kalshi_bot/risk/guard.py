"""Risk guard and kill-switch.

The :class:`RiskGuard` is consulted by the engine *before* every order and
updated *after* every fill. It enforces three independent limits and a manual
stop. Tripping any hard limit latches :attr:`tripped`, after which the engine
halts trading for the session.

Limits:

- **max_position_contracts** — per-market absolute net YES exposure (soft: the
  individual order is rejected, trading continues).
- **max_daily_loss_usd** — cumulative realized loss for the session (hard:
  trips the kill-switch).
- **manual stop** — a sentinel file or an explicit :meth:`stop` call (hard).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class RiskGuard:
    max_position_contracts: int = 1_000
    max_daily_loss_usd: float = 100.0
    stop_file: str | None = None

    realized_pnl_usd: float = 0.0
    _net_position: dict[str, int] = field(default_factory=dict)
    _stopped: bool = False

    @property
    def tripped(self) -> bool:
        """True once a hard limit (loss or manual stop) has latched."""
        if self._stopped:
            return True
        if self.realized_pnl_usd <= -abs(self.max_daily_loss_usd):
            return True
        if self.stop_file and Path(self.stop_file).exists():
            self._stopped = True
            return True
        return False

    def stop(self) -> None:
        """Manually latch the kill-switch."""
        self._stopped = True

    def check(self, ticker: str, contracts: int, price_cents: int) -> tuple[bool, str]:
        """Return ``(allowed, reason)`` for a proposed order."""
        if self.tripped:
            return False, "kill-switch tripped"

        projected = abs(self._net_position.get(ticker, 0)) + contracts
        if projected > self.max_position_contracts:
            return (
                False,
                f"position cap: {projected} > {self.max_position_contracts} on {ticker}",
            )
        return True, "ok"

    def record_fill(
        self, ticker: str, contracts: int, action: str, side: str, realized_pnl_usd: float
    ) -> None:
        """Update internal state after a fill."""
        self.realized_pnl_usd += realized_pnl_usd
        increases = (side == "yes" and action == "buy") or (side == "no" and action == "sell")
        delta = contracts if increases else -contracts
        self._net_position[ticker] = self._net_position.get(ticker, 0) + delta
