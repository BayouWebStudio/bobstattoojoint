from kalshi_bot.backtest import backtest, read_snapshots, write_snapshots
from kalshi_bot.kalshi.feed import MockFeed
from kalshi_bot.strategy.threshold import ThresholdStrategy


def test_csv_roundtrip(tmp_path):
    snaps = list(MockFeed(seed=3).stream(max_ticks=30))
    path = tmp_path / "data.csv"
    n = write_snapshots(path, snaps)
    assert n == len(snaps)

    restored = list(read_snapshots(path))
    assert len(restored) == len(snaps)
    assert restored[0].ticker == snaps[0].ticker
    assert restored[5].yes_bid == snaps[5].yes_bid
    assert restored[5].yes_ask == snaps[5].yes_ask


def test_backtest_produces_report():
    snaps = MockFeed(seed=11).stream(max_ticks=200)
    report = backtest(ThresholdStrategy(threshold_cents=1.0), snaps, bankroll_usd=1000)

    assert report.trades > 0
    assert report.starting_equity_usd == 1000
    assert len(report.equity_curve) == 201  # one point + one per snapshot
    assert 0.0 <= report.win_rate <= 1.0
    assert report.max_drawdown_pct >= 0.0


def test_backtest_replay_is_deterministic():
    snaps = list(MockFeed(seed=5).stream(max_ticks=100))
    a = backtest(ThresholdStrategy(threshold_cents=2.0), iter(snaps))
    b = backtest(ThresholdStrategy(threshold_cents=2.0), iter(snaps))
    assert a.final_equity_usd == b.final_equity_usd
    assert a.trades == b.trades
