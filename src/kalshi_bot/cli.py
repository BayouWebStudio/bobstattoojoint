"""Command-line entry point."""

from __future__ import annotations

import argparse
import logging
import sys

from .config import load_settings
from .engine import Engine
from .kalshi.client import KalshiClient
from .kalshi.feed import MockFeed, RestPollingFeed
from .paper.broker import PaperBroker
from .risk.sizing import Sizer
from .strategy.threshold import ThresholdStrategy


def _load_dotenv() -> None:
    """Minimal .env loader (avoids a hard dependency on python-dotenv)."""
    import os
    from pathlib import Path

    path = Path(".env")
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="kalshi-bot", description="Kalshi trading bot")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="Run the bot")
    run.add_argument("--paper", action="store_true", default=True, help="Paper mode (default)")
    run.add_argument("--live", action="store_true", help="Enable live execution (NOT IMPLEMENTED)")
    run.add_argument("--mock", action="store_true", help="Use the offline mock feed")
    run.add_argument("--tickers", nargs="*", default=[], help="Market tickers for the live feed")
    run.add_argument("--ticks", type=int, default=100, help="Number of feed iterations")
    run.add_argument("--bankroll", type=float, default=1000.0, help="Paper bankroll in USD")
    return parser


def cmd_run(args: argparse.Namespace) -> int:
    if args.live:
        print("Live execution is intentionally not implemented yet. Use --paper.", file=sys.stderr)
        return 2

    settings = load_settings()
    strategy = ThresholdStrategy()
    broker = PaperBroker(starting_cash_usd=args.bankroll)
    sizer = Sizer(bankroll_usd=args.bankroll, max_position_usd=settings.max_position_usd)
    engine = Engine(strategy=strategy, sizer=sizer, broker=broker)

    if args.mock or not settings.has_credentials:
        if not args.mock:
            print("No API credentials found; falling back to the mock feed.", file=sys.stderr)
        feed = MockFeed().stream(max_ticks=args.ticks)
    else:
        if not args.tickers:
            print("Live feed requires --tickers TICKER [TICKER ...]", file=sys.stderr)
            return 2
        client = KalshiClient(settings)
        feed = RestPollingFeed(client, args.tickers).stream(max_ticks=args.ticks)

    engine.run(feed)
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    _load_dotenv()
    args = build_parser().parse_args(argv)
    if args.command == "run":
        return cmd_run(args)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
