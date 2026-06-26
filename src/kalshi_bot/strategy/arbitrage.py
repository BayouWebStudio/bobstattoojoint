"""Cross-market arbitrage detection for mutually-exclusive events.

Many Kalshi events are partitions: a set of markets where *exactly one* resolves
YES (e.g. "which candidate wins", "which range will CPI land in"). For such a
group the YES contracts must collectively be worth exactly $1 (100c) at
settlement. That creates two riskless opportunities:

- **Underpriced** — if you can *buy* YES on every outcome for a combined ask of
  less than 100c, you pay < $1 now and are guaranteed to receive exactly $1.
- **Overpriced** — if you can *sell* YES on every outcome (buy NO) for a combined
  bid of more than 100c, you collect > $1 now and pay out exactly $1.

This module finds those opportunities from top-of-book prices. Detection is pure
and unit-tested; execution is left to the caller (paper or live broker).
"""

from __future__ import annotations

from dataclasses import dataclass

from .base import Signal


@dataclass(frozen=True)
class Leg:
    ticker: str
    yes_bid: int
    yes_ask: int


@dataclass(frozen=True)
class ArbOpportunity:
    kind: str  # "underpriced" or "overpriced"
    legs: list[Signal]
    combined_price_cents: int
    profit_per_set_cents: int  # guaranteed profit per 1-contract set

    @property
    def reason(self) -> str:
        return (
            f"{self.kind}: {len(self.legs)} legs combined {self.combined_price_cents}c "
            f"-> {self.profit_per_set_cents}c/set guaranteed"
        )


def find_arbitrage(legs: list[Leg], threshold_cents: int = 1) -> ArbOpportunity | None:
    """Detect an arb across a partition of mutually-exclusive YES contracts.

    ``threshold_cents`` is the minimum guaranteed profit (after the implicit
    cost of crossing the spread) required to report an opportunity. Returns the
    more profitable of the two directions, or ``None``.
    """
    if len(legs) < 2:
        return None

    # Underpriced: buy YES on every leg at its ask.
    total_ask = sum(leg.yes_ask for leg in legs)
    under_profit = 100 - total_ask

    # Overpriced: sell YES on every leg at its bid.
    total_bid = sum(leg.yes_bid for leg in legs)
    over_profit = total_bid - 100

    if under_profit >= threshold_cents and under_profit >= over_profit:
        signals = [
            Signal(ticker=leg.ticker, side="yes", action="buy", price_cents=leg.yes_ask,
                   reason="arb leg (buy YES)")
            for leg in legs
        ]
        return ArbOpportunity("underpriced", signals, total_ask, under_profit)

    if over_profit >= threshold_cents:
        signals = [
            Signal(ticker=leg.ticker, side="yes", action="sell", price_cents=leg.yes_bid,
                   reason="arb leg (sell YES)")
            for leg in legs
        ]
        return ArbOpportunity("overpriced", signals, total_bid, over_profit)

    return None


class ArbitrageDetector:
    """Maintains latest top-of-book per ticker and scans partitions for arbs.

    ``groups`` maps an event name to the list of tickers that partition it.
    """

    def __init__(self, groups: dict[str, list[str]], threshold_cents: int = 1):
        self._groups = groups
        self._threshold = threshold_cents
        self._latest: dict[str, Leg] = {}

    def update(self, ticker: str, yes_bid: int, yes_ask: int) -> None:
        self._latest[ticker] = Leg(ticker, yes_bid, yes_ask)

    def scan(self) -> list[ArbOpportunity]:
        opportunities = []
        for tickers in self._groups.values():
            legs = [self._latest[t] for t in tickers if t in self._latest]
            if len(legs) != len(tickers):
                continue  # incomplete book for this event
            opp = find_arbitrage(legs, self._threshold)
            if opp is not None:
                opportunities.append(opp)
        return opportunities
