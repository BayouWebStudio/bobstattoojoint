"""A paper broker that simulates fills and tracks P&L.

Orders fill immediately at their limit price (an optimistic but useful baseline;
a future version can model partial fills and queue position). Positions are
tracked per ticker in *signed YES contracts*: positive = long YES, negative =
short YES. P&L is realized when a position is reduced or closed.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..execution.base import Fill


@dataclass
class Position:
    contracts: int = 0  # signed: + long YES, - short YES
    avg_price_cents: float = 0.0  # average entry price of the open position


@dataclass
class PaperBroker:
    starting_cash_usd: float = 1000.0
    cash_usd: float = field(init=False)
    realized_pnl_usd: float = 0.0
    positions: dict[str, Position] = field(default_factory=dict)
    fills: list[Fill] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.cash_usd = self.starting_cash_usd

    def _signed_delta(self, side: str, action: str, contracts: int) -> int:
        """Convert a (side, action) order into a signed change in YES contracts.

        Buying YES or selling NO increases YES exposure; selling YES or buying
        NO decreases it.
        """
        increases = (side == "yes" and action == "buy") or (
            side == "no" and action == "sell"
        )
        return contracts if increases else -contracts

    def execute(
        self, ticker: str, side: str, action: str, contracts: int, price_cents: int
    ) -> Fill:
        """Apply an order to the paper book and return the resulting fill."""
        if contracts <= 0:
            raise ValueError("contracts must be positive")

        pos = self.positions.setdefault(ticker, Position())
        delta = self._signed_delta(side, action, contracts)
        # Price of the YES contract implied by this order, in cents.
        yes_price = price_cents if side == "yes" else 100 - price_cents

        realized = 0.0
        old = pos.contracts
        new = old + delta

        if old == 0 or (old > 0) == (delta > 0):
            # Opening or adding in the same direction: blend the entry price.
            total = abs(old) + abs(delta)
            pos.avg_price_cents = (
                pos.avg_price_cents * abs(old) + yes_price * abs(delta)
            ) / total
        else:
            # Reducing or flipping: realize P&L on the closed quantity.
            closed = min(abs(old), abs(delta))
            direction = 1 if old > 0 else -1
            realized = direction * (yes_price - pos.avg_price_cents) * closed / 100.0
            if (old > 0 and new < 0) or (old < 0 and new > 0):
                # Flipped through zero: remainder opens a new position.
                pos.avg_price_cents = yes_price
            elif new == 0:
                pos.avg_price_cents = 0.0
            # else: partial reduction keeps the original avg price.

        pos.contracts = new
        self.realized_pnl_usd += realized
        self.cash_usd += realized

        fill = Fill(ticker, side, action, contracts, price_cents, realized)
        self.fills.append(fill)
        return fill

    def unrealized_pnl_usd(self, marks: dict[str, float]) -> float:
        """Mark-to-market open positions given current YES mid-prices (cents)."""
        total = 0.0
        for ticker, pos in self.positions.items():
            if pos.contracts == 0 or ticker not in marks:
                continue
            total += pos.contracts * (marks[ticker] - pos.avg_price_cents) / 100.0
        return total

    def summary(self, marks: dict[str, float] | None = None) -> str:
        unreal = self.unrealized_pnl_usd(marks or {})
        open_pos = {t: p.contracts for t, p in self.positions.items() if p.contracts}
        return (
            f"fills={len(self.fills)} realized=${self.realized_pnl_usd:+.2f} "
            f"unrealized=${unreal:+.2f} cash=${self.cash_usd:.2f} positions={open_pos}"
        )
