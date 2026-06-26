"""Forward (out-of-sample) paper test of the fade-longshot edge.

A backtest can overfit; the honest validation is a *forward* test — pick live
longshots today, record the paper NO entry, and check the realized outcome only
after the market settles in the future. This module persists positions to a JSON
ledger (committed to the repo so it survives the ephemeral container) and lets a
later run settle them and tally out-of-sample P&L.

Workflow:
  forward scan    -> open new paper NO positions on live deep longshots
  forward settle  -> mark any now-settled positions and record realized P&L
  forward status  -> show open positions and running forward performance

Entry is the *aggressive* (cross-the-spread) NO price, ``100 - yes_bid`` — the
conservative case (~+2.5% in-sample), so the forward test does not flatter the
edge. Positions are capped per event for diversification (the key caveat from
the in-sample analysis: outcomes cluster within events).
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path

from .kalshi.client import KalshiClient
from .research.calibration import kalshi_fee_cents

log = logging.getLogger("kalshi_bot.forward")

DEFAULT_LEDGER = "data/forward_ledger.json"


@dataclass
class Position:
    ticker: str
    event_ticker: str
    category: str
    entry_yes_bid: int
    entry_yes_ask: int
    entry_no_cost: int  # what we paid for NO = 100 - yes_bid
    contracts: int
    opened_date: str
    close_time: str
    status: str = "open"            # "open" | "settled"
    result: str | None = None       # "yes" | "no"
    realized_pnl_cents: float | None = None
    # Set for forecast-filtered weather fades:
    model_prob: float | None = None
    weather_date: str | None = None


def market_quote_cents(m: dict) -> tuple[int, int] | None:
    """Extract (yes_bid_cents, yes_ask_cents) from a raw market dict, or None."""
    try:
        yb = round(float(m.get("yes_bid_dollars") or 0) * 100)
        ya = round(float(m.get("yes_ask_dollars") or 0) * 100)
    except (TypeError, ValueError):
        return None
    return yb, ya


class ForwardLedger:
    def __init__(self, path: str = DEFAULT_LEDGER):
        self.path = Path(path)
        self.positions: list[Position] = []
        if self.path.exists():
            raw = json.loads(self.path.read_text())
            self.positions = [Position(**p) for p in raw.get("positions", [])]

    def open_tickers(self) -> set[str]:
        return {p.ticker for p in self.positions}

    def open_positions(self) -> list[Position]:
        return [p for p in self.positions if p.status == "open"]

    def settled_positions(self) -> list[Position]:
        return [p for p in self.positions if p.status == "settled"]

    def add(self, position: Position) -> None:
        self.positions.append(position)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps({"positions": [asdict(p) for p in self.positions]}, indent=2)
        )

    def summary(self) -> str:
        settled = self.settled_positions()
        realized = sum(p.realized_pnl_cents or 0 for p in settled)
        capital = sum(p.entry_no_cost for p in settled)
        wins = sum(1 for p in settled if (p.realized_pnl_cents or 0) > 0)
        roi = (100 * realized / capital) if capital else 0.0
        win_rate = (wins / len(settled)) if settled else 0.0
        return (
            f"open={len(self.open_positions())} settled={len(settled)} "
            f"realized={realized:+.1f}c ROI={roi:+.1f}% win={win_rate:.0%}"
        )


def _raw_candidates_from_events(client: KalshiClient, max_pages: int):
    """Yield (event_ticker, category, market) from the open-events feed."""
    cursor = None
    for _ in range(max_pages):
        params = {"limit": 200, "with_nested_markets": "true", "status": "open"}
        if cursor:
            params["cursor"] = cursor
        data = client._request("GET", "/events", signed=False, params=params)
        for e in data.get("events", []):
            for m in (e.get("markets") or []):
                yield e.get("event_ticker", ""), e.get("category", "?"), m
        cursor = data.get("cursor")
        if not cursor:
            break


def _raw_candidates_from_series(client: KalshiClient, series: list[str]):
    """Yield (event_ticker, category, market) by querying specific series.

    Used to reach high-frequency daily markets (crypto, temperature, indices)
    that the generic events feed does not surface.
    """
    for ser in series:
        cursor = None
        for _ in range(3):
            params = {"series_ticker": ser, "status": "open", "limit": 200}
            if cursor:
                params["cursor"] = cursor
            data = client._request("GET", "/markets", signed=False, params=params)
            for m in data.get("markets", []):
                yield m.get("event_ticker", ser), ser, m
            cursor = data.get("cursor")
            if not cursor:
                break


def _within_horizon(close_time: str, now_iso: str | None, within_hours: float | None) -> bool:
    if within_hours is None or now_iso is None:
        return True
    import datetime as dt

    try:
        ct = dt.datetime.fromisoformat(close_time.replace("Z", "+00:00"))
        now = dt.datetime.fromisoformat(now_iso.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return False
    return now < ct <= now + dt.timedelta(hours=within_hours)


def scan_and_open(
    ledger: ForwardLedger,
    client: KalshiClient,
    *,
    today: str,
    series: list[str] | None = None,
    within_hours: float | None = None,
    now_iso: str | None = None,
    min_yes_ask: int = 2,
    max_yes_ask: int = 15,
    min_volume: float = 1000.0,
    contracts: int = 10,
    max_new: int = 40,
    max_pages: int = 8,
) -> list[Position]:
    """Scan live open markets for deep longshots and open paper NO positions.

    With ``series`` set, scans those (daily) series directly and can filter to
    markets settling ``within_hours`` for a fast feedback loop; otherwise scans
    the general open-events feed. At most one position per event
    (diversification), nearest settlement first. Skips markets already held.
    """
    held = ledger.open_tickers() | {p.ticker for p in ledger.settled_positions()}
    seen_events: set[str] = set()
    candidates: list[Position] = []

    source = (
        _raw_candidates_from_series(client, series) if series
        else _raw_candidates_from_events(client, max_pages)
    )
    for event_ticker, category, m in source:
        q = market_quote_cents(m)
        if q is None:
            continue
        yb, ya = q
        try:
            vol = float(m.get("volume_fp") or 0)
        except (TypeError, ValueError):
            vol = 0
        if not (min_yes_ask <= ya <= max_yes_ask and yb >= 1 and vol >= min_volume):
            continue
        if not _within_horizon(m.get("close_time", ""), now_iso, within_hours):
            continue
        if m["ticker"] in held or event_ticker in seen_events:
            continue
        seen_events.add(event_ticker)
        candidates.append(Position(
            ticker=m["ticker"],
            event_ticker=event_ticker,
            category=category,
            entry_yes_bid=yb,
            entry_yes_ask=ya,
            entry_no_cost=100 - yb,
            contracts=contracts,
            opened_date=today,
            close_time=m.get("close_time", ""),
        ))

    candidates.sort(key=lambda p: p.close_time or "9999")
    opened = candidates[:max_new]
    for p in opened:
        ledger.add(p)
    if opened:
        ledger.save()
    return opened


def scan_weather_filtered(
    ledger: ForwardLedger,
    client: KalshiClient,
    *,
    today: str,
    min_lead_days: int = 2,
    max_lead_days: int = 3,
    max_yes_ask: int = 15,
    filter_prob: float = 0.07,
    sigma_floor: float = 3.5,
    min_volume: float = 200.0,
    contracts: int = 10,
    max_new: int = 20,
    session=None,
) -> list[Position]:
    """Open forecast-filtered fade-longshot positions on weather markets.

    For events settling ``min_lead_days``-``max_lead_days`` out, fetch the live
    forecast for the weather date and, in each event, fade (buy NO) the deepest
    longshot only if the forecast independently says it is unlikely
    (``model_prob <= filter_prob``). One position per event for diversification.
    """
    import datetime as dt
    from collections import defaultdict

    import requests

    from .weather.forecast import fetch_forecast
    from .weather.model import parse_range, range_probability
    from .weather.scan import weather_date_from_ticker
    from .weather.stations import STATIONS

    session = session or requests.Session()
    held = ledger.open_tickers() | {p.ticker for p in ledger.settled_positions()}
    today_d = dt.date.fromisoformat(today)
    lo, hi = today_d + dt.timedelta(days=min_lead_days), today_d + dt.timedelta(days=max_lead_days)

    candidates: list[Position] = []
    for series, station in STATIONS.items():
        try:
            markets = client.get_markets(series_ticker=series, status="open", limit=200)
        except Exception:
            continue
        events: dict[str, list[dict]] = defaultdict(list)
        for m in markets:
            events[m.get("event_ticker", "")].append(m)

        for event_ticker, ms in events.items():
            wd = weather_date_from_ticker(event_ticker)
            if not wd:
                continue
            try:
                wd_date = dt.date.fromisoformat(wd)
            except ValueError:
                continue
            if not (lo <= wd_date <= hi):
                continue
            try:
                forecast = fetch_forecast(station, wd, session=session, sigma_floor=sigma_floor)
            except Exception:
                forecast = None
            if forecast is None:
                continue

            # Among qualifying longshots in this event, fade the deepest one.
            best: Position | None = None
            for m in ms:
                rng = parse_range(m.get("yes_sub_title", ""))
                q = market_quote_cents(m)
                if rng is None or q is None or m["ticker"] in held:
                    continue
                yb, ya = q
                try:
                    vol = float(m.get("volume_fp") or 0)
                except (TypeError, ValueError):
                    vol = 0
                if not (2 <= ya <= max_yes_ask and yb >= 1 and vol >= min_volume):
                    continue
                prob = range_probability(rng, forecast.mean_f, forecast.sigma_f)
                if prob > filter_prob:  # forecast says it's actually live -> skip
                    continue
                if best is None or ya < best.entry_yes_ask:
                    best = Position(
                        ticker=m["ticker"], event_ticker=event_ticker,
                        category="Weather", entry_yes_bid=yb, entry_yes_ask=ya,
                        entry_no_cost=100 - yb, contracts=contracts, opened_date=today,
                        close_time=m.get("close_time", ""), model_prob=round(prob, 4),
                        weather_date=wd,
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


def settle_position(position: Position, market: dict) -> bool:
    """If ``market`` has settled, finalize the position's P&L. Returns True if changed."""
    status = market.get("status", "")
    result = market.get("result")
    if status not in ("settled", "finalized") or result not in ("yes", "no"):
        return False
    # NO contract pays 100c if the market resolves NO, else 0.
    payoff = 100 if result == "no" else 0
    fee = kalshi_fee_cents(position.entry_no_cost)
    position.result = result
    position.realized_pnl_cents = payoff - position.entry_no_cost - fee
    position.status = "settled"
    return True


def settle_open(ledger: ForwardLedger, client: KalshiClient) -> int:
    """Check each open position; settle the ones whose market has resolved."""
    changed = 0
    for p in ledger.open_positions():
        try:
            market = client.get_market(p.ticker)
        except Exception as exc:  # noqa: BLE001
            log.warning("could not fetch %s: %s", p.ticker, exc)
            continue
        if settle_position(p, market):
            changed += 1
            log.info("settled %s -> %s  P&L %+.1fc", p.ticker, p.result, p.realized_pnl_cents)
    if changed:
        ledger.save()
    return changed
