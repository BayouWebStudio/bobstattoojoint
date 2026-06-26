from kalshi_bot.research.calibration import (
    SettlementSample,
    calibration_table,
    fade_longshot_backtest,
    kalshi_fee_cents,
)


def test_fee_model_shape():
    # Fee is largest near 50c and small for deep longshots; rounds up.
    assert kalshi_fee_cents(50) == 2          # ceil(0.07*0.25*100)=ceil(1.75)=2
    assert kalshi_fee_cents(5) == 1           # ceil(0.07*0.0475*100)=ceil(0.33)=1
    assert kalshi_fee_cents(50) >= kalshi_fee_cents(10)


def _longshot_samples():
    # 100 longshots priced ~5c YES (ask 6), 2 actually resolve YES (overpriced:
    # implied ~6%, realized 2%). Fading (buy NO) should be profitable.
    samples = []
    for i in range(100):
        res = i < 2  # 2% resolve YES
        samples.append(SettlementSample(f"M{i}", yes_bid=4, yes_ask=6, result_yes=res))
    return samples


def test_calibration_detects_overpriced_longshots():
    table = calibration_table(_longshot_samples(), width=10)
    band = table[0]
    assert band.low == 0
    assert band.realized_pct == 2.0       # 2 of 100
    assert band.gap < 0                    # realized below implied => overpriced


def test_fade_longshot_backtest_positive_edge():
    stats = fade_longshot_backtest(_longshot_samples(), max_yes_ask=10, entry="mid")
    assert stats.n == 100
    assert stats.ev_per_market_cents > 0   # positive expected value
    assert stats.roi_pct > 0
    # Positive but modest t-stat: the rare large loss (a longshot that hits)
    # skews the P&L distribution, which is the real-world caveat for this edge.
    assert stats.t_stat > 1


def test_fade_longshot_no_trades_outside_band():
    # All contracts priced at 50c -> none qualify as longshots.
    samples = [SettlementSample(f"M{i}", 49, 51, result_yes=(i % 2 == 0)) for i in range(20)]
    stats = fade_longshot_backtest(samples, max_yes_ask=15)
    assert stats.n == 0


def test_aggressive_entry_costs_more_than_passive():
    samples = _longshot_samples()
    passive = fade_longshot_backtest(samples, max_yes_ask=10, entry="mid")
    aggressive = fade_longshot_backtest(samples, max_yes_ask=10, entry="ask")
    # Crossing the spread lowers ROI versus passive entry.
    assert aggressive.roi_pct < passive.roi_pct
