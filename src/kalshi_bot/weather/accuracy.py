"""Measure how well our free forecast tracks the realized HDD/CDD index.

This is the precondition for any weather-derivative edge: before trading CME
HDD/CDD futures you must know how accurately you can forecast the *cumulative
index* for a contract strip. We compare our lead-time forecast (Open-Meteo
previous-runs, reconstructed daily Tmax/Tmin) to the realized values (ERA5
archive) and report daily error plus the cumulative-index error.

Note: accuracy is necessary but NOT sufficient for an edge — an edge requires
beating the *market's* forecast (the futures price), which needs CME settlement
data we do not have for free. See ``docs/STRATEGY.md``.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

import requests

from .forecast import ARCHIVE, PREVIOUS_RUNS
from .indices import cumulative, daily_average


def fetch_actual_daily(lat: float, lon: float, tz: str, start: str, end: str,
                       *, session: requests.Session | None = None,
                       unit: str = "fahrenheit") -> dict[str, tuple[float, float]]:
    """Realized daily (Tmax, Tmin) from the ERA5 archive."""
    session = session or requests.Session()
    r = session.get(ARCHIVE, params={
        "latitude": lat, "longitude": lon, "daily": "temperature_2m_max,temperature_2m_min",
        "temperature_unit": unit, "timezone": tz, "start_date": start, "end_date": end,
    }, timeout=40)
    r.raise_for_status()
    d = r.json().get("daily", {})
    out = {}
    for day, tmax, tmin in zip(d.get("time", []), d.get("temperature_2m_max", []),
                               d.get("temperature_2m_min", [])):
        if tmax is not None and tmin is not None:
            out[day] = (float(tmax), float(tmin))
    return out


def fetch_forecast_daily_lead(lat: float, lon: float, tz: str, lead_days: int,
                              *, past_days: int = 60, session: requests.Session | None = None,
                              unit: str = "fahrenheit") -> dict[str, tuple[float, float]]:
    """Lead-time daily (Tmax, Tmin) reconstructed from hourly previous-runs."""
    session = session or requests.Session()
    var = f"temperature_2m_previous_day{lead_days}"
    r = session.get(PREVIOUS_RUNS, params={
        "latitude": lat, "longitude": lon, "hourly": var, "temperature_unit": unit,
        "timezone": tz, "past_days": past_days, "forecast_days": 1,
    }, timeout=40)
    r.raise_for_status()
    h = r.json().get("hourly", {})
    by_day: dict[str, list[float]] = defaultdict(list)
    for t, v in zip(h.get("time", []), h.get(var, [])):
        if v is not None:
            by_day[t[:10]].append(float(v))
    return {d: (max(vs), min(vs)) for d, vs in by_day.items() if vs}


@dataclass
class AccuracyReport:
    city: str
    days: int
    daily_avg_mae: float        # mean abs error of daily average temp
    forecast_cdd: float
    actual_cdd: float
    forecast_hdd: float
    actual_hdd: float

    @property
    def cdd_error_pct(self) -> float:
        return 100.0 * (self.forecast_cdd - self.actual_cdd) / self.actual_cdd if self.actual_cdd else 0.0

    @property
    def hdd_error_pct(self) -> float:
        return 100.0 * (self.forecast_hdd - self.actual_hdd) / self.actual_hdd if self.actual_hdd else 0.0

    def __str__(self) -> str:
        return (f"{self.city:24} n={self.days:3} dailyMAE={self.daily_avg_mae:.2f}F  "
                f"CDD fc={self.forecast_cdd:6.1f} act={self.actual_cdd:6.1f} "
                f"({self.cdd_error_pct:+5.1f}%)  HDD err {self.hdd_error_pct:+5.1f}%")


def accuracy_report(city: str, forecast: dict[str, tuple[float, float]],
                    actual: dict[str, tuple[float, float]], *, base: float = 65.0) -> AccuracyReport:
    """Compare a lead forecast to realized values over the overlapping dates."""
    dates = sorted(set(forecast) & set(actual))
    if not dates:
        return AccuracyReport(city, 0, 0.0, 0.0, 0.0, 0.0, 0.0)
    abs_err = sum(abs(daily_average(*forecast[d]) - daily_average(*actual[d])) for d in dates)
    fc = cumulative([forecast[d] for d in dates], base=base)
    ac = cumulative([actual[d] for d in dates], base=base)
    return AccuracyReport(city, len(dates), abs_err / len(dates),
                          fc.cdd, ac.cdd, fc.hdd, ac.hdd)
