import json

from kalshi_bot.report import _group_stats, build_report, render


def _write(path, positions):
    path.write_text(json.dumps({"positions": positions}))


def test_group_stats_basic(tmp_path):
    led = tmp_path / "l.json"
    _write(led, [
        {"status": "settled", "realized_pnl_cents": 5, "entry_no_cost": 95},
        {"status": "settled", "realized_pnl_cents": -90, "entry_no_cost": 90},
        {"status": "open", "entry_no_cost": 80},
    ])
    s = _group_stats("X", str(led), "entry_no_cost")
    assert s.open_n == 1 and s.settled_n == 2
    assert s.pnl_cents == -85 and s.capital_cents == 185
    assert s.wins == 1


def test_small_sample_flagged_as_noise(tmp_path):
    led = tmp_path / "l.json"
    _write(led, [{"status": "settled", "realized_pnl_cents": 14, "entry_no_cost": 86}])
    s = _group_stats("X", str(led), "entry_no_cost")
    assert s.settled_n == 1
    assert s.roi_pct > 0
    assert "noise" in s.verdict   # one settled position is never a "win"


def test_missing_ledger_is_zeroed(tmp_path):
    s = _group_stats("X", str(tmp_path / "nope.json"), "entry_no_cost")
    assert s.settled_n == 0 and s.open_n == 0
    assert s.verdict == "no data yet"


def test_polymarket_cost_field(tmp_path):
    led = tmp_path / "p.json"
    _write(led, [{"status": "settled", "realized_pnl_cents": 14.5, "entry_cost_cents": 85.5}])
    s = _group_stats("Poly", str(led), "entry_cost_cents")
    assert s.settled_n == 1 and s.capital_cents == 85.5


def test_render_runs(tmp_path):
    led = tmp_path / "l.json"
    _write(led, [{"status": "settled", "realized_pnl_cents": 5, "entry_no_cost": 95}])
    out = render(build_report([("L", str(led), "entry_no_cost")]))
    assert "validation scorecard" in out and "TOTAL" in out
