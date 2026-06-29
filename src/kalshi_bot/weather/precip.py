"""Precipitation forecasting for Kalshi rain markets (e.g. KXRAINNYC).

These markets are binary: "precipitation > 0 at <station> on <date>". Open-Meteo
gives a daily max precipitation probability per model; averaging across models
yields P(rain), which prices the market directly — the same model-driven value
approach as the temperature markets, just a single binary instead of a range
partition.
"""

from __future__ import annotations

import requests

from .forecast import OPEN_METEO
from .stations import Station

PRECIP_MODELS = ["gfs_seamless", "ecmwf_ifs025", "icon_seamless"]


def rain_probability_from_daily(daily: dict) -> float | None:
    """Average daily max precipitation probability across models -> P(rain), 0-1."""
    probs = []
    for key, values in daily.items():
        if not key.startswith("precipitation_probability_max") or not values:
            continue
        v = values[0]
        if v is not None:
            probs.append(float(v))
    if not probs:
        return None
    return max(0.0, min(1.0, (sum(probs) / len(probs)) / 100.0))


def fetch_rain_probability(
    station: Station, date: str, *, session: requests.Session | None = None,
    models: list[str] | None = None,
) -> float | None:
    """Fetch P(measurable precipitation) for ``station`` on ISO ``date``."""
    session = session or requests.Session()
    resp = session.get(OPEN_METEO, params={
        "latitude": station.latitude, "longitude": station.longitude,
        "daily": "precipitation_probability_max", "timezone": station.timezone,
        "start_date": date, "end_date": date, "models": ",".join(models or PRECIP_MODELS),
    }, timeout=20)
    resp.raise_for_status()
    return rain_probability_from_daily(resp.json().get("daily", {}))
