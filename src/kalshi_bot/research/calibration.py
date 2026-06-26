"""Calibration analysis and settlement-aware edge backtesting.

A :class:`SettlementSample` pairs the price you could have entered at with the
market's realized binary outcome. From a set of these we can:

- build a **calibration table** (does a contract priced at X% resolve YES X% of
  the time?), and
- **backtest** a hold-to-settlement strategy with realistic Kalshi fees, getting
  ROI, expected value per market, win rate, and a t-statistic.

This is how the favorite-longshot edge was found and validated; see the module
``strategy.fade_longshot`` for the deployable strategy.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class SettlementSample:
    """An entry quote (cents) for a market and its realized outcome."""

    ticker: str
    yes_bid: int
    yes_ask: int
    result_yes: bool  # True if the market resolved YES

    @property
    def yes_mid(self) -> float:
        return (self.yes_bid + self.yes_ask) / 2


def kalshi_fee_cents(price_cents: float, contracts: int = 1) -> float:
    """Kalshi trading fee in cents: ceil(0.07 * C * P * (1-P)), P in dollars.

    This is Kalshi's general fee schedule (some series are cheaper at 0.035);
    0.07 is the conservative choice. Fees are largest near 50c and small for
    deep longshots, which is why the fade-longshot edge survives them.
    """
    p = price_cents / 100.0
    return math.ceil(0.07 * contracts * p * (1.0 - p) * 100.0)


@dataclass
class CalibrationBucket:
    low: int
    high: int
    n: int
    yes_count: int

    @property
    def implied_pct(self) -> float:
        return (self.low + self.high + 1) / 2.0  # midpoint of the band

    @property
    def realized_pct(self) -> float:
        return 100.0 * self.yes_count / self.n if self.n else 0.0

    @property
    def gap(self) -> float:
        """Realized minus implied. Negative => overpriced (longshot bias)."""
        return self.realized_pct - self.implied_pct


def calibration_table(samples: list[SettlementSample], width: int = 10) -> list[CalibrationBucket]:
    """Bucket samples by entry mid-price and report realized YES frequency."""
    buckets: dict[int, list[int]] = {}
    for s in samples:
        b = int(s.yes_mid // width) * width
        agg = buckets.setdefault(b, [0, 0])
        agg[0] += 1
        agg[1] += int(s.result_yes)
    return [
        CalibrationBucket(b, b + width - 1, n, y)
        for b, (n, y) in sorted(buckets.items())
    ]


@dataclass
class BacktestStats:
    n: int
    roi_pct: float
    ev_per_market_cents: float
    win_rate: float
    t_stat: float

    def __str__(self) -> str:
        return (
            f"n={self.n} ROI={self.roi_pct:+.1f}% EV/mkt={self.ev_per_market_cents:+.2f}c "
            f"win={self.win_rate:.0%} t={self.t_stat:+.2f}"
        )


def fade_longshot_backtest(
    samples: list[SettlementSample],
    *,
    max_yes_ask: int = 15,
    min_yes_ask: int = 2,
    entry: str = "mid",
    fee_fn=kalshi_fee_cents,
) -> BacktestStats:
    """Backtest buying NO on YES longshots and holding to settlement.

    ``entry`` is ``"mid"`` (passive: pay the NO mid = 100 - yes_mid) or ``"ask"``
    (aggressive: cross the spread, pay 100 - yes_bid). Returns per-market EV, ROI
    on capital deployed, win rate, and a t-statistic on per-market P&L.

    Caveat the t-stat overstates significance when outcomes cluster within a few
    events (e.g. many candidates in one election) — treat it as directional.
    """
    pnl: list[float] = []
    capital = 0.0
    for s in samples:
        if not min_yes_ask <= s.yes_ask <= max_yes_ask:
            continue
        no_cost = (100 - s.yes_bid) if entry == "ask" else (100 - s.yes_mid)
        payoff = 0.0 if s.result_yes else 100.0  # NO pays 100 if market resolves NO
        pnl.append(payoff - no_cost - fee_fn(no_cost))
        capital += no_cost

    n = len(pnl)
    if n == 0 or capital == 0:
        return BacktestStats(0, 0.0, 0.0, 0.0, 0.0)

    total = sum(pnl)
    mean = total / n
    if n > 1:
        sd = math.sqrt(sum((x - mean) ** 2 for x in pnl) / (n - 1))
        t_stat = mean / (sd / math.sqrt(n)) if sd > 0 else 0.0
    else:
        t_stat = 0.0
    wins = sum(1 for x in pnl if x > 0)
    return BacktestStats(
        n=n,
        roi_pct=100.0 * total / capital,
        ev_per_market_cents=mean,
        win_rate=wins / n,
        t_stat=t_stat,
    )
