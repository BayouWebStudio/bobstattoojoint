"""Command-line entry point.

Subcommands:
  run        paper- or live-trade a strategy against a feed
  arb        scan a mutually-exclusive event for partition arbitrage
  backtest   replay recorded/synthetic snapshots through a strategy
"""

from __future__ import annotations

import argparse
import logging
import sys

from .arb import run_arbitrage
from .backtest import backtest, read_snapshots
from .backtest.record import record_feed
from .config import load_settings
from .engine import Engine
from .execution.crossvenue import CrossVenueExecutor
from .execution.live import LiveBroker
from .kalshi.client import KalshiClient
from .kalshi.feed import MockEventFeed, MockFeed, RestPollingFeed
from .paper.broker import PaperBroker
from .risk.guard import RiskGuard
from .risk.sizing import Sizer
from .strategy.arbitrage import ArbitrageDetector
from .strategy.threshold import ThresholdStrategy
from .venues.base import MockVenue, Quote
from .xarb import run_cross_venue


def _now_ts() -> int:
    import time

    return int(time.time())


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


def _live_feed(settings, tickers, ticks, use_ws):
    if use_ws:
        from .kalshi.ws import WebSocketFeed

        return WebSocketFeed(settings, tickers).stream(max_messages=ticks)
    client = KalshiClient(settings)
    return RestPollingFeed(client, tickers).stream(max_ticks=ticks)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="kalshi-bot", description="Kalshi trading bot")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="Run a strategy (paper by default)")
    run.add_argument("--live", action="store_true", help="Place REAL orders (requires confirmation)")
    run.add_argument("--i-understand-live-risk", action="store_true",
                     help="Required confirmation flag for --live")
    run.add_argument("--mock", action="store_true", help="Use the offline mock feed")
    run.add_argument("--ws", action="store_true", help="Use the WebSocket feed (live data)")
    run.add_argument("--tickers", nargs="*", default=[], help="Market tickers for the live feed")
    run.add_argument("--ticks", type=int, default=100, help="Number of feed iterations")
    run.add_argument("--bankroll", type=float, default=1000.0, help="Bankroll in USD")

    arb = sub.add_parser("arb", help="Scan a mutually-exclusive event for arbitrage")
    arb.add_argument("--mock", action="store_true", help="Use the synthetic event feed")
    arb.add_argument("--tickers", nargs="*", default=[], help="Tickers that partition one event")
    arb.add_argument("--ticks", type=int, default=40, help="Number of feed iterations")
    arb.add_argument("--threshold", type=int, default=1, help="Min guaranteed profit (cents)")

    bt = sub.add_parser("backtest", help="Backtest a strategy over snapshots")
    bt.add_argument("--csv", help="CSV of recorded snapshots (see backtest.record)")
    bt.add_argument("--mock", action="store_true", help="Generate synthetic data instead")
    bt.add_argument("--kalshi-ticker", help="Backtest over real Kalshi candlestick history")
    bt.add_argument("--series", help="Series ticker for --kalshi-ticker (e.g. KXWARMING)")
    bt.add_argument("--days", type=int, default=90, help="Days of history for --kalshi-ticker")
    bt.add_argument("--interval", type=int, default=1440, help="Candle interval minutes (1/60/1440)")
    bt.add_argument("--end-ts", type=int, default=0, help="History end unix ts (0 = newest available)")
    bt.add_argument("--ticks", type=int, default=500, help="Mock snapshots to generate")
    bt.add_argument("--threshold", type=float, default=2.0, help="Strategy threshold (cents)")
    bt.add_argument("--bankroll", type=float, default=1000.0, help="Bankroll in USD")

    xarb = sub.add_parser("xarb", help="Cross-venue arbitrage (Kalshi vs Polymarket)")
    xarb.add_argument("--mock", action="store_true", help="Run a synthetic two-venue demo")
    xarb.add_argument("--execute", action="store_true",
                      help="Paper-execute both legs of each opportunity")
    xarb.add_argument("--contracts", type=int, default=1, help="Contracts per arb set")
    xarb.add_argument("--event", default="event", help="Event name (live mode)")
    xarb.add_argument("--kalshi-ticker", help="Kalshi market ticker (live mode)")
    xarb.add_argument("--poly-yes-token", help="Polymarket YES token id (live mode)")
    xarb.add_argument("--poly-no-token", help="Polymarket NO token id (live mode)")
    xarb.add_argument("--threshold", type=int, default=1, help="Min guaranteed profit (cents)")
    xarb.add_argument("--rounds", type=int, default=1, help="Number of polling rounds")

    cal = sub.add_parser("calibrate",
                         help="Measure favorite-longshot bias on settled Kalshi markets")
    cal.add_argument("--series", nargs="*", default=[],
                     help="Series tickers to sample (default: a liquid election set)")
    cal.add_argument("--max-yes-ask", type=int, default=15, help="Longshot threshold (cents)")
    cal.add_argument("--entry", choices=["mid", "ask"], default="mid",
                     help="Entry model: passive mid or aggressive (cross spread)")
    cal.add_argument("--max-calls", type=int, default=500, help="Max candlestick API calls")

    rec = sub.add_parser("record", help="Record live market snapshots to CSV for backtesting")
    rec.add_argument("--out", required=True, help="Output CSV path")
    rec.add_argument("--tickers", nargs="*", default=[], help="Market tickers to record")
    rec.add_argument("--ticks", type=int, default=500, help="Number of feed iterations")
    rec.add_argument("--ws", action="store_true", help="Use the WebSocket feed (live data)")
    rec.add_argument("--mock", action="store_true", help="Record the synthetic feed (for testing)")
    return parser


def cmd_run(args: argparse.Namespace) -> int:
    settings = load_settings()
    strategy = ThresholdStrategy()
    sizer = Sizer(bankroll_usd=args.bankroll, max_position_usd=settings.max_position_usd)

    if args.live:
        if not args.i_understand_live_risk:
            print("Refusing to trade live without --i-understand-live-risk.", file=sys.stderr)
            return 2
        if not settings.has_credentials:
            print("Live trading requires API credentials (see .env.example).", file=sys.stderr)
            return 2
        if not args.tickers:
            print("Live trading requires --tickers.", file=sys.stderr)
            return 2
        client = KalshiClient(settings)
        broker = LiveBroker(client)
        guard = RiskGuard(
            max_daily_loss_usd=settings.max_daily_loss_usd,
            max_position_contracts=int(settings.max_position_usd),  # conservative proxy
            stop_file="STOP",
        )
        feed = _live_feed(settings, args.tickers, args.ticks, args.ws)
        print("⚠️  LIVE trading enabled. Touch a file named 'STOP' to halt.", file=sys.stderr)
        Engine(strategy=strategy, sizer=sizer, broker=broker, guard=guard).run(feed)
        return 0

    # Paper mode.
    broker = PaperBroker(starting_cash_usd=args.bankroll)
    if args.mock or not settings.has_credentials:
        if not args.mock:
            print("No API credentials found; falling back to the mock feed.", file=sys.stderr)
        feed = MockFeed().stream(max_ticks=args.ticks)
    else:
        if not args.tickers:
            print("Live feed requires --tickers TICKER [TICKER ...]", file=sys.stderr)
            return 2
        feed = _live_feed(settings, args.tickers, args.ticks, args.ws)
    Engine(strategy=strategy, sizer=sizer, broker=broker).run(feed)
    return 0


def cmd_arb(args: argparse.Namespace) -> int:
    broker = PaperBroker()
    if args.mock or not args.tickers:
        feed = MockEventFeed().stream(max_ticks=args.ticks)
        groups = {MockEventFeed.EVENT: list(MockEventFeed.TICKERS)}
    else:
        settings = load_settings()
        client = KalshiClient(settings)
        feed = RestPollingFeed(client, args.tickers).stream(max_ticks=args.ticks)
        groups = {"event": list(args.tickers)}

    detector = ArbitrageDetector(groups, threshold_cents=args.threshold)
    taken = run_arbitrage(feed, detector, broker)
    print(f"Done. {len(taken)} arbitrage opportunities taken (paper).")
    return 0


def cmd_backtest(args: argparse.Namespace) -> int:
    if args.csv:
        snapshots = read_snapshots(args.csv)
    elif args.kalshi_ticker:
        if not args.series:
            print("--kalshi-ticker requires --series (e.g. --series KXWARMING).", file=sys.stderr)
            return 2
        from .kalshi.history import candlesticks_to_snapshots

        settings = load_settings()
        client = KalshiClient(settings)
        end_ts = args.end_ts or _now_ts()
        candles = client.get_candlesticks(
            args.series, args.kalshi_ticker, end_ts - args.days * 86400, end_ts, args.interval
        )
        snapshots = candlesticks_to_snapshots(candles, args.kalshi_ticker)
        snaps = list(snapshots)
        print(f"Loaded {len(snaps)} real bars for {args.kalshi_ticker}")
        if not snaps:
            print("No usable history returned (check --series/--end-ts).", file=sys.stderr)
            return 2
        snapshots = snaps
    elif args.mock:
        snapshots = MockFeed().stream(max_ticks=args.ticks)
    else:
        print("backtest requires --csv PATH, --kalshi-ticker, or --mock.", file=sys.stderr)
        return 2

    strategy = ThresholdStrategy(threshold_cents=args.threshold)
    report = backtest(strategy, snapshots, bankroll_usd=args.bankroll)
    print(report)
    return 0


def _build_executor(args: argparse.Namespace) -> CrossVenueExecutor | None:
    """A paper executor (one paper broker per venue) when --execute is set.

    Live cross-venue execution is intentionally not wired: Polymarket order
    placement is not implemented, so executing for real would fill only the
    Kalshi leg and leave the other naked.
    """
    if not args.execute:
        return None
    brokers = {"kalshi": PaperBroker(), "polymarket": PaperBroker()}
    guard = RiskGuard(stop_file="STOP")
    return CrossVenueExecutor(brokers=brokers, guard=guard)


def cmd_xarb(args: argparse.Namespace) -> int:
    executor = _build_executor(args)

    if args.mock or not args.kalshi_ticker:
        # Synthetic demo: YES cheapest on Kalshi, NO cheapest on Polymarket.
        kalshi = MockVenue("kalshi", {
            "DEMO": Quote("kalshi", "DEMO", yes_bid=46, yes_ask=48, no_bid=52, no_ask=54),
        })
        poly = MockVenue("polymarket", {
            "DEMO": Quote("polymarket", "DEMO", yes_bid=53, yes_ask=55, no_bid=45, no_ask=47),
        })
        links = [("DEMO", {"kalshi": "DEMO", "polymarket": "DEMO"})]
        venues = {"kalshi": kalshi, "polymarket": poly}
    else:
        if not (args.poly_yes_token and args.poly_no_token):
            print("Live xarb requires --poly-yes-token and --poly-no-token.", file=sys.stderr)
            return 2
        from .venues.kalshi_venue import KalshiVenue
        from .venues.polymarket import PolymarketClient, PolymarketVenue

        settings = load_settings()
        kalshi = KalshiVenue(KalshiClient(settings))
        poly = PolymarketVenue(
            PolymarketClient(),
            {args.event: (args.poly_yes_token, args.poly_no_token)},
        )
        links = [(args.event, {"kalshi": args.kalshi_ticker, "polymarket": args.event})]
        venues = {"kalshi": kalshi, "polymarket": poly}

    found = run_cross_venue(
        links, venues, executor=executor, contracts=args.contracts,
        threshold_cents=args.threshold, rounds=args.rounds,
    )
    print(f"Done. {len(found)} cross-venue opportunities found.")
    if executor is not None:
        print(f"Execution (paper): {executor.summary()}")
    return 0


def cmd_calibrate(args: argparse.Namespace) -> int:
    from .research.calibration import calibration_table, fade_longshot_backtest
    from .research.collect import collect_settled_samples

    client = KalshiClient(load_settings())
    print("Collecting settled-market samples from Kalshi (public data)...")
    samples = collect_settled_samples(
        client, args.series or None, max_candlestick_calls=args.max_calls
    )
    if not samples:
        print("No samples collected (try different --series).", file=sys.stderr)
        return 2

    print(f"\nCalibration over {len(samples)} settled markets:")
    print(f"  {'band':10} {'n':>4} {'implied':>8} {'realized':>9} {'gap':>6}")
    for b in calibration_table(samples):
        if b.n < 5:
            continue
        print(f"  {f'{b.low}-{b.high}':10} {b.n:>4} {b.implied_pct:>7.0f}% "
              f"{b.realized_pct:>8.1f}% {b.gap:>+6.1f}")

    print(f"\nFade-longshot backtest (YES ask <= {args.max_yes_ask}c, {args.entry} entry, "
          f"Kalshi fees):")
    stats = fade_longshot_backtest(
        samples, max_yes_ask=args.max_yes_ask, entry=args.entry
    )
    print(f"  {stats}")
    print("  caveat: outcomes cluster within a few events, so the t-stat overstates "
          "significance; treat as directional.")
    return 0


def cmd_record(args: argparse.Namespace) -> int:
    if args.mock:
        feed = MockFeed().stream(max_ticks=args.ticks)
    else:
        settings = load_settings()
        if not settings.has_credentials:
            print("Recording live data requires API credentials (see .env.example).",
                  file=sys.stderr)
            return 2
        if not args.tickers:
            print("record requires --tickers TICKER [TICKER ...] (or --mock).", file=sys.stderr)
            return 2
        feed = _live_feed(settings, args.tickers, args.ticks, args.ws)

    count = 0
    for _ in record_feed(feed, args.out):
        count += 1
    print(f"Recorded {count} snapshots to {args.out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    _load_dotenv()
    args = build_parser().parse_args(argv)
    if args.command == "run":
        return cmd_run(args)
    if args.command == "arb":
        return cmd_arb(args)
    if args.command == "backtest":
        return cmd_backtest(args)
    if args.command == "record":
        return cmd_record(args)
    if args.command == "xarb":
        return cmd_xarb(args)
    if args.command == "calibrate":
        return cmd_calibrate(args)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
