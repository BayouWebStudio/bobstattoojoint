"""Kelly-criterion position sizing with safety limits.

For a binary contract bought at price ``c`` (in dollars, 0<c<1) with estimated
true probability ``q`` of paying out $1, the full-Kelly fraction of bankroll to
risk is::

    f* = (q - c) / (1 - c)

We apply a fractional-Kelly multiplier (default 1/4) because full Kelly is
notoriously aggressive and our probability estimates are noisy, then cap the
result by an absolute per-position dollar limit.
"""

from __future__ import annotations

from dataclasses import dataclass


def kelly_fraction(prob: float, price_cents: float) -> float:
    """Full-Kelly bankroll fraction for buying a YES contract.

    Returns 0 when there is no positive edge. ``prob`` is the estimated win
    probability (0-1); ``price_cents`` is the buy price in cents (1-99).
    """
    c = price_cents / 100.0
    if not 0.0 < c < 1.0:
        return 0.0
    f = (prob - c) / (1.0 - c)
    return max(0.0, f)


@dataclass(frozen=True)
class Sizer:
    """Turns an edge estimate into a contract count, respecting limits."""

    bankroll_usd: float
    max_position_usd: float
    kelly_multiplier: float = 0.25
    default_contracts: int = 1

    def contracts_for(self, prob: float, price_cents: float) -> int:
        """Number of contracts to trade given win prob and price (cents)."""
        price_usd = price_cents / 100.0
        if price_usd <= 0:
            return 0

        f = kelly_fraction(prob, price_cents) * self.kelly_multiplier
        if f <= 0:
            return 0

        budget = min(self.bankroll_usd * f, self.max_position_usd)
        return int(budget // price_usd)

    def flat_contracts(self, price_cents: float) -> int:
        """Fallback sizing when no probability estimate is available."""
        price_usd = price_cents / 100.0
        if price_usd <= 0:
            return 0
        budget = min(
            self.default_contracts * price_usd,
            self.max_position_usd,
            self.bankroll_usd,
        )
        return int(budget // price_usd)
