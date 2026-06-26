"""Daily high-temperature forecasts from Open-Meteo (free, no API key).

Pulls the daily maximum temperature for a station/date from several numerical
weather models. The model *mean* is the central estimate; the model *spread*
(floored) is the uncertainty used to turn the forecast into a probability
distribution over Kalshi's temperature ranges.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import requests

from .stations import Station

OPEN_METEO = "https://api.open-meteo.com/v1/forecast"
PREVIOUS_RUNS = "https://previous-runs-api.open-meteo.com/v1/forecast"
ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"
# Independent models; their disagreement is a usable uncertainty proxy.
MODELS = ["gfs_seamless", "ecmwf_ifs025", "icon_seamless"]
# Floor on sigma: model agreement understates true forecast error (~2-3F at 24h).
SIGMA_FLOOR_F = 2.5


@dataclass(frozen=True)
class TempForecast:
    station: str
    date: str
    mean_f: float
    sigma_f: float
    members: list[float]

    @property
    def n_models(self) -> int:
        return len(self.members)


def _stdev(xs: list[float], mean: float) -> float:
    if len(xs) < 2:
        return 0.0
    return math.sqrt(sum((x - mean) ** 2 for x in xs) / (len(xs) - 1))


def fetch_forecast(
    station: Station,
    date: str,
    *,
    session: requests.Session | None = None,
    models: list[str] | None = None,
    sigma_floor: float = SIGMA_FLOOR_F,
) -> TempForecast | None:
    """Fetch the daily-high forecast for ``station`` on ISO ``date`` (YYYY-MM-DD)."""
    session = session or requests.Session()
    models = models or MODELS
    resp = session.get(OPEN_METEO, params={
        "latitude": station.latitude,
        "longitude": station.longitude,
        "daily": "temperature_2m_max",
        "temperature_unit": "fahrenheit",
        "timezone": station.timezone,
        "start_date": date,
        "end_date": date,
        "models": ",".join(models),
    }, timeout=20)
    resp.raise_for_status()
    daily = resp.json().get("daily", {})
    return forecast_from_daily(station.name, date, daily, sigma_floor)


def daily_high_from_hourly(hourly: dict, var: str) -> dict[str, float]:
    """Reconstruct each day's high from hourly values (max over the day)."""
    from collections import defaultdict

    by_day: dict[str, list[float]] = defaultdict(list)
    times = hourly.get("time", [])
    values = hourly.get(var, [])
    for t, v in zip(times, values):
        if v is not None:
            by_day[t[:10]].append(float(v))
    return {d: max(vs) for d, vs in by_day.items() if vs}


def fetch_lead_forecast(
    station: Station, lead_days: int, *, past_days: int = 90,
    session: requests.Session | None = None,
) -> dict[str, float]:
    """Daily-high forecasts as they were ``lead_days`` before each date.

    Uses Open-Meteo's previous-runs archive (hourly ``*_previous_dayN``) and
    takes the daily max, so the forecast reflects only information available at
    that lead — no look-ahead.
    """
    session = session or requests.Session()
    var = f"temperature_2m_previous_day{lead_days}"
    resp = session.get(PREVIOUS_RUNS, params={
        "latitude": station.latitude, "longitude": station.longitude,
        "hourly": var, "temperature_unit": "fahrenheit", "timezone": station.timezone,
        "past_days": past_days, "forecast_days": 1,
    }, timeout=40)
    resp.raise_for_status()
    return daily_high_from_hourly(resp.json().get("hourly", {}), var)


def fetch_actuals(
    station: Station, start_date: str, end_date: str,
    session: requests.Session | None = None,
) -> dict[str, float]:
    """Realized daily-high temperatures (ERA5 archive) for ground truth."""
    session = session or requests.Session()
    resp = session.get(ARCHIVE, params={
        "latitude": station.latitude, "longitude": station.longitude,
        "daily": "temperature_2m_max", "temperature_unit": "fahrenheit",
        "timezone": station.timezone, "start_date": start_date, "end_date": end_date,
    }, timeout=40)
    resp.raise_for_status()
    daily = resp.json().get("daily", {})
    return {
        d: v for d, v in zip(daily.get("time", []), daily.get("temperature_2m_max", []))
        if v is not None
    }


def forecast_from_daily(
    station_name: str, date: str, daily: dict, sigma_floor: float = SIGMA_FLOOR_F
) -> TempForecast | None:
    """Build a forecast from an Open-Meteo ``daily`` block (pure; unit-testable)."""
    members = []
    for key, values in daily.items():
        if not key.startswith("temperature_2m_max") or not values:
            continue
        v = values[0]
        if v is not None:
            members.append(float(v))
    if not members:
        return None
    mean = sum(members) / len(members)
    sigma = max(_stdev(members, mean), sigma_floor)
    return TempForecast(station_name, date, mean, sigma, members)
