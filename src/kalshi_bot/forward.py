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
    forecast_mean: float | None = None
    forecast_sigma: float | None = None
    entry_mode: str | None = None        # "mid" (passive) | "ask" (cross spread)
    edge_cents: float | None = None      # modeled EV per contract at entry
    actual_high: float | None = None     # realized temperature, filled at settlement


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
    min_lead_days: int = 1,
    max_lead_days: int = 3,
    min_yes_ask: int = 3,
    max_yes_ask: int = 20,
    min_edge_cents: float = 2.0,
    entry: str = "mid",
    sigma_floor: float = 3.5,
    min_volume: float = 200.0,
    contracts: int = 10,
    max_new: int = 20,
    calibrate: bool = True,
    session=None,
) -> list[Position]:
    """Open forecast-driven NO (fade) positions on weather longshot ranges.

    For events settling ``min_lead_days``-``max_lead_days`` out, fetch the live
    forecast and, in each event, buy NO on the longshot range with the highest
    **expected value** at the chosen entry, when that EV clears ``min_edge_cents``
    after fees. EV uses the forecast probability:
    ``(1 - model_prob)*100 - NO_cost - fee``.

    This is the principled form of the fade: it targets ranges the market
    *overprices* relative to the forecast, regardless of exact price — the deep
    1-2c longshots are usually -EV (the calibrated forecast says they are more
    likely than their price implies), while genuine value sits where the market
    bids a tail up past what the forecast supports. One position per event.
    """
    from .research.calibration import kalshi_fee_cents
    import datetime as dt
    from collections import defaultdict

    import requests

    from .weather.forecast import fetch_forecast, station_calibration
    from .weather.model import parse_range, range_probability
    from .weather.scan import weather_date_from_ticker
    from .weather.stations import STATIONS

    session = session or requests.Session()
    held = ledger.open_tickers() | {p.ticker for p in ledger.settled_positions()}
    today_d = dt.date.fromisoformat(today)
    lo, hi = today_d + dt.timedelta(days=min_lead_days), today_d + dt.timedelta(days=max_lead_days)

    cal_cache: dict[str, tuple[float, float]] = {}
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
            mean_f, sigma_f = forecast.mean_f, forecast.sigma_f
            # Debias per station against measured forecast error — the same
            # correction the Polymarket scan applies; our gridpoint runs
            # systematically hot/cold vs some resolution stations (see 'indices').
            if calibrate:
                if series not in cal_cache:
                    try:
                        cal_cache[series] = station_calibration(
                            station, today=today, session=session)
                    except Exception:
                        cal_cache[series] = (0.0, sigma_f)
                bias, cal_sigma = cal_cache[series]
                mean_f -= bias
                sigma_f = max(cal_sigma, sigma_f)

            # In each event, take the longshot NO with the best EV that clears
            # the edge threshold (one position per event for diversification).
            best: Position | None = None
            best_edge = min_edge_cents
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
                if not (min_yes_ask <= ya <= max_yes_ask and yb >= 1 and vol >= min_volume):
                    continue
                prob = range_probability(rng, mean_f, sigma_f)
                no_cost = (100 - yb) if entry == "ask" else round(100 - (yb + ya) / 2)
                edge = (1 - prob) * 100 - no_cost - kalshi_fee_cents(no_cost)
                if edge >= best_edge:
                    best_edge = edge
                    best = Position(
                        ticker=m["ticker"], event_ticker=event_ticker,
                        category="Weather", entry_yes_bid=yb, entry_yes_ask=ya,
                        entry_no_cost=no_cost, contracts=contracts, opened_date=today,
                        close_time=m.get("close_time", ""), model_prob=round(prob, 4),
                        weather_date=wd, forecast_mean=round(mean_f, 1),
                        forecast_sigma=round(sigma_f, 1), entry_mode=entry,
                        edge_cents=round(edge, 1),
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


def scan_precip(
    ledger: ForwardLedger,
    client: KalshiClient,
    *,
    today: str,
    series: str = "KXRAINNYC",
    station_series: str = "KXHIGHNY",   # Central Park coords
    min_lead_days: int = 1,
    max_lead_days: int = 3,
    min_edge_cents: float = 3.0,
    entry: str = "ask",                  # rain markets are thin; assume crossing
    min_volume: float = 20.0,
    contracts: int = 10,
    session=None,
) -> list[Position]:
    """Fade (buy NO) Kalshi rain markets when the forecast says rain is unlikely.

    Binary "precip > 0" markets priced from Open-Meteo P(rain). Opens a NO fade
    when ``(1 - P(rain))*100 - NO_cost - fee >= min_edge_cents``.
    """
    import datetime as dt

    import requests

    from .research.calibration import kalshi_fee_cents
    from .weather.precip import fetch_rain_probability
    from .weather.scan import weather_date_from_ticker
    from .weather.stations import STATIONS

    session = session or requests.Session()
    station = STATIONS.get(station_series)
    if station is None:
        return []
    held = ledger.open_tickers() | {p.ticker for p in ledger.settled_positions()}
    today_d = dt.date.fromisoformat(today)
    lo, hi = today_d + dt.timedelta(days=min_lead_days), today_d + dt.timedelta(days=max_lead_days)

    try:
        markets = client.get_markets(series_ticker=series, status="open", limit=50)
    except Exception:
        return []

    opened: list[Position] = []
    for m in markets:
        if m["ticker"] in held:
            continue
        wd = weather_date_from_ticker(m.get("event_ticker", "")) or \
            weather_date_from_ticker(m["ticker"])
        if not wd:
            continue
        try:
            wd_date = dt.date.fromisoformat(wd)
        except ValueError:
            continue
        if not (lo <= wd_date <= hi):
            continue
        q = market_quote_cents(m)
        if q is None:
            continue
        yb, ya = q
        try:
            vol = float(m.get("volume_fp") or 0)
        except (TypeError, ValueError):
            vol = 0
        if not (yb >= 1 and ya <= 99 and vol >= min_volume):
            continue
        prob = fetch_rain_probability(station, wd, session=session)  # P(rain = YES)
        if prob is None:
            continue
        no_cost = (100 - yb) if entry == "ask" else round(100 - (yb + ya) / 2)
        edge = (1 - prob) * 100 - no_cost - kalshi_fee_cents(no_cost)
        if edge < min_edge_cents:
            continue
        pos = Position(
            ticker=m["ticker"], event_ticker=m.get("event_ticker", ""), category="Rain",
            entry_yes_bid=yb, entry_yes_ask=ya, entry_no_cost=no_cost, contracts=contracts,
            opened_date=today, close_time=m.get("close_time", ""), model_prob=round(prob, 4),
            weather_date=wd, entry_mode=entry, edge_cents=round(edge, 1),
        )
        ledger.add(pos)
        opened.append(pos)
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


RESULTS_LOG = "data/results_log.csv"


def settle_open(ledger: ForwardLedger, client: KalshiClient, *, results_log: str = RESULTS_LOG,
                session=None) -> int:
    """Check each open position; settle the ones whose market has resolved.

    Weather positions are enriched with the realized daily high (for later
    forecast-calibration analysis), and every newly-settled position is appended
    to a permanent results log so no data is lost across runs.
    """
    changed = 0
    newly_settled: list[Position] = []
    for p in ledger.open_positions():
        try:
            market = client.get_market(p.ticker)
        except Exception as exc:  # noqa: BLE001
            log.warning("could not fetch %s: %s", p.ticker, exc)
            continue
        if settle_position(p, market):
            changed += 1
            _enrich_actual_high(p, session)
            newly_settled.append(p)
            log.info("settled %s -> %s  P&L %+.1fc", p.ticker, p.result, p.realized_pnl_cents)
    if changed:
        ledger.save()
        append_results_log(newly_settled, results_log)
    return changed


def _enrich_actual_high(position: Position, session=None) -> None:
    """Best-effort: record the realized daily high for a settled weather position."""
    if not position.weather_date:
        return
    try:
        from .weather.forecast import fetch_actuals
        from .weather.stations import STATIONS

        series = position.ticker.split("-")[0]
        station = STATIONS.get(series)
        if station is None:
            return
        actuals = fetch_actuals(station, position.weather_date, position.weather_date,
                                session=session)
        if position.weather_date in actuals:
            position.actual_high = round(actuals[position.weather_date], 1)
    except Exception:  # noqa: BLE001 - enrichment is non-critical
        pass


_RESULTS_FIELDS = [
    "ticker", "category", "weather_date", "opened_date", "close_time",
    "entry_yes_bid", "entry_yes_ask", "entry_no_cost", "entry_mode", "contracts",
    "model_prob", "forecast_mean", "forecast_sigma", "edge_cents", "actual_high",
    "result", "realized_pnl_cents",
]


def append_results_log(positions: list[Position], path: str = RESULTS_LOG) -> None:
    """Append settled positions to a permanent CSV (header written once)."""
    import csv
    from dataclasses import asdict

    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    exists = p.exists()
    with p.open("a", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=_RESULTS_FIELDS, extrasaction="ignore")
        if not exists:
            writer.writeheader()
        for pos in positions:
            writer.writerow(asdict(pos))
