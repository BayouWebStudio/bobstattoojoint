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

    fwd = sub.add_parser("forward", help="Forward (out-of-sample) paper test of fade-longshot")
    fwd.add_argument("fwd_action", choices=["scan", "settle", "status"],
                     help="scan: open new positions; settle: resolve them; status: show state")
    fwd.add_argument("--ledger", default="data/forward_ledger.json", help="Ledger JSON path")
    fwd.add_argument("--max-yes-ask", type=int, default=15, help="Longshot threshold (cents)")
    fwd.add_argument("--max-new", type=int, default=40, help="Max new positions per scan")
    fwd.add_argument("--contracts", type=int, default=10, help="Contracts per position")
    fwd.add_argument("--min-volume", type=float, default=1000.0, help="Min market volume")
    fwd.add_argument("--daily", action="store_true",
                     help="Target fast-settling daily series (crypto/temperature/indices)")
    fwd.add_argument("--within-hours", type=float, default=None,
                     help="Only open positions settling within this many hours")
    fwd.add_argument("--series", nargs="*", default=[], help="Explicit series to scan")
    fwd.add_argument("--weather", action="store_true",
                     help="Forecast-filtered fade-longshot on weather markets (the double edge)")
    fwd.add_argument("--precip", action="store_true",
                     help="Forecast-driven fade on rain markets (KXRAINNYC)")
    fwd.add_argument("--min-edge", type=float, default=2.0,
                     help="Min forecast EV per contract in cents (weather mode)")
    fwd.add_argument("--lead-min", type=int, default=1, help="Min lead days (weather mode)")
    fwd.add_argument("--lead-max", type=int, default=3, help="Max lead days (weather mode)")
    fwd.add_argument("--min-yes-ask", type=int, default=3,
                     help="Skip longshots cheaper than this YES ask (weather mode)")
    fwd.add_argument("--entry", choices=["mid", "ask"], default="mid",
                     help="Entry: passive mid or aggressive cross-spread (weather mode)")

    wx = sub.add_parser("weather",
                        help="Forecast-vs-market value bets on Kalshi high-temp markets")
    wx.add_argument("--cities", nargs="*", default=[],
                    help="Series to scan (default: all weather cities)")
    wx.add_argument("--min-edge", type=float, default=3.0, help="Min EV per contract (cents)")

    wxb = sub.add_parser("weather-backtest",
                         help="Double-edge backtest: forecast-filtered fade-longshot at lead")
    wxb.add_argument("--lead-days", type=int, default=3, help="Forecast/entry lead (days)")
    wxb.add_argument("--per-city", type=int, default=45, help="Max settled markets per city")

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


def cmd_forward(args: argparse.Namespace) -> int:
    import datetime as dt

    from .forward import (
        ForwardLedger, scan_and_open, scan_precip, scan_weather_filtered, settle_open,
    )

    ledger = ForwardLedger(args.ledger)
    client = KalshiClient(load_settings())

    # Liquid daily series that settle within ~24h (crypto, temperature, indices).
    DAILY_SERIES = [
        "KXETH", "KXETHD", "KXBTC", "KXBTCD",
        "KXHIGHNY", "KXHIGHCHI", "KXHIGHMIA", "KXHIGHLAX", "KXHIGHAUS",
        "KXHIGHDEN", "KXHIGHPHIL", "KXINXU", "KXINXD", "KXNASDAQ100U", "KXNASDAQ100D",
    ]

    if args.fwd_action == "scan":
        today = dt.date.today().isoformat()
        if args.precip:
            opened = scan_precip(ledger, client, today=today, min_lead_days=args.lead_min,
                                 max_lead_days=args.lead_max, min_edge_cents=args.min_edge,
                                 min_volume=args.min_volume)
            print(f"Opened {len(opened)} forecast-driven rain fades (EV >= {args.min_edge}c).")
            for p in opened:
                print(f"  {p.ticker[:40]:42} NO@{p.entry_no_cost}c (YES {p.entry_yes_bid}/"
                      f"{p.entry_yes_ask}) P(rain) {p.model_prob*100:.1f}% EV {p.edge_cents:+.1f}c "
                      f"wx={p.weather_date}")
            print(f"\n{ledger.summary()}")
            return 0
        if args.weather:
            opened = scan_weather_filtered(
                ledger, client, today=today, min_lead_days=args.lead_min,
                max_lead_days=args.lead_max, min_yes_ask=args.min_yes_ask,
                max_yes_ask=args.max_yes_ask, min_edge_cents=args.min_edge,
                entry=args.entry, min_volume=args.min_volume,
                contracts=args.contracts, max_new=args.max_new,
            )
            print(f"Opened {len(opened)} forecast-value weather fades "
                  f"(EV >= {args.min_edge}c, YES {args.min_yes_ask}-{args.max_yes_ask}c, "
                  f"{args.entry} entry).")
            for p in opened[:20]:
                print(f"  {p.ticker[:40]:42} NO@{p.entry_no_cost}c (YES {p.entry_yes_bid}/"
                      f"{p.entry_yes_ask}) model {p.model_prob*100:.1f}% EV {p.edge_cents:+.1f}c "
                      f"fc={p.forecast_mean}F wx={p.weather_date}")
            print(f"\n{ledger.summary()}")
            return 0
        series = args.series or (DAILY_SERIES if args.daily else None)
        now_iso = dt.datetime.now(dt.timezone.utc).isoformat() if args.within_hours else None
        opened = scan_and_open(
            ledger, client, today=today, series=series, within_hours=args.within_hours,
            now_iso=now_iso, max_yes_ask=args.max_yes_ask, max_new=args.max_new,
            contracts=args.contracts, min_volume=args.min_volume,
        )
        print(f"Opened {len(opened)} new paper NO positions (fade-longshot).")
        for p in opened[:15]:
            print(f"  {p.ticker[:42]:44} NO@{p.entry_no_cost}c x{p.contracts} "
                  f"(YES {p.entry_yes_bid}/{p.entry_yes_ask}) close={p.close_time[:10]} [{p.category}]")
        print(f"\n{ledger.summary()}")
        return 0

    if args.fwd_action == "settle":
        n = settle_open(ledger, client)
        print(f"Settled {n} positions.")
        print(ledger.summary())
        return 0

    # status
    print(ledger.summary())
    for p in ledger.settled_positions():
        print(f"  [settled] {p.ticker[:40]:42} {p.result} P&L {p.realized_pnl_cents:+.1f}c")
    for p in ledger.open_positions()[:20]:
        print(f"  [open]    {p.ticker[:40]:42} NO@{p.entry_no_cost}c close={p.close_time[:10]}")
    return 0


def cmd_weather(args: argparse.Namespace) -> int:
    from .weather.scan import scan_city
    from .weather.stations import STATIONS

    client = KalshiClient(load_settings())
    cities = args.cities or list(STATIONS.keys())
    all_bets = []
    for series in cities:
        try:
            scan = scan_city(client, series, min_edge_cents=args.min_edge)
        except Exception as exc:  # noqa: BLE001
            print(f"{series}: error ({exc})", file=sys.stderr)
            continue
        if scan is None:
            continue
        fc = scan.forecast
        print(f"\n{scan.station} — {scan.date}  forecast {fc.mean_f:.1f}F "
              f"(sigma {fc.sigma_f:.1f}, {fc.n_models} models)  "
              f"[model total {scan.model_total*100:.0f}%]")
        for r in scan.rows:
            flag = f"  <== {r.bet.side.upper()} EV{r.bet.edge_cents:+.1f}c" if r.bet else ""
            print(f"  {r.subtitle:16} model {r.model_prob*100:5.1f}%  "
                  f"mkt {r.yes_bid:2}/{r.yes_ask:<2}c{flag}")
        all_bets.extend(scan.value_bets())

    print(f"\n{len(all_bets)} value bets found (min edge {args.min_edge}c after fees).")
    return 0


def cmd_weather_backtest(args: argparse.Namespace) -> int:
    from .weather.backtest import collect_samples, fade_backtest

    client = KalshiClient(load_settings())
    print(f"Collecting settled weather markets + {args.lead_days}d-lead forecasts...")
    samples = collect_samples(client, lead_days=args.lead_days, per_city=args.per_city)
    print(f"{len(samples)} samples.\n")
    print(f"Fade-longshot at {args.lead_days*24}h lead (passive entry, Kalshi fees):")
    print(f"  unfiltered           : {fade_backtest(samples)}")
    for thr in (0.10, 0.07, 0.05):
        print(f"  forecast-filter<={int(thr*100):2d}% : {fade_backtest(samples, filter_prob=thr)}")
    print("\ncaveat: outcomes cluster within city-days; treat t-stats as directional.")
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
    if args.command == "forward":
        return cmd_forward(args)
    if args.command == "weather":
        return cmd_weather(args)
    if args.command == "weather-backtest":
        return cmd_weather_backtest(args)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
