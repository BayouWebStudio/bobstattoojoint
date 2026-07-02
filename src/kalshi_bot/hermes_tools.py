"""Agent-facing tool layer for exporting this system to Hermes (or any framework).

Design contract for every tool in this module:

- **JSON-in / JSON-out**: parameters are plain scalars/lists/dicts; returns are
  JSON-serializable dicts (dataclasses are converted). No custom types leak.
- **Never raises**: failures return ``{"error": "<message>"}`` so an agent can
  reason about them instead of crashing its tool call.
- **Self-describing**: :data:`TOOL_REGISTRY` maps tool name -> callable and
  :func:`tool_manifest` emits name/description/parameter specs that any
  tool-calling framework (Hermes, MCP, OpenAI-style) can translate into its own
  schema format.

Public market data needs no credentials. Set ``KALSHI_ENV`` (demo|prod) or pass
``env`` where offered. See HERMES.md for integration notes and the validated-edge
summary (and its caveats) behind these tools.
"""

from __future__ import annotations

import inspect
from dataclasses import asdict
from typing import Any, Callable


def _guard(fn: Callable[..., dict]) -> Callable[..., dict]:
    """Wrap a tool so failures come back as {'error': ...} instead of raising."""
    def wrapper(*args: Any, **kwargs: Any) -> dict:
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 - the contract is error dicts
            return {"error": f"{type(exc).__name__}: {exc}"}
    wrapper.__name__ = fn.__name__
    wrapper.__doc__ = fn.__doc__
    wrapper.__signature__ = inspect.signature(fn)  # keep manifest accurate
    return wrapper


def _station(latitude: float, longitude: float, timezone: str, name: str = "custom"):
    from .weather.stations import Station

    return Station("CUSTOM", name, latitude, longitude, timezone)


def _public_client(env: str = "prod"):
    from .config import Settings
    from .kalshi.client import KalshiClient

    settings = Settings(api_key_id=None, private_key_path=None, env=env,
                        max_position_usd=50.0, max_daily_loss_usd=100.0)
    return KalshiClient(settings)


# --------------------------------------------------------------------------
# Forecasting
# --------------------------------------------------------------------------

@_guard
def get_temperature_forecast(latitude: float, longitude: float, timezone: str,
                             date: str, unit_note: str = "fahrenheit") -> dict:
    """Multi-model (GFS/ECMWF/ICON) daily-high forecast for a location and ISO date.

    Returns mean_f, sigma_f (model spread, floored), and the per-model members.
    """
    from .weather.forecast import fetch_forecast

    fc = fetch_forecast(_station(latitude, longitude, timezone), date)
    if fc is None:
        return {"error": "no forecast data returned"}
    return {"date": fc.date, "mean_f": round(fc.mean_f, 2), "sigma_f": round(fc.sigma_f, 2),
            "members_f": fc.members, "n_models": fc.n_models}


@_guard
def get_forecast_calibration(latitude: float, longitude: float, timezone: str,
                             today: str, lead_days: int = 2, days: int = 45) -> dict:
    """Measured forecast bias and error spread (deg F) for a location.

    bias = mean(lead forecast - realized) over recent days. Subtract bias from a
    live forecast before pricing anything: gridpoints run systematically hot or
    cold vs specific stations, and uncorrected bias produced our worst phantom
    edges. ``today`` is an ISO date.
    """
    from .weather.forecast import station_calibration

    bias, sigma = station_calibration(_station(latitude, longitude, timezone),
                                      lead_days=lead_days, days=days, today=today)
    return {"bias_f": round(bias, 2), "sigma_f": round(sigma, 2),
            "lead_days": lead_days, "window_days": days}


@_guard
def temperature_range_probability(low: int | None, high: int | None,
                                  mean: float, sigma: float) -> dict:
    """P(daily high lands in [low, high]) under Normal(mean, sigma).

    Integer bounds are inclusive; pass low=None or high=None for open-ended
    ranges ("X or below" / "X or above"). Caveat from live testing: Normal tails
    are too thin — never bet a model-favored tail against a liquid market.
    """
    from .weather.model import range_probability

    p = range_probability((low, high), mean, sigma)
    return {"probability": round(p, 6)}


@_guard
def compute_degree_day_indices(daily_max_min: list, base: float = 65.0) -> dict:
    """Cumulative HDD/CDD/CAT over a strip of [tmax, tmin] daily pairs.

    These are the indices CME weather derivatives settle on (base 65F / 18C).
    """
    from .weather.indices import cumulative

    totals = cumulative([(float(a), float(b)) for a, b in daily_max_min], base=base)
    return {"days": totals.days, "hdd": round(totals.hdd, 2),
            "cdd": round(totals.cdd, 2), "cat": round(totals.cat, 2), "base": base}


# --------------------------------------------------------------------------
# Pricing / edge evaluation
# --------------------------------------------------------------------------

@_guard
def evaluate_binary_value(model_prob: float, yes_bid_cents: int, yes_ask_cents: int,
                          min_edge_cents: float = 3.0) -> dict:
    """Given model P(YES) and a two-sided quote, find the +EV side after Kalshi fees.

    Returns the recommended side/price/EV, or bet=None when neither side clears
    min_edge_cents. Validated guidance: NO-side fades of overpriced longshots are
    where the measured edge lives; treat large YES-side 'value' vs a liquid
    market as your model's error (it went 0-for-9 live).
    """
    from .weather.value import evaluate

    bet = evaluate("", "", model_prob, yes_bid_cents, yes_ask_cents,
                   min_edge_cents=min_edge_cents)
    if bet is None:
        return {"bet": None, "reason": f"no side clears {min_edge_cents}c after fees"}
    return {"bet": {"side": bet.side, "price_cents": bet.price_cents,
                    "ev_cents": round(bet.edge_cents, 2)}}


@_guard
def kalshi_trading_fee(price_cents: float, contracts: int = 1) -> dict:
    """Kalshi trading fee in cents: ceil(0.07 * C * P * (1-P))."""
    from .research.calibration import kalshi_fee_cents

    return {"fee_cents": kalshi_fee_cents(price_cents, contracts)}


# --------------------------------------------------------------------------
# Market data
# --------------------------------------------------------------------------

@_guard
def kalshi_orderbook(ticker: str, env: str = "prod") -> dict:
    """Kalshi order book normalized to {'yes': [[cents, size], ...], 'no': [...]}.

    Public endpoint; levels are best-price-last.
    """
    return {"ticker": ticker, "orderbook": _public_client(env).get_orderbook(ticker)}


@_guard
def kalshi_markets(series_ticker: str, status: str = "open", env: str = "prod",
                   limit: int = 100) -> dict:
    """List Kalshi markets for a series (e.g. KXHIGHNY). Public endpoint."""
    markets = _public_client(env).get_markets(
        series_ticker=series_ticker, status=status, limit=limit)
    return {"count": len(markets), "markets": markets}


@_guard
def polymarket_temperature_events(closed: bool = False, max_events: int = 15) -> dict:
    """Polymarket global daily-temperature events with parsed Celsius bins.

    Read-only (trading Polymarket requires crypto). Each bin includes the book
    mid, executable best bid/ask, and volume.
    """
    import requests

    from .polymarket.weather import fetch_temp_events, markets_from_event

    session = requests.Session()
    out = []
    for event in fetch_temp_events(session, closed=closed)[:max_events]:
        bins = markets_from_event(event)
        if not bins:
            continue
        out.append({
            "city": bins[0].city, "date": bins[0].date, "slug": event.get("slug", ""),
            "bins": [{"title": b.bin_title, "range": list(b.rng), "yes_mid": b.yes_price,
                      "best_bid": b.best_bid, "best_ask": b.best_ask, "volume": b.volume}
                     for b in bins],
        })
    return {"count": len(out), "events": out}


# --------------------------------------------------------------------------
# Paper-trading operations (stateful: ledger files on disk)
# --------------------------------------------------------------------------

@_guard
def open_kalshi_weather_fades(ledger_path: str, today: str, min_edge_cents: float = 4.0,
                              max_new: int = 10, env: str = "prod") -> dict:
    """Scan Kalshi high-temp markets and open paper NO fades with +EV after fees.

    Bias-calibrated, passive-mid entry, one position per event. Appends to the
    JSON ledger at ledger_path. ``today`` is an ISO date.
    """
    from .forward import ForwardLedger, scan_weather_filtered

    ledger = ForwardLedger(ledger_path)
    opened = scan_weather_filtered(ledger, _public_client(env), today=today,
                                   min_edge_cents=min_edge_cents, max_new=max_new)
    return {"opened": [asdict(p) for p in opened], "summary": ledger.summary()}


@_guard
def settle_kalshi_ledger(ledger_path: str, env: str = "prod") -> dict:
    """Settle any resolved positions in a Kalshi paper ledger; returns updated stats."""
    from .forward import ForwardLedger, settle_open

    ledger = ForwardLedger(ledger_path)
    n = settle_open(ledger, _public_client(env))
    return {"newly_settled": n, "summary": ledger.summary()}


@_guard
def settle_polymarket_ledger(ledger_path: str) -> dict:
    """Settle any resolved positions in a Polymarket paper ledger."""
    from .polymarket.forward import PolyLedger, settle_poly

    ledger = PolyLedger(ledger_path)
    n = settle_poly(ledger)
    return {"newly_settled": n, "summary": ledger.summary()}


@_guard
def validation_scorecard() -> dict:
    """Consolidated paper-trading scorecard across all ledgers.

    Includes a significance verdict per strategy; anything under 20 settled is
    flagged as noise by design — do not act on it.
    """
    from .report import build_report, render

    stats = build_report()
    return {
        "strategies": [{
            "label": s.label, "open": s.open_n, "settled": s.settled_n,
            "roi_pct": round(s.roi_pct, 2), "win_rate": round(s.win_rate, 3),
            "t_stat": round(s.t_stat, 2), "verdict": s.verdict,
        } for s in stats],
        "rendered": render(stats),
    }


# --------------------------------------------------------------------------
# Registry + manifest
# --------------------------------------------------------------------------

TOOL_REGISTRY: dict[str, Callable[..., dict]] = {
    fn.__name__: fn for fn in [
        get_temperature_forecast,
        get_forecast_calibration,
        temperature_range_probability,
        compute_degree_day_indices,
        evaluate_binary_value,
        kalshi_trading_fee,
        kalshi_orderbook,
        kalshi_markets,
        polymarket_temperature_events,
        open_kalshi_weather_fades,
        settle_kalshi_ledger,
        settle_polymarket_ledger,
        validation_scorecard,
    ]
}


def tool_manifest() -> list[dict]:
    """Framework-agnostic tool specs: name, description, parameters.

    Translate into your framework's schema (Hermes tool registration, MCP tool
    definitions, OpenAI function specs, ...). Parameter 'required' is True when
    the parameter has no default.
    """
    manifest = []
    for name, fn in TOOL_REGISTRY.items():
        params = []
        for pname, p in inspect.signature(fn).parameters.items():
            params.append({
                "name": pname,
                "required": p.default is inspect.Parameter.empty,
                "default": None if p.default is inspect.Parameter.empty else p.default,
            })
        manifest.append({
            "name": name,
            "description": inspect.getdoc(fn) or "",
            "parameters": params,
        })
    return manifest
