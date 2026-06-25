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

# Run a paper-trading demo against a built-in mock market feed (no keys needed):
python -m kalshi_bot run --paper --mock

# Run tests
pytest
```

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
    feed.py            # market-data feed (live REST poller + mock feed)
  paper/
    broker.py          # paper broker: simulates fills against the order book
  strategy/
    base.py            # Strategy interface + Signal type
    threshold.py       # example: mean-reversion threshold strategy
  risk/
    sizing.py          # Kelly-criterion position sizing + risk limits
  engine.py            # main loop: feed -> strategy -> risk -> broker
  cli.py               # command-line entry point
```

## Roadmap

- [x] Paper-trading engine with mock feed
- [x] Kalshi REST client with API-key auth
- [x] Kelly sizing + risk limits
- [ ] WebSocket live feed (lower latency than REST polling)
- [ ] Cross-market arbitrage detection
- [ ] Backtesting over historical data
- [ ] Live execution with kill-switch

## Disclaimer

For educational and research use. Trading involves risk of loss. You are
responsible for complying with Kalshi's terms and the laws of your jurisdiction.
