"""Compare model probabilities to market prices and surface value bets.

For each market the model gives ``P(YES)``. The market lets us buy YES at the
ask or NO at ``100 - yes_bid``. We take whichever side has positive expected
value beyond a minimum edge (after Kalshi fees). This generalizes fade-longshot:
fading a longshot is just the NO signal when the model says YES is very unlikely
but the market still bids it up.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..research.calibration import kalshi_fee_cents


@dataclass(frozen=True)
class ValueBet:
    ticker: str
    subtitle: str
    model_prob: float       # model P(YES), 0-1
    yes_bid: int
    yes_ask: int
    side: str               # "yes" or "no"
    action: str             # always "buy"
    price_cents: int        # what we pay for the chosen side
    edge_cents: float       # expected value per contract after fees

    @property
    def reason(self) -> str:
        return (
            f"model {self.model_prob*100:.0f}% vs mkt {self.yes_bid}/{self.yes_ask} "
            f"-> buy {self.side.upper()} @ {self.price_cents}c (EV {self.edge_cents:+.1f}c)"
        )


def evaluate(
    ticker: str,
    subtitle: str,
    model_prob: float,
    yes_bid: int,
    yes_ask: int,
    *,
    min_edge_cents: float = 3.0,
) -> ValueBet | None:
    """Return the better of buy-YES / buy-NO if its EV clears ``min_edge_cents``."""
    fair = model_prob * 100.0

    # Buy YES at the ask: pays 100 if YES.
    ev_yes = fair - yes_ask - kalshi_fee_cents(yes_ask)
    # Buy NO at the NO ask (= 100 - yes_bid): pays 100 if NO.
    no_cost = 100 - yes_bid
    ev_no = (100 - fair) - no_cost - kalshi_fee_cents(no_cost)

    if ev_yes >= ev_no and ev_yes >= min_edge_cents:
        return ValueBet(ticker, subtitle, model_prob, yes_bid, yes_ask,
                        "yes", "buy", yes_ask, ev_yes)
    if ev_no > ev_yes and ev_no >= min_edge_cents:
        return ValueBet(ticker, subtitle, model_prob, yes_bid, yes_ask,
                        "no", "buy", no_cost, ev_no)
    return None
