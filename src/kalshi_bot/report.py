"""Consolidated validation scorecard across all paper-trading ledgers.

Reads every ledger, aggregates settled positions per strategy, and reports ROI,
win rate, and — crucially — a significance flag so a big number on a tiny sample
(e.g. +24.9% on n=3) is shown as noise, not a win. This is the internal dashboard
for tracking the forward tests as the data accumulates.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

# (label, ledger path, per-position cost field)
LEDGERS = [
    ("Kalshi event fades", "data/forward_ledger.json", "entry_no_cost"),
    ("Kalshi weather/rain", "data/forward_ledger_weather.json", "entry_no_cost"),
    ("Kalshi daily (legacy)", "data/forward_ledger_daily.json", "entry_no_cost"),
    ("Polymarket weather", "data/poly_weather_ledger.json", "entry_cost_cents"),
]


@dataclass
class GroupStats:
    label: str
    open_n: int
    settled_n: int
    pnl_cents: float
    capital_cents: float
    wins: int
    t_stat: float

    @property
    def roi_pct(self) -> float:
        return 100.0 * self.pnl_cents / self.capital_cents if self.capital_cents else 0.0

    @property
    def win_rate(self) -> float:
        return self.wins / self.settled_n if self.settled_n else 0.0

    @property
    def verdict(self) -> str:
        """Honest significance label — small samples are noise, period."""
        if self.settled_n == 0:
            return "no data yet"
        if self.settled_n < 20:
            return "too few to judge (noise)"
        if abs(self.t_stat) < 2:
            return "not significant"
        return "significant" + (" +" if self.pnl_cents > 0 else " -")

    def line(self) -> str:
        return (f"{self.label:24} open={self.open_n:3} settled={self.settled_n:3} "
                f"ROI={self.roi_pct:+6.1f}% win={self.win_rate:4.0%} "
                f"t={self.t_stat:+5.2f}  [{self.verdict}]")


def _group_stats(label: str, path: str, cost_field: str) -> GroupStats:
    p = Path(path)
    if not p.exists():
        return GroupStats(label, 0, 0, 0.0, 0.0, 0, 0.0)
    positions = json.loads(p.read_text()).get("positions", [])
    open_n = sum(1 for x in positions if x.get("status") == "open")
    pnls, caps = [], []
    for x in positions:
        if x.get("status") != "settled" or x.get("realized_pnl_cents") is None:
            continue
        pnls.append(float(x["realized_pnl_cents"]))
        caps.append(float(x.get(cost_field) or 0))
    n = len(pnls)
    if n == 0:
        return GroupStats(label, open_n, 0, 0.0, 0.0, 0, 0.0)
    mean = sum(pnls) / n
    if n > 1:
        sd = math.sqrt(sum((x - mean) ** 2 for x in pnls) / (n - 1))
        t = mean / (sd / math.sqrt(n)) if sd > 0 else 0.0
    else:
        t = 0.0
    wins = sum(1 for x in pnls if x > 0)
    return GroupStats(label, open_n, n, sum(pnls), sum(caps), wins, t)


def build_report(ledgers=LEDGERS) -> list[GroupStats]:
    return [_group_stats(*spec) for spec in ledgers]


def render(stats: list[GroupStats]) -> str:
    lines = ["Paper-trading validation scorecard", "=" * 78]
    for s in stats:
        lines.append("  " + s.line())
    tot_pnl = sum(s.pnl_cents for s in stats)
    tot_cap = sum(s.capital_cents for s in stats)
    tot_settled = sum(s.settled_n for s in stats)
    tot_open = sum(s.open_n for s in stats)
    roi = 100.0 * tot_pnl / tot_cap if tot_cap else 0.0
    lines.append("-" * 78)
    lines.append(f"  {'TOTAL':24} open={tot_open:3} settled={tot_settled:3} "
                 f"ROI={roi:+6.1f}%  P&L={tot_pnl:+.0f}c")
    lines.append("\nReminder: ROI on <20 settled is noise. Wait for the sample to grow.")
    return "\n".join(lines)
