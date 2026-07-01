"""Paper forward test of forecast-value betting on Polymarket global weather.

Read-only (Polymarket trading needs crypto): scans live temperature events, bets
the side the forecast favours when it differs from the market, records paper
positions, and settles them via the Gamma API as events resolve. Runs alongside
the Kalshi forward test to accumulate independent out-of-sample evidence.
"""

from __future__ import annotations

import csv
import datetime as dt
import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path

import requests

from ..weather.model import range_probability
from .weather import (
    GAMMA, city_calibration, fetch_forecast_c, fetch_temp_events, markets_from_event,
)

log = logging.getLogger("kalshi_bot.polymarket.forward")

DEFAULT_LEDGER = "data/poly_weather_ledger.json"
RESULTS_LOG = "data/poly_results_log.csv"


@dataclass
class PolyPosition:
    market_id: str
    event_slug: str
    city: str
    weather_date: str
    bin_title: str
    side: str               # "yes" or "no"
    entry_cost_cents: float  # what we paid for the chosen side
    market_yes: float        # market YES price at entry (0-1)
    model_prob: float        # forecast P(bin wins)
    edge_cents: float
    contracts: int
    opened_date: str
    close_time: str
    status: str = "open"
    result_yes: bool | None = None   # did the bin resolve YES
    realized_pnl_cents: float | None = None


class PolyLedger:
    def __init__(self, path: str = DEFAULT_LEDGER):
        self.path = Path(path)
        self.positions: list[PolyPosition] = []
        if self.path.exists():
            raw = json.loads(self.path.read_text())
            self.positions = [PolyPosition(**p) for p in raw.get("positions", [])]

    def held(self) -> set[str]:
        return {p.market_id for p in self.positions}

    def open_positions(self) -> list[PolyPosition]:
        return [p for p in self.positions if p.status == "open"]

    def settled_positions(self) -> list[PolyPosition]:
        return [p for p in self.positions if p.status == "settled"]

    def add(self, p: PolyPosition) -> None:
        self.positions.append(p)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"positions": [asdict(p) for p in self.positions]}, indent=2))

    def summary(self) -> str:
        settled = self.settled_positions()
        realized = sum(p.realized_pnl_cents or 0 for p in settled)
        cap = sum(p.entry_cost_cents for p in settled)
        wins = sum(1 for p in settled if (p.realized_pnl_cents or 0) > 0)
        roi = (100 * realized / cap) if cap else 0.0
        return (f"open={len(self.open_positions())} settled={len(settled)} "
                f"realized={realized:+.1f}c ROI={roi:+.1f}% "
                f"win={(wins/len(settled)) if settled else 0:.0%}")


def scan_poly_weather(
    ledger: PolyLedger,
    *,
    today: str,
    session: requests.Session | None = None,
    min_lead_days: int = 1,
    max_lead_days: int = 3,
    min_edge_cents: float = 4.0,
    max_edge_cents: float = 15.0,
    min_volume: float = 2000.0,
    contracts: int = 10,
    max_new: int = 20,
    calibrate: bool = True,
) -> list[PolyPosition]:
    """Open the best forecast-value bet per live temperature event.

    Guards against betting our own forecast error against a sharp market:
    requires bin liquidity (``min_volume``) and **caps the edge**
    (``max_edge_cents``) — a gap larger than the backtest's plausible band is far
    more likely a model error (wrong city bias) than real alpha on Polymarket.
    """
    session = session or requests.Session()
    held = ledger.held()
    today_d = dt.date.fromisoformat(today)
    lo, hi = today_d + dt.timedelta(days=min_lead_days), today_d + dt.timedelta(days=max_lead_days)

    cal_cache: dict[str, tuple[float, float]] = {}
    candidates: list[PolyPosition] = []
    for event in fetch_temp_events(session, closed=False):
        mk = markets_from_event(event)
        if not mk:
            continue
        city, date = mk[0].city, mk[0].date
        try:
            wd = dt.date.fromisoformat(date)
        except ValueError:
            continue
        if not (lo <= wd <= hi):
            continue
        try:
            fc = fetch_forecast_c(city, date, session)
        except Exception:
            fc = None
        if fc is None:
            continue
        mean, sigma = fc
        # Bias-correct per city against its resolution source (removes spurious
        # edges that are really our gridpoint's systematic forecast error).
        if calibrate:
            if city not in cal_cache:
                try:
                    cal_cache[city] = city_calibration(city, session, today=today)
                except Exception:
                    cal_cache[city] = (0.0, sigma)
            bias, cal_sigma = cal_cache[city]
            mean -= bias
            sigma = cal_sigma

        best: PolyPosition | None = None
        best_edge = min_edge_cents
        for pm in mk:
            if pm.market_id in held or pm.volume < min_volume:
                continue
            prob = range_probability(pm.rng, mean, sigma)
            # EV at EXECUTABLE prices: buying YES fills at the ask, buying NO at
            # (1 - bid). A missing side means no resting quote — skip that side
            # rather than pretending the stale last-trade price is fillable.
            candidates_sides: list[tuple[str, float, float]] = []
            if pm.best_ask is not None and pm.best_ask * 100.0 >= 1.0:
                # <1c asks are extreme model-vs-market disagreements (100x);
                # against a sharp book that is our error, not theirs.
                yes_cost = pm.best_ask * 100.0
                candidates_sides.append(("yes", yes_cost, prob * 100.0 - yes_cost))
            if pm.best_bid is not None:
                no_cost = (1.0 - pm.best_bid) * 100.0
                candidates_sides.append(("no", no_cost, (1 - prob) * 100.0 - no_cost))
            if not candidates_sides:
                continue
            side, cost, edge = max(candidates_sides, key=lambda x: x[2])
            if edge > max_edge_cents:   # implausible vs a sharp market -> model error, skip
                continue
            if edge >= best_edge:
                best_edge = edge
                best = PolyPosition(
                    market_id=pm.market_id or pm.token_id or pm.bin_title,
                    event_slug=event.get("slug", ""), city=city, weather_date=date,
                    bin_title=pm.bin_title, side=side, entry_cost_cents=round(cost, 1),
                    market_yes=round(pm.yes_price, 4), model_prob=round(prob, 4),
                    edge_cents=round(edge, 1), contracts=contracts, opened_date=today,
                    close_time=(event.get("endDate") or "")[:19],
                )
        if best is not None:
            candidates.append(best)

    candidates.sort(key=lambda p: p.close_time or "9999")
    opened = candidates[:max_new]
    for p in opened:
        ledger.add(p)
    if opened:
        ledger.save()
    return opened


def settle_poly(ledger: PolyLedger, *, session: requests.Session | None = None,
                results_log: str = RESULTS_LOG) -> int:
    """Settle open positions whose Polymarket event has resolved."""
    session = session or requests.Session()
    changed = 0
    newly: list[PolyPosition] = []
    # group open positions by event slug to minimize fetches
    by_slug: dict[str, list[PolyPosition]] = {}
    for p in ledger.open_positions():
        by_slug.setdefault(p.event_slug, []).append(p)

    for slug, positions in by_slug.items():
        try:
            r = session.get(f"{GAMMA}/events", params={"slug": slug}, timeout=20)
            events = r.json() if r.status_code == 200 else []
        except Exception:
            continue
        event = events[0] if events else None
        if not event or not event.get("closed"):
            continue
        outcome_by_bin = {}
        for m in event.get("markets") or []:
            try:
                pr = json.loads(m.get("outcomePrices") or "[]")
            except (ValueError, TypeError):
                pr = []
            if len(pr) >= 2 and pr[0] in ("0", "1"):
                outcome_by_bin[m.get("groupItemTitle") or ""] = (pr[0] == "1")
        for p in positions:
            if p.bin_title not in outcome_by_bin:
                continue
            bin_won = outcome_by_bin[p.bin_title]
            win = bin_won if p.side == "yes" else (not bin_won)
            p.result_yes = bin_won
            p.realized_pnl_cents = (100.0 if win else 0.0) - p.entry_cost_cents
            p.status = "settled"
            changed += 1
            newly.append(p)
            log.info("settled %s %s %s -> %s P&L %+.1fc", p.city, p.bin_title, p.side,
                     "win" if win else "loss", p.realized_pnl_cents)
    if changed:
        ledger.save()
        _append_results(newly, results_log)
    return changed


_FIELDS = ["city", "weather_date", "bin_title", "side", "entry_cost_cents", "market_yes",
           "model_prob", "edge_cents", "contracts", "result_yes", "realized_pnl_cents",
           "opened_date", "close_time"]


def _append_results(positions: list[PolyPosition], path: str) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    exists = p.exists()
    with p.open("a", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=_FIELDS, extrasaction="ignore")
        if not exists:
            w.writeheader()
        for pos in positions:
            w.writerow(asdict(pos))
