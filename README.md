# Kalshi Trading Bot

An automated trading bot for [Kalshi](https://kalshi.com) prediction markets.

Kalshi is a US-regulated (CFTC) event exchange with an official REST + WebSocket
API built for programmatic trading. Unlike traditional sportsbooks, an exchange
does not ban or limit winning accounts — making it a sound foundation for an
automated strategy.

> ⚠️ **Status: paper-trading scaffold.** This project starts in *paper mode*:
> it simulates fills against real (or mocked) order books so you can validate a
> strategy before risking real money. Live order execution is gated behind an
> explicit flag and a kill-switch.

## Why this design

- **Paper-trading first.** Edges in prediction markets are thin and competitive.
  We validate an edge in simulation before going live.
- **Detection before execution.** Strategies emit *signals*; the engine decides
  whether to (paper-)execute. Live trading is opt-in.
- **Real auth, real client.** The Kalshi client implements RSA-PSS request
  signing, so it works against the live/demo API the moment you add keys.

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# Paper-trade a strategy against a built-in mock feed (no keys needed):
python -m kalshi_bot run --mock

# Scan a mutually-exclusive event for partition arbitrage (synthetic demo):
python -m kalshi_bot arb --mock

# Cross-venue arbitrage between Kalshi and Polymarket (synthetic demo):
python -m kalshi_bot xarb --mock

# ...and actually execute both legs on paper (with leg-risk unwind):
python -m kalshi_bot xarb --mock --execute --contracts 100

# Record live market data to CSV, then backtest over the real history:
python -m kalshi_bot record --tickers KXMARKET --out data.csv   # needs API keys
python -m kalshi_bot backtest --csv data.csv

# Backtest a strategy over synthetic data and print performance metrics:
python -m kalshi_bot backtest --mock --ticks 400

# Backtest over REAL Kalshi candlestick history (public data, no keys needed):
python -m kalshi_bot backtest --series KXWARMING --kalshi-ticker KXWARMING-50 --days 90

# Measure the favorite-longshot edge on real settled markets (see docs/STRATEGY.md):
python -m kalshi_bot calibrate --max-yes-ask 15 --entry mid

# Weather: live forecast (Open-Meteo) vs market for high-temp markets (diagnostic)
python -m kalshi_bot weather --cities KXHIGHNY KXHIGHCHI

# Forward (out-of-sample) paper test: open live positions now, settle them later
python -m kalshi_bot forward scan      # open paper NO positions on live longshots
python -m kalshi_bot forward settle    # mark any now-settled positions (run over days)
python -m kalshi_bot forward status    # running out-of-sample P&L

# Fast feedback loop: target daily series (crypto/temperature) settling within ~24h
python -m kalshi_bot forward scan --daily --within-hours 30 \
    --ledger data/forward_ledger_daily.json

# Run tests
pytest
```

### Live trading (opt-in, guarded)

Live order placement is gated behind an explicit confirmation flag, requires API
credentials, and runs with the risk guard / kill-switch active:

```bash
python -m kalshi_bot run --live --i-understand-live-risk \
    --tickers KXSOMEMARKET --ws
```

While live, **create a file named `STOP`** in the working directory to latch the
kill-switch and halt trading immediately. The guard also halts automatically on
the daily-loss limit (`MAX_DAILY_LOSS_USD`).

## Configuration

Copy `.env.example` to `.env` and fill in your Kalshi API credentials when you
are ready to use live market data or live trading.

```
KALSHI_API_KEY_ID=...           # API key UUID from Kalshi
KALSHI_PRIVATE_KEY_PATH=...     # path to your RSA private key (PEM)
KALSHI_ENV=demo                 # "demo" or "prod"
```

## Architecture

```
src/kalshi_bot/
  config.py            # env-driven settings
  kalshi/
    auth.py            # RSA-PSS request signing
    client.py          # REST client (markets, order book, orders, balance)
    feed.py            # REST-polling feed + offline mock feeds
    ws.py              # WebSocket feed + testable OrderBookState
    history.py         # Kalshi candlestick history -> backtest snapshots
  execution/
    base.py            # Broker protocol + Fill (paper & live share it)
    live.py            # LiveBroker: routes orders to the Kalshi API
    crossvenue.py      # CrossVenueExecutor: two-leg execution + leg-risk unwind
  paper/
    broker.py          # paper broker: simulates fills, tracks P&L
  strategy/
    base.py            # Strategy interface + Signal type
    threshold.py       # example: mean-reversion threshold strategy
    fade_longshot.py   # favorite-longshot-bias strategy (the measured edge)
    arbitrage.py       # partition arbitrage detector (mutually-exclusive events)
    crossvenue.py      # cross-venue arbitrage detector (cheapest YES + cheapest NO)
  research/
    calibration.py     # calibration table + settlement backtest + Kalshi fees
    collect.py         # build settlement samples from the live API
  venues/
    base.py            # Quote + Venue protocol (normalizes venues to cents)
    kalshi_venue.py    # Kalshi as a Venue
    polymarket.py      # Polymarket CLOB client + Venue (read-only)
  risk/
    sizing.py          # Kelly-criterion position sizing
    guard.py           # risk guard / kill-switch (loss + position limits, STOP file)
  backtest/
    record.py          # record/replay snapshots as CSV
    runner.py          # backtest + metrics (return, drawdown, win rate, Sharpe)
  engine.py            # main loop: feed -> strategy -> risk -> broker
  arb.py               # multi-leg (partition) arbitrage runner
  xarb.py              # cross-venue arbitrage runner
  cli.py               # CLI entry point (run / arb / xarb / backtest / record)
```

## Roadmap

- [x] Paper-trading engine with mock feed
- [x] Kalshi REST client with API-key auth
- [x] Kelly sizing + risk limits
- [x] WebSocket live feed (lower latency than REST polling)
- [x] Cross-market arbitrage detection (mutually-exclusive partitions)
- [x] Backtesting with performance metrics
- [x] Live execution with kill-switch
- [x] Cross-venue arbitrage detection (Kalshi vs. Polymarket)
- [x] Record live data to CSV for realistic backtests
- [x] Cross-venue execution with leg-risk unwind (paper)
- [ ] Polymarket order placement (EIP-712 signing + Polygon settlement) — the
      last piece needed for *live* cross-venue execution
- [ ] Richer fill model (partial fills, queue position, fees)

## Paper testing against real data

Kalshi's market-data endpoints (markets, order books, candlesticks) are public,
so the bot is validated against **real** markets without credentials:

- **Arbitrage detection** was run on the live next-pope event. This surfaced a
  correctness bug: the partition detector assumed any set of outcomes is a
  *complete* partition. A real event listing 7 named candidates (but not "any
  other") summed to 27c and looked like a 73c "guaranteed arb". The detector now
  applies a completeness guard (a true partition's YES bids sum near 100c) and
  correctly rejects non-exhaustive sets.
- **The live order-book schema had changed** (`orderbook_fp` with dollar-string
  levels); the REST client now normalizes both the current and legacy schemas to
  integer cents.
- **Strategy backtests** run on real candlestick history (e.g. a market that
  moved 38c -> 81c -> 26c over 120 days), exercising the full
  feed -> strategy -> broker -> metrics pipeline on genuine prices.

## Does it have an edge?

Yes — a modest, real one. A hypothesis-driven search (documented in
[docs/STRATEGY.md](docs/STRATEGY.md)) found the **favorite-longshot bias** on
Kalshi: longshot contracts (YES ≤ ~15¢) are overpriced and resolve YES less
often than their price implies. Fading them (buying NO, holding to settlement)
returns roughly **+2.5% after fees crossing the spread, ~+3.5% with passive
entry**, measured on ~100 real settled markets.

It is real but **modest, skewed (rare large losses), and capacity-limited** — a
behavioral edge to deploy with small size and broad diversification, not a money
printer. The naive mean-reversion strategy, by contrast, does *not* beat costs.
Reproduce the measurement with `python -m kalshi_bot calibrate`.

**Out-of-sample validation in progress.** A backtest can overfit, so a live
forward test is running: 40 paper NO positions were opened on real deep
longshots (recorded in `data/forward_ledger.json`, diversified one-per-event,
entered at the conservative cross-the-spread price). Run `forward settle` over
the coming days/weeks as those markets resolve to accumulate genuine
out-of-sample results — this is the real test of the edge, and we evaluate it
before investing in a passive-execution layer.

**Weather forecasting (honest negative result).** We built a live forecast model
(`weather/`, Open-Meteo GFS/ECMWF/ICON) to price temperature markets directly.
It is well-calibrated, but live testing found **no demonstrated edge**: on
same-day markets the model is *less* informed than the market (which sees
intraday data), and at lead time, model-vs-market disagreement is not proof the
model is right. See [docs/STRATEGY.md](docs/STRATEGY.md) — the next step is a
historical-forecast backtest before trusting any weather value signal.

## Disclaimer

For educational and research use. Trading involves risk of loss. You are
responsible for complying with Kalshi's terms and the laws of your jurisdiction.
