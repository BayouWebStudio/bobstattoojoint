"""Cross-venue arbitrage between two-sided quotes on the same event.

If the *same* binary event trades on multiple venues, you can lock a guaranteed
$1 payout by holding one YES and one NO of that event. Buy the YES wherever it
is cheapest and the NO wherever it is cheapest — possibly different venues. If

    best_yes_ask  +  best_no_ask  <  100c

the combined cost is under a dollar while the payout is exactly a dollar, so the
difference is riskless profit (before fees and execution risk).

This is the highest-value prediction-market strategy because the two venues are
independent order books that routinely disagree on price.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..venues.base import Quote


@dataclass(frozen=True)
class CrossVenueArb:
    event: str
    yes_venue: str
    yes_price_cents: int
    no_venue: str
    no_price_cents: int
    cost_cents: int
    profit_cents: int

    @property
    def reason(self) -> str:
        return (
            f"buy YES@{self.yes_venue} {self.yes_price_cents}c + "
            f"NO@{self.no_venue} {self.no_price_cents}c = {self.cost_cents}c "
            f"-> {self.profit_cents}c/set guaranteed"
        )


def find_cross_venue_arb(
    event: str, quotes: list[Quote], threshold_cents: int = 1
) -> CrossVenueArb | None:
    """Find the cheapest YES + cheapest NO across venues for one event.

    ``quotes`` are quotes for the *same* event from different venues. Returns a
    :class:`CrossVenueArb` when the combined cost clears ``threshold_cents`` of
    guaranteed profit, else ``None``.
    """
    if len(quotes) < 1:
        return None

    best_yes = min(quotes, key=lambda q: q.yes_ask)
    best_no = min(quotes, key=lambda q: q.no_ask)

    cost = best_yes.yes_ask + best_no.no_ask
    profit = 100 - cost
    if profit < threshold_cents:
        return None

    return CrossVenueArb(
        event=event,
        yes_venue=best_yes.venue,
        yes_price_cents=best_yes.yes_ask,
        no_venue=best_no.venue,
        no_price_cents=best_no.no_ask,
        cost_cents=cost,
        profit_cents=profit,
    )
