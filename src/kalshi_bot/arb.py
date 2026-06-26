"""Arbitrage runner: scan a feed for partition arbitrage and execute the legs.

This is separate from the single-signal :class:`~kalshi_bot.engine.Engine`
because an arbitrage is inherently *multi-leg*: every leg must be executed for
the position to be riskless.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator

from .execution.base import Broker
from .kalshi.feed import MarketSnapshot
from .strategy.arbitrage import ArbitrageDetector, ArbOpportunity

log = logging.getLogger("kalshi_bot.arb")


def _signature(opp: ArbOpportunity) -> tuple:
    return (opp.kind, tuple((s.ticker, s.price_cents) for s in opp.legs))


def run_arbitrage(
    feed: Iterator[MarketSnapshot],
    detector: ArbitrageDetector,
    broker: Broker,
    *,
    contracts_per_set: int = 1,
) -> list[ArbOpportunity]:
    """Consume ``feed``, executing every detected arbitrage's legs on ``broker``.

    Returns the list of opportunities taken. A standing opportunity with an
    unchanged price signature is executed only once (until it changes or
    disappears) to avoid spamming duplicate orders.
    """
    taken: list[ArbOpportunity] = []
    last_sig: tuple | None = None

    for snap in feed:
        detector.update(snap.ticker, snap.yes_bid, snap.yes_ask)
        opportunities = detector.scan()
        if not opportunities:
            # Opportunity gone — allow an identical one to be re-taken if it returns.
            last_sig = None
            continue
        for opp in opportunities:
            sig = _signature(opp)
            if sig == last_sig:
                continue
            last_sig = sig
            for leg in opp.legs:
                broker.execute(
                    ticker=leg.ticker,
                    side=leg.side,
                    action=leg.action,
                    contracts=contracts_per_set,
                    price_cents=leg.price_cents,
                )
            taken.append(opp)
            log.info("ARB taken — %s", opp.reason)

    log.info("arbitrage scan complete: %d opportunities taken", len(taken))
    return taken
