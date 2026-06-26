"""Resolution stations for Kalshi high-temperature series.

Each series resolves on the NWS daily high at a specific station (per each
market's ``rules_primary``). Coordinates are the official observation sites so
the forecast is pulled for the right grid point.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Station:
    series: str
    name: str
    latitude: float
    longitude: float
    timezone: str


STATIONS: dict[str, Station] = {
    "KXHIGHNY": Station("KXHIGHNY", "Central Park, New York", 40.7790, -73.9693, "America/New_York"),
    "KXHIGHCHI": Station("KXHIGHCHI", "Chicago Midway", 41.7861, -87.7522, "America/Chicago"),
    "KXHIGHMIA": Station("KXHIGHMIA", "Miami Intl Airport", 25.7959, -80.2870, "America/New_York"),
    "KXHIGHLAX": Station("KXHIGHLAX", "Los Angeles Airport", 33.9416, -118.4085, "America/Los_Angeles"),
    "KXHIGHAUS": Station("KXHIGHAUS", "Austin Bergstrom", 30.1945, -97.6699, "America/Chicago"),
    "KXHIGHDEN": Station("KXHIGHDEN", "Denver Intl Airport", 39.8561, -104.6737, "America/Denver"),
    "KXHIGHPHIL": Station("KXHIGHPHIL", "Philadelphia Intl Airport", 39.8744, -75.2424, "America/New_York"),
}


def station_for(series: str) -> Station | None:
    return STATIONS.get(series)
