"""Weather-derivative indices: HDD, CDD, CAT.

These are the standard indices CME weather futures settle on. Pure, deterministic
functions (no I/O) so they are trivial to expose as agent/MCP tools and to unit
test. Daily average temperature is ``(Tmax + Tmin) / 2``; the base is 65°F (18°C)
by convention.

- HDD (Heating Degree Day)  = max(0, base - avg)   — winter/heating demand
- CDD (Cooling Degree Day)  = max(0, avg - base)   — summer/cooling demand
- CAT (Cumulative Avg Temp) = sum of daily averages — European-style contracts
"""

from __future__ import annotations

from dataclasses import dataclass

BASE_F = 65.0
BASE_C = 18.0


def daily_average(tmax: float, tmin: float) -> float:
    """Daily average temperature used by all the indices."""
    return (tmax + tmin) / 2.0


def hdd(tmax: float, tmin: float, base: float = BASE_F) -> float:
    """Heating degree days for one day."""
    return max(0.0, base - daily_average(tmax, tmin))


def cdd(tmax: float, tmin: float, base: float = BASE_F) -> float:
    """Cooling degree days for one day."""
    return max(0.0, daily_average(tmax, tmin) - base)


@dataclass(frozen=True)
class IndexTotals:
    days: int
    hdd: float
    cdd: float
    cat: float  # cumulative average temperature

    def __str__(self) -> str:
        return f"days={self.days} HDD={self.hdd:.1f} CDD={self.cdd:.1f} CAT={self.cat:.1f}"


def cumulative(
    daily: list[tuple[float, float]], *, base: float = BASE_F
) -> IndexTotals:
    """Cumulative HDD/CDD/CAT over a strip of ``(tmax, tmin)`` daily pairs."""
    total_hdd = total_cdd = total_cat = 0.0
    for tmax, tmin in daily:
        avg = daily_average(tmax, tmin)
        total_cat += avg
        total_hdd += max(0.0, base - avg)
        total_cdd += max(0.0, avg - base)
    return IndexTotals(len(daily), total_hdd, total_cdd, total_cat)


def fahrenheit(celsius: float) -> float:
    return celsius * 9.0 / 5.0 + 32.0
