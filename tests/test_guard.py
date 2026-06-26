from kalshi_bot.risk.guard import RiskGuard


def test_position_cap_blocks_oversized_order():
    guard = RiskGuard(max_position_contracts=10)
    ok, _ = guard.check("MKT", contracts=8, price_cents=50)
    assert ok
    guard.record_fill("MKT", 8, "buy", "yes", 0.0)
    ok, reason = guard.check("MKT", contracts=5, price_cents=50)  # 8+5 > 10
    assert not ok
    assert "position cap" in reason
    assert not guard.tripped  # soft limit: does not latch


def test_daily_loss_trips_killswitch():
    guard = RiskGuard(max_daily_loss_usd=20)
    guard.record_fill("MKT", 10, "sell", "yes", -25.0)  # exceeds loss limit
    assert guard.tripped
    ok, reason = guard.check("MKT", 1, 50)
    assert not ok
    assert "kill-switch" in reason


def test_manual_stop_latches():
    guard = RiskGuard()
    assert not guard.tripped
    guard.stop()
    assert guard.tripped


def test_stop_file_trips_killswitch(tmp_path):
    stop = tmp_path / "STOP"
    guard = RiskGuard(stop_file=str(stop))
    assert not guard.tripped
    stop.write_text("halt")
    assert guard.tripped


def test_record_fill_tracks_net_position():
    guard = RiskGuard(max_position_contracts=100)
    guard.record_fill("MKT", 10, "buy", "yes", 0.0)   # +10
    guard.record_fill("MKT", 4, "sell", "yes", 0.0)   # -4 -> net 6
    ok, _ = guard.check("MKT", 94, 50)                 # 6+94 = 100, at the cap
    assert ok
    ok, _ = guard.check("MKT", 95, 50)                 # 6+95 = 101, over
    assert not ok
