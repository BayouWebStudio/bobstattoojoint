"""Weather forecasting edge for Kalshi daily high-temperature markets.

Kalshi high-temp markets resolve on an official NWS station reading and are
structured as a partition of temperature ranges. With a real forecast we can
price every range and bet where the market disagrees — model-driven value,
not a blind statistical bias.
"""

from .stations import STATIONS, Station
from .model import range_probability, parse_range

__all__ = ["STATIONS", "Station", "range_probability", "parse_range"]
