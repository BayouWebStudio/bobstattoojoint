"""Double-edge backtest: fade-longshot filtered by a lead-time forecast.

Each :class:`ForecastSample` pairs, for a settled weather market: the model's
probability from the forecast *as it stood at the chosen lead* (e.g. 72h before),
the market's quote at that same lead, and the realized outcome.

The comparison that matters is **unfiltered** fade-longshot vs **forecast-filtered**
fade — fading a cheap contract only when the forecast independently says the
outcome is genuinely unlikely. The filter's job is to drop the "longshots" that
are actually live; we report how many it excluded and how many of those hit.
"""

from __future__ import annotations

import datetime as dt
import math
import statistics
from dataclasses import dataclass

from ..research.calibration import kalshi_fee_cents


@dataclass(frozen=True)
class ForecastSample:
    model_prob: float   # P(YES) from the lead-time forecast
    yes_bid: int
    yes_ask: int
    result_yes: bool


def collect_samples(client, lead_days: int = 3, *, session=None, per_city: int = 45,
                    today: str = "2026-06-26"):
    """Build ForecastSamples from settled weather markets (live data).

    For each settled market: the lead-time forecast (debiased, with sigma from
    that station's forecast-error spread) gives the model probability; the market
    quote ``lead_days`` before the weather date gives the price; the settled
    result gives the outcome.
    """
    import requests

    from .forecast import fetch_actuals, fetch_lead_forecast
    from .model import parse_range, range_probability
    from .scan import weather_date_from_ticker
    from .stations import STATIONS

    session = session or requests.Session()
    base = client._settings.base_url
    samples: list[ForecastSample] = []

    for series, st in STATIONS.items():
        try:
            fc = fetch_lead_forecast(st, lead_days, session=session)
            start = (dt.date.fromisoformat(today) - dt.timedelta(days=95)).isoformat()
            act = fetch_actuals(st, start, today, session=session)
        except Exception:
            continue
        errs = [fc[d] - act[d] for d in fc if d in act]
        sigma = max(statistics.pstdev(errs), 2.0) if len(errs) > 3 else 3.5
        bias = statistics.mean(errs) if errs else 0.0

        markets, cursor, used = [], None, 0
        for _ in range(3):
            params = {"series_ticker": series, "status": "settled", "limit": 200}
            if cursor:
                params["cursor"] = cursor
            data = client._request("GET", "/markets", signed=False, params=params)
            markets += [m for m in data.get("markets", []) if m.get("result") in ("yes", "no")]
            cursor = data.get("cursor")
            if not cursor:
                break

        for m in markets:
            if used >= per_city:
                break
            wd = weather_date_from_ticker(m.get("event_ticker", "")) or \
                weather_date_from_ticker(m["ticker"])
            rng = parse_range(m.get("yes_sub_title", ""))
            if not wd or wd not in fc or not rng:
                continue
            quote = _quote_at_lead(client, series, m, wd, lead_days, session)
            if quote is None:
                continue
            prob = range_probability(rng, fc[wd] - bias, sigma)
            samples.append(ForecastSample(prob, quote[0], quote[1], m["result"] == "yes"))
            used += 1
    return samples


def _quote_at_lead(client, series, market, weather_date, lead_days, session):
    """Market (yes_bid, yes_ask) in cents ~``lead_days`` before the weather date."""
    try:
        close = dt.datetime.fromisoformat(market["close_time"].replace("Z", "+00:00")).timestamp()
        candles = client.get_candlesticks(
            series, market["ticker"], int(close - 10 * 86400), int(close), 1440
        )
    except Exception:
        return None
    target = dt.datetime.fromisoformat(weather_date).timestamp() - lead_days * 86400
    best, best_d = None, None
    for c in candles:
        yb = (c.get("yes_bid") or {}).get("close_dollars")
        ya = (c.get("yes_ask") or {}).get("close_dollars")
        ts = c.get("end_period_ts")
        try:
            yb, ya = float(yb), float(ya)
        except (TypeError, ValueError):
            continue
        if not 0 < yb < ya < 1:
            continue
        d = abs((ts or 0) - target)
        if best_d is None or d < best_d:
            best_d, best = d, (round(yb * 100), round(ya * 100))
    return best


@dataclass
class FadeResult:
    n: int
    roi_pct: float
    ev_cents: float
    win_rate: float
    t_stat: float
    excluded: int        # longshots the filter skipped
    excluded_hits: int   # of those, how many actually resolved YES

    def __str__(self) -> str:
        s = (f"n={self.n} ROI={self.roi_pct:+.1f}% EV={self.ev_cents:+.1f}c "
             f"win={self.win_rate:.0%} t={self.t_stat:+.2f}")
        if self.excluded:
            s += f" (excluded {self.excluded}, {self.excluded_hits} hit)"
        return s


def fade_backtest(
    samples: list[ForecastSample],
    *,
    filter_prob: float | None = None,
    max_yes_ask: int = 15,
    min_yes_ask: int = 2,
    entry: str = "mid",
) -> FadeResult:
    """Backtest fading longshots, optionally filtered by forecast probability.

    With ``filter_prob`` set, a longshot is only faded when the forecast model
    says ``model_prob <= filter_prob`` — i.e. the forecast agrees it is unlikely.
    """
    pnl: list[float] = []
    capital = 0.0
    excluded = 0
    excluded_hits = 0

    for s in samples:
        if not min_yes_ask <= s.yes_ask <= max_yes_ask:
            continue
        if filter_prob is not None and s.model_prob > filter_prob:
            excluded += 1
            excluded_hits += int(s.result_yes)
            continue
        no_cost = (100 - s.yes_bid) if entry == "ask" else (100 - (s.yes_bid + s.yes_ask) / 2)
        payoff = 0.0 if s.result_yes else 100.0
        pnl.append(payoff - no_cost - kalshi_fee_cents(no_cost))
        capital += no_cost

    n = len(pnl)
    if n == 0 or capital == 0:
        return FadeResult(0, 0.0, 0.0, 0.0, 0.0, excluded, excluded_hits)

    mean = sum(pnl) / n
    if n > 1:
        sd = math.sqrt(sum((x - mean) ** 2 for x in pnl) / (n - 1))
        t = mean / (sd / math.sqrt(n)) if sd > 0 else 0.0
    else:
        t = 0.0
    wins = sum(1 for x in pnl if x > 0)
    return FadeResult(
        n=n, roi_pct=100.0 * sum(pnl) / capital, ev_cents=mean,
        win_rate=wins / n, t_stat=t, excluded=excluded, excluded_hits=excluded_hits,
    )
