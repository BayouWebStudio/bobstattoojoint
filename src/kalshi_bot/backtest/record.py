"""Record and replay market snapshots as CSV.

A backtest needs historical data. Until a live recorder is run against Kalshi,
backtests can replay any synthetic feed written to CSV. The format is a simple
header + one row per snapshot: ``ticker,yes_bid,yes_ask,ts``.
"""

from __future__ import annotations

import csv
from collections.abc import Iterable, Iterator
from pathlib import Path

from ..kalshi.feed import MarketSnapshot

_FIELDS = ["ticker", "yes_bid", "yes_ask", "ts"]


def write_snapshots(path: str | Path, snapshots: Iterable[MarketSnapshot]) -> int:
    """Write snapshots to ``path`` as CSV. Returns the number of rows written."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(_FIELDS)
        for s in snapshots:
            writer.writerow([s.ticker, s.yes_bid, s.yes_ask, s.ts])
            count += 1
    return count


def read_snapshots(path: str | Path) -> Iterator[MarketSnapshot]:
    """Stream snapshots back from a CSV written by :func:`write_snapshots`."""
    with Path(path).open(newline="") as fh:
        for row in csv.DictReader(fh):
            yield MarketSnapshot(
                ticker=row["ticker"],
                yes_bid=int(row["yes_bid"]),
                yes_ask=int(row["yes_ask"]),
                ts=float(row["ts"]),
            )


def record_feed(
    feed: Iterable[MarketSnapshot], path: str | Path
) -> Iterator[MarketSnapshot]:
    """Tee a live feed to CSV while still yielding snapshots to the caller."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(_FIELDS)
        for s in feed:
            writer.writerow([s.ticker, s.yes_bid, s.yes_ask, s.ts])
            yield s
