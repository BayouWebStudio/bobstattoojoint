"""Polymarket global temperature markets: city coords, parsing, forecasts.

Polymarket runs daily "Highest temperature in <City> on <Date>?" events, each a
partition of 1-degree Celsius range bins. This module maps cities to coordinates,
parses the Celsius bins, and pulls Open-Meteo forecasts (in Celsius) so the same
model-driven value approach used for Kalshi applies globally.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass

import requests

from ..weather.model import Range

GAMMA = "https://gamma-api.polymarket.com"
OPEN_METEO = "https://api.open-meteo.com/v1/forecast"
PREVIOUS_RUNS = "https://previous-runs-api.open-meteo.com/v1/forecast"
MODELS = ["gfs_seamless", "ecmwf_ifs025", "icon_seamless"]
SIGMA_FLOOR_C = 1.5  # ~2.5F floor converted to Celsius


# City -> (latitude, longitude, IANA timezone). Coordinates are city-center;
# Polymarket's exact resolution source may differ slightly (a refinement).
CITY_COORDS: dict[str, tuple[float, float, str]] = {
    "NYC": (40.78, -73.97, "America/New_York"),
    "London": (51.51, -0.13, "Europe/London"),
    "Paris": (48.85, 2.35, "Europe/Paris"),
    "Seoul": (37.57, 126.98, "Asia/Seoul"),
    "Busan": (35.18, 129.08, "Asia/Seoul"),
    "Tokyo": (35.68, 139.69, "Asia/Tokyo"),
    "Hong Kong": (22.32, 114.17, "Asia/Hong_Kong"),
    "Shanghai": (31.23, 121.47, "Asia/Shanghai"),
    "Beijing": (39.90, 116.41, "Asia/Shanghai"),
    "Guangzhou": (23.13, 113.26, "Asia/Shanghai"),
    "Shenzhen": (22.54, 114.06, "Asia/Shanghai"),
    "Qingdao": (36.07, 120.38, "Asia/Shanghai"),
    "Chengdu": (30.57, 104.07, "Asia/Shanghai"),
    "Madrid": (40.42, -3.70, "Europe/Madrid"),
    "Munich": (48.14, 11.58, "Europe/Berlin"),
    "Helsinki": (60.17, 24.94, "Europe/Helsinki"),
    "Ankara": (39.93, 32.87, "Europe/Istanbul"),
    "Karachi": (24.86, 67.00, "Asia/Karachi"),
    "Jeddah": (21.49, 39.19, "Asia/Riyadh"),
    "Cape Town": (-33.92, 18.42, "Africa/Johannesburg"),
    "Sao Paulo": (-23.55, -46.63, "America/Sao_Paulo"),
    "Dallas": (32.78, -96.80, "America/Chicago"),
    "San Francisco": (37.77, -122.42, "America/Los_Angeles"),
}


@dataclass(frozen=True)
class PolyTempMarket:
    city: str
    date: str          # ISO weather date
    bin_title: str     # e.g. "22°C", "21°C or below"
    rng: Range         # parsed integer Celsius bounds
    yes_price: float   # 0-1 (dollars)
    token_id: str | None
    volume: float


def parse_celsius_bin(title: str) -> Range | None:
    """Parse a Polymarket Celsius bin title into integer inclusive bounds."""
    s = title.replace("°C", "").replace("°", "").strip()
    m = re.match(r"^(-?\d+)\s*or\s*below$", s, re.I)
    if m:
        return (None, int(m.group(1)))
    m = re.match(r"^(-?\d+)\s*or\s*(?:higher|above)$", s, re.I)
    if m:
        return (int(m.group(1)), None)
    m = re.match(r"^(-?\d+)$", s)
    if m:
        return (int(m.group(1)), int(m.group(1)))
    return None


def city_from_title(title: str) -> str | None:
    """Extract the city from 'Highest temperature in <City> on <Date>?'."""
    m = re.search(r"temperature in (.+?) on ", title, re.I)
    return m.group(1).strip() if m else None


def _date_from_event(event: dict) -> str | None:
    end = (event.get("endDate") or "")[:10]
    return end or None


def fetch_temp_events(session: requests.Session, *, closed: bool) -> list[dict]:
    """Fetch temperature events via the public search endpoint."""
    r = session.get(f"{GAMMA}/public-search",
                    params={"q": "highest temperature", "limit_per_type": 100}, timeout=20)
    r.raise_for_status()
    events = r.json().get("events") or []
    return [e for e in events if bool(e.get("closed")) == closed]


def markets_from_event(event: dict) -> list[PolyTempMarket]:
    """Parse a temperature event's range-bin markets into PolyTempMarket rows."""
    import json

    city = city_from_title(event.get("title", ""))
    date = _date_from_event(event)
    if city is None or date is None or city not in CITY_COORDS:
        return []
    out = []
    for m in event.get("markets") or []:
        bin_title = m.get("groupItemTitle") or ""
        rng = parse_celsius_bin(bin_title)
        if rng is None:
            continue
        try:
            prices = json.loads(m.get("outcomePrices") or "[]")
            yes = float(prices[0]) if prices else None
        except (ValueError, TypeError, IndexError):
            yes = None
        if yes is None:
            continue
        token = None
        try:
            toks = json.loads(m.get("clobTokenIds") or "[]")
            token = toks[0] if toks else None
        except (ValueError, TypeError):
            pass
        try:
            vol = float(m.get("volumeNum") or 0)
        except (TypeError, ValueError):
            vol = 0.0
        out.append(PolyTempMarket(city, date, bin_title, rng, yes, token, vol))
    return out


def _stdev(xs: list[float], mean: float) -> float:
    import math
    if len(xs) < 2:
        return 0.0
    return math.sqrt(sum((x - mean) ** 2 for x in xs) / (len(xs) - 1))


def fetch_forecast_c(city: str, date: str, session: requests.Session,
                     *, lead_days: int | None = None) -> tuple[float, float] | None:
    """Return (mean_C, sigma_C) for a city/date. If ``lead_days`` set, use the
    previous-runs archive (forecast as it stood that many days before)."""
    if city not in CITY_COORDS:
        return None
    lat, lon, tz = CITY_COORDS[city]
    if lead_days is None:
        r = session.get(OPEN_METEO, params={
            "latitude": lat, "longitude": lon, "daily": "temperature_2m_max",
            "temperature_unit": "celsius", "timezone": tz,
            "start_date": date, "end_date": date, "models": ",".join(MODELS),
        }, timeout=20)
        r.raise_for_status()
        daily = r.json().get("daily", {})
        members = [v[0] for k, v in daily.items()
                   if k.startswith("temperature_2m_max") and v and v[0] is not None]
        if not members:
            return None
        mean = sum(members) / len(members)
        return mean, max(_stdev(members, mean), SIGMA_FLOOR_C)
    # lead-time forecast from hourly previous-runs
    var = f"temperature_2m_previous_day{lead_days}"
    r = session.get(PREVIOUS_RUNS, params={
        "latitude": lat, "longitude": lon, "hourly": var,
        "temperature_unit": "celsius", "timezone": tz, "past_days": 90, "forecast_days": 1,
    }, timeout=40)
    r.raise_for_status()
    hourly = r.json().get("hourly", {})
    by_day: dict[str, list[float]] = defaultdict(list)
    for t, v in zip(hourly.get("time", []), hourly.get(var, [])):
        if v is not None:
            by_day[t[:10]].append(float(v))
    highs = {d: max(vs) for d, vs in by_day.items() if vs}
    if date not in highs:
        return None
    return highs[date], SIGMA_FLOOR_C + 1.0  # wider sigma at multi-day lead
