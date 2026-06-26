"""Turn a temperature forecast into per-range probabilities.

Kalshi high-temp ranges come as subtitles like ``"75° or below"``,
``"76° to 77°"``, ``"84° or above"``. The reported daily high is an integer, so
each range maps to a continuous interval using half-degree boundaries (e.g.
"76° to 77°" -> (75.5, 77.5)). With the forecast modeled as Normal(mean, sigma),
each range's probability is a difference of Normal CDFs. The probabilities over
a full event partition sum to 1 by construction.
"""

from __future__ import annotations

import math
import re

# (low_inclusive, high_inclusive); None means open-ended on that side.
Range = tuple[int | None, int | None]


def parse_range(subtitle: str) -> Range | None:
    """Parse a Kalshi temperature-range subtitle into integer inclusive bounds."""
    s = subtitle.replace("°", "").replace("°", "").strip()
    m = re.match(r"^(-?\d+)\s*(?:or\s*)?below$", s, re.I) or \
        re.match(r"^below\s*(-?\d+)$", s, re.I)
    if m:
        return (None, int(m.group(1)))
    m = re.match(r"^(-?\d+)\s*(?:or\s*)?above$", s, re.I) or \
        re.match(r"^above\s*(-?\d+)$", s, re.I)
    if m:
        return (int(m.group(1)), None)
    m = re.match(r"^(-?\d+)\s*(?:to|-)\s*(-?\d+)$", s, re.I)
    if m:
        return (int(m.group(1)), int(m.group(2)))
    return None


def norm_cdf(x: float, mean: float, sigma: float) -> float:
    if sigma <= 0:
        return 1.0 if x >= mean else 0.0
    return 0.5 * (1.0 + math.erf((x - mean) / (sigma * math.sqrt(2.0))))


def range_probability(rng: Range, mean: float, sigma: float) -> float:
    """P(daily high falls in ``rng``) under Normal(mean, sigma).

    Integer bounds are widened by 0.5 to continuous boundaries (the high is
    reported as an integer, so e.g. "76 to 77" covers (75.5, 77.5)).
    """
    low, high = rng
    lo_edge = -math.inf if low is None else low - 0.5
    hi_edge = math.inf if high is None else high + 0.5
    p = norm_cdf(hi_edge, mean, sigma) - norm_cdf(lo_edge, mean, sigma)
    return max(0.0, min(1.0, p))
