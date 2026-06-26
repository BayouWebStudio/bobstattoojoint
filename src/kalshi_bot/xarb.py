"""Cross-venue arbitrage runner.

Given *links* — events that trade on more than one venue — this polls each
venue for a quote and runs the cross-venue detector. Execution is left as
detection-only: capturing a cross-venue arb requires funded accounts on both
venues (and, for Polymarket, on-chain settlement), so this reports and tallies
guaranteed profit rather than placing orders.
"""

from __future__ import annotations

import logging
import time

from .execution.crossvenue import CrossVenueExecutor
from .strategy.crossvenue import CrossVenueArb, find_cross_venue_arb
from .venues.base import Quote, Venue

log = logging.getLogger("kalshi_bot.xarb")

# A link maps an event name to the per-venue market id: {venue_name: market_id}.
Link = tuple[str, dict[str, str]]


def _signature(opp: CrossVenueArb) -> tuple:
    return (opp.yes_venue, opp.yes_market_id, opp.yes_price_cents,
            opp.no_venue, opp.no_market_id, opp.no_price_cents)


def scan_once(
    links: list[Link], venues: dict[str, Venue], threshold_cents: int = 1
) -> list[CrossVenueArb]:
    """One pass: quote every venue for every link and detect arbs."""
    found: list[CrossVenueArb] = []
    for event, market_by_venue in links:
        quotes: list[Quote] = []
        for venue_name, market_id in market_by_venue.items():
            venue = venues.get(venue_name)
            if venue is None:
                continue
            q = venue.quote(market_id)
            if q is not None:
                quotes.append(q)
        if len(quotes) < 2:
            continue  # need at least two venues to cross
        opp = find_cross_venue_arb(event, quotes, threshold_cents)
        if opp is not None:
            found.append(opp)
            log.info("XARB %s — %s", event, opp.reason)
    return found


def run_cross_venue(
    links: list[Link],
    venues: dict[str, Venue],
    *,
    executor: CrossVenueExecutor | None = None,
    contracts: int = 1,
    threshold_cents: int = 1,
    rounds: int = 1,
    interval_s: float = 2.0,
) -> list[CrossVenueArb]:
    """Scan ``rounds`` times, sleeping ``interval_s`` between rounds.

    If ``executor`` is given, each newly-appearing opportunity (deduped per
    event by its price signature) is executed as a two-leg hedged set.
    """
    all_found: list[CrossVenueArb] = []
    last_sig: dict[str, tuple] = {}

    for r in range(rounds):
        opps = scan_once(links, venues, threshold_cents)
        found_events = {o.event for o in opps}
        # Reset dedup for events whose opportunity has disappeared.
        for event in [e for e in last_sig if e not in found_events]:
            last_sig.pop(event, None)

        for opp in opps:
            all_found.append(opp)
            if executor is not None:
                sig = _signature(opp)
                if last_sig.get(opp.event) == sig:
                    continue  # same standing opportunity already executed
                last_sig[opp.event] = sig
                executor.execute(opp, contracts)

        if r < rounds - 1:
            time.sleep(interval_s)

    log.info("cross-venue scan complete: %d opportunities across %d rounds",
             len(all_found), rounds)
    return all_found
