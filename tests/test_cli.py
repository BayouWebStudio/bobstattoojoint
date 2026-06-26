from kalshi_bot.backtest import read_snapshots
from kalshi_bot.cli import main


def test_record_subcommand_writes_csv(tmp_path):
    out = tmp_path / "rec.csv"
    rc = main(["record", "--mock", "--ticks", "50", "--out", str(out)])
    assert rc == 0
    assert out.exists()
    assert len(list(read_snapshots(out))) == 50


def test_backtest_subcommand_on_recorded_csv(tmp_path):
    out = tmp_path / "rec.csv"
    assert main(["record", "--mock", "--ticks", "100", "--out", str(out)]) == 0
    assert main(["backtest", "--csv", str(out), "--threshold", "1"]) == 0


def test_arb_subcommand_mock():
    assert main(["arb", "--mock", "--ticks", "20"]) == 0


def test_xarb_subcommand_mock():
    assert main(["xarb", "--mock"]) == 0


def test_live_requires_confirmation():
    # Missing --i-understand-live-risk -> refuse with exit code 2.
    assert main(["run", "--live", "--tickers", "FOO"]) == 2
