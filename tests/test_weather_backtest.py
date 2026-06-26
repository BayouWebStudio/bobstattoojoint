from kalshi_bot.weather.backtest import ForecastSample, fade_backtest
from kalshi_bot.weather.forecast import daily_high_from_hourly


def test_daily_high_from_hourly():
    hourly = {
        "time": ["2026-06-01T00:00", "2026-06-01T15:00", "2026-06-02T14:00"],
        "temperature_2m_previous_day3": [70.0, 88.0, 91.0],
    }
    highs = daily_high_from_hourly(hourly, "temperature_2m_previous_day3")
    assert highs == {"2026-06-01": 88.0, "2026-06-02": 91.0}


def _samples():
    # 20 genuine dead longshots (model 2%, all resolve NO) priced 5/7 YES,
    # plus 10 "live" longshots also priced cheap (5/7) but model says 30% and
    # they DO resolve YES -> these are the losses the filter should remove.
    dead = [ForecastSample(0.02, 5, 7, False) for _ in range(20)]
    live = [ForecastSample(0.30, 5, 7, True) for _ in range(10)]
    return dead + live


def test_filter_excludes_live_longshots_and_improves_edge():
    samples = _samples()
    unfiltered = fade_backtest(samples, max_yes_ask=15)
    filtered = fade_backtest(samples, max_yes_ask=15, filter_prob=0.10)

    # Unfiltered fades all 30 and eats the 10 big losses.
    assert unfiltered.n == 30
    assert unfiltered.roi_pct < 0

    # Filter keeps only the 20 dead ones; excludes the 10 live ones (all hits).
    assert filtered.n == 20
    assert filtered.excluded == 10
    assert filtered.excluded_hits == 10
    assert filtered.roi_pct > 0
    assert filtered.win_rate == 1.0


def test_no_trades_when_nothing_qualifies():
    samples = [ForecastSample(0.5, 48, 52, False) for _ in range(5)]  # not longshots
    assert fade_backtest(samples, max_yes_ask=15).n == 0
