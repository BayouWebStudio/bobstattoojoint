# Hermes Export Guide

This repo is a validated prediction-market research system. The agent-facing
surface lives in **`kalshi_bot.hermes_tools`** — JSON-in/JSON-out functions with
a registry and manifest, designed to drop into Hermes (or any tool-calling
framework: MCP, OpenAI-style, CrewAI).

## Quickstart

```python
from kalshi_bot.hermes_tools import TOOL_REGISTRY, tool_manifest

# Register every tool with your framework:
for spec in tool_manifest():          # name, description, parameters
    fn = TOOL_REGISTRY[spec["name"]]
    my_framework.register_tool(spec, fn)

# Or call directly:
TOOL_REGISTRY["get_temperature_forecast"](40.78, -73.97, "America/New_York", "2026-07-04")
```

Contract: tools never raise — failures return `{"error": "..."}`. All outputs
are JSON-serializable. Public market data needs **no credentials**; Kalshi
trading/portfolio calls would need API keys (see `.env.example`), but nothing in
the tool layer requires them.

## The tools

| Tool | What it does |
|---|---|
| `get_temperature_forecast` | Multi-model daily-high forecast (Open-Meteo, free) |
| `get_forecast_calibration` | Measured per-station bias + error spread — **subtract bias before pricing anything** |
| `temperature_range_probability` | Normal-CDF probability for a temperature range |
| `compute_degree_day_indices` | HDD/CDD/CAT (what CME weather derivatives settle on) |
| `evaluate_binary_value` | +EV side of a binary quote after Kalshi fees |
| `kalshi_trading_fee` | Fee model: `ceil(0.07·C·P·(1−P))` |
| `kalshi_orderbook` / `kalshi_markets` | Public Kalshi market data (normalized) |
| `polymarket_temperature_events` | Global city temp markets with executable bid/ask (read-only) |
| `open_kalshi_weather_fades` | Open paper NO fades (bias-calibrated, +EV-gated) into a ledger file |
| `settle_kalshi_ledger` / `settle_polymarket_ledger` | Resolve paper positions, tally P&L |
| `validation_scorecard` | Per-strategy ROI / win rate / t-stat with a significance verdict |

## What the validation established (read before trading anything)

Full detail in [docs/STRATEGY.md](docs/STRATEGY.md). The condensed lessons an
agent should inherit:

1. **The one measured edge**: fading overpriced longshots (5–15¢ YES) on Kalshi
   — a behavioral favorite-longshot bias. ~+2.5% crossing the spread, ~+3.5%
   passive, per settled-market backtests. Forward test in progress.
2. **Never bet a model-favored tail against a liquid market.** Our forecast-value
   YES-tail bets went 0-for-9 live on Polymarket. Big model-vs-market
   disagreement = your model's error. Use forecasts as a **veto** (skip fades
   the forecast says are live), not as an oracle.
3. **Bias-calibrate every forecast** (`get_forecast_calibration`); raw gridpoint
   forecasts produced phantom edges (LAX ran hot by several °F).
4. **Fees and spread eat small edges.** Deep 1–2¢ longshots are no-margin traps;
   passive entry is worth ~⅓ of the whole edge.
5. **Skewed payoffs demand diversification and patience**: fade books win small
   ~85–95% of the time and occasionally lose ~90¢ in one shot (a 4% NYC rain
   tail hit for −90¢ in our forward test). Judge nothing under 20 settled
   positions and t > 2 — `validation_scorecard` enforces this framing.
6. **Capacity is small.** The edge lives in thin books; it's a few-hundred-
   dollars-a-month system at current Kalshi liquidity, which is why this repo is
   in harvest mode rather than build-out.

## Daily ops loop (also usable by an agent)

```bash
python -m kalshi_bot polyweather settle
KALSHI_ENV=prod python -m kalshi_bot forward settle --ledger data/forward_ledger_weather.json
KALSHI_ENV=prod python -m kalshi_bot forward settle --ledger data/forward_ledger.json
python -m kalshi_bot report
```

Settled results append permanently to `data/results_log.csv` and
`data/poly_results_log.csv`. Ledgers are committed to git so state survives
ephemeral environments.
