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

# Backtest a strategy over synthetic data and print performance metrics:
python -m kalshi_bot backtest --mock --ticks 400

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
  execution/
    base.py            # Broker protocol + Fill (paper & live share it)
    live.py            # LiveBroker: routes orders to the Kalshi API
  paper/
    broker.py          # paper broker: simulates fills, tracks P&L
  strategy/
    base.py            # Strategy interface + Signal type
    threshold.py       # example: mean-reversion threshold strategy
    arbitrage.py       # partition arbitrage detector (mutually-exclusive events)
  risk/
    sizing.py          # Kelly-criterion position sizing
    guard.py           # risk guard / kill-switch (loss + position limits, STOP file)
  backtest/
    record.py          # record/replay snapshots as CSV
    runner.py          # backtest + metrics (return, drawdown, win rate, Sharpe)
  engine.py            # main loop: feed -> strategy -> risk -> broker
  arb.py               # multi-leg arbitrage runner
  cli.py               # command-line entry point (run / arb / backtest)
```

## Roadmap

- [x] Paper-trading engine with mock feed
- [x] Kalshi REST client with API-key auth
- [x] Kelly sizing + risk limits
- [x] WebSocket live feed (lower latency than REST polling)
- [x] Cross-market arbitrage detection (mutually-exclusive partitions)
- [x] Backtesting with performance metrics
- [x] Live execution with kill-switch
- [ ] Cross-venue arbitrage (Kalshi vs. Polymarket)
- [ ] Persist live-recorded data for realistic backtests
- [ ] Richer fill model (partial fills, queue position, fees)

## Disclaimer

For educational and research use. Trading involves risk of loss. You are
responsible for complying with Kalshi's terms and the laws of your jurisdiction.
