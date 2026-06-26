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
