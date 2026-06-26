"""Scan a city's temperature event: forecast vs market, surfacing value bets."""

from __future__ import annotations

import re
from dataclasses import dataclass

import requests

from ..kalshi.client import KalshiClient
from .forecast import TempForecast, fetch_forecast
from .model import parse_range, range_probability
from .stations import station_for
from .value import ValueBet, evaluate


@dataclass
class RangeView:
    ticker: str
    subtitle: str
    model_prob: float
    yes_bid: int
    yes_ask: int
    bet: ValueBet | None


@dataclass
class CityScan:
    series: str
    station: str
    date: str
    forecast: TempForecast
    rows: list[RangeView]

    @property
    def model_total(self) -> float:
        """Sum of model probabilities — should be ~1 for a full partition."""
        return sum(r.model_prob for r in self.rows)

    def value_bets(self) -> list[ValueBet]:
        return [r.bet for r in self.rows if r.bet is not None]


def _cents(v) -> int | None:
    try:
        return round(float(v) * 100)
    except (TypeError, ValueError):
        return None


_MONTHS = {m: i for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"], 1)}


def weather_date_from_ticker(ticker: str) -> str | None:
    """Parse the resolution date from a ticker like ``KXHIGHCHI-26JUN26-T69``.

    This is the *weather* date, which differs from the market's close_time (these
    markets close the following morning) — using close_time forecasts the wrong
    day.
    """
    m = re.search(r"-(\d{2})([A-Z]{3})(\d{2})(?:-|$)", ticker)
    if not m:
        return None
    yy, mon, dd = m.group(1), m.group(2), m.group(3)
    if mon not in _MONTHS:
        return None
    return f"20{yy}-{_MONTHS[mon]:02d}-{int(dd):02d}"


def scan_city(
    client: KalshiClient,
    series: str,
    *,
    session: requests.Session | None = None,
    min_edge_cents: float = 3.0,
    event_ticker: str | None = None,
    min_volume: float = 500.0,
    max_spread_cents: int = 8,
) -> CityScan | None:
    """Forecast the next event for ``series`` and compare every range to market.

    Only markets with a genuine two-sided, reasonably tight, liquid quote are
    eligible for a value bet — stale one-sided quotes on thin range markets
    otherwise produce absurd phantom "edges".
    """
    station = station_for(series)
    if station is None:
        return None
    markets = client.get_markets(series_ticker=series, status="open", limit=200)
    if not markets:
        return None

    # Choose the nearest event (soonest close) unless one is named.
    if event_ticker is None:
        event_ticker = min(markets, key=lambda m: m.get("close_time", "9999"))["event_ticker"]
    event_markets = [m for m in markets if m.get("event_ticker") == event_ticker]
    # The weather date comes from the ticker, NOT close_time (markets close the
    # morning after the weather day).
    date = weather_date_from_ticker(event_ticker) or (event_markets[0].get("close_time") or "")[:10]

    forecast = fetch_forecast(station, date, session=session)
    if forecast is None:
        return None

    rows: list[RangeView] = []
    for m in event_markets:
        sub = m.get("yes_sub_title", "")
        rng = parse_range(sub)
        yb, ya = _cents(m.get("yes_bid_dollars")), _cents(m.get("yes_ask_dollars"))
        if rng is None or yb is None or ya is None:
            continue
        prob = range_probability(rng, forecast.mean_f, forecast.sigma_f)

        try:
            vol = float(m.get("volume_fp") or 0)
        except (TypeError, ValueError):
            vol = 0
        # Liquidity gate: real two-sided book, tight-ish spread, enough volume.
        liquid = (yb >= 1 and ya <= 99 and (ya - yb) <= max_spread_cents and vol >= min_volume)
        bet = (
            evaluate(m["ticker"], sub, prob, yb, ya, min_edge_cents=min_edge_cents)
            if liquid else None
        )
        rows.append(RangeView(m["ticker"], sub, prob, yb, ya, bet))

    rows.sort(key=lambda r: r.subtitle)
    return CityScan(series, station.name, date, forecast, rows)
