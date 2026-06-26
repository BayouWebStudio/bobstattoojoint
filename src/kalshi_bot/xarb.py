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

from .strategy.crossvenue import CrossVenueArb, find_cross_venue_arb
from .venues.base import Quote, Venue

log = logging.getLogger("kalshi_bot.xarb")

# A link maps an event name to the per-venue market id: {venue_name: market_id}.
Link = tuple[str, dict[str, str]]


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
    threshold_cents: int = 1,
    rounds: int = 1,
    interval_s: float = 2.0,
) -> list[CrossVenueArb]:
    """Scan ``rounds`` times, sleeping ``interval_s`` between rounds."""
    all_found: list[CrossVenueArb] = []
    for r in range(rounds):
        all_found.extend(scan_once(links, venues, threshold_cents))
        if r < rounds - 1:
            time.sleep(interval_s)
    log.info("cross-venue scan complete: %d opportunities across %d rounds",
             len(all_found), rounds)
    return all_found
