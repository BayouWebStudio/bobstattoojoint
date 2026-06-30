from kalshi_bot.weather.accuracy import accuracy_report
from kalshi_bot.weather.indices import cdd, cumulative, daily_average, fahrenheit, hdd


def test_daily_average():
    assert daily_average(80, 60) == 70.0


def test_hdd_cdd_basics():
    # avg 70F, base 65 -> 5 CDD, 0 HDD
    assert cdd(80, 60) == 5.0
    assert hdd(80, 60) == 0.0
    # cold day avg 40 -> 25 HDD, 0 CDD
    assert hdd(50, 30) == 25.0
    assert cdd(50, 30) == 0.0


def test_custom_base_and_celsius():
    # Celsius base 18: avg 20C -> 2 CDD
    assert cdd(25, 15, base=18) == 2.0
    assert fahrenheit(0) == 32.0
    assert fahrenheit(100) == 212.0


def test_cumulative_strip():
    daily = [(80, 60), (90, 70), (50, 30)]  # avgs 70, 80, 40
    tot = cumulative(daily)
    assert tot.days == 3
    assert tot.cdd == 5 + 15 + 0      # 20
    assert tot.hdd == 0 + 0 + 25      # 25
    assert tot.cat == 70 + 80 + 40    # 190


def test_accuracy_report_perfect_forecast():
    actual = {"2026-06-01": (80, 60), "2026-06-02": (90, 70)}
    rep = accuracy_report("Test", actual, actual)
    assert rep.days == 2
    assert rep.daily_avg_mae == 0.0
    assert rep.cdd_error_pct == 0.0


def test_accuracy_report_with_error():
    forecast = {"2026-06-01": (82, 62)}   # avg 72 -> CDD 7
    actual = {"2026-06-01": (80, 60)}     # avg 70 -> CDD 5
    rep = accuracy_report("Test", forecast, actual)
    assert rep.daily_avg_mae == 2.0
    assert rep.forecast_cdd == 7.0 and rep.actual_cdd == 5.0
    assert abs(rep.cdd_error_pct - 40.0) < 1e-9   # (7-5)/5
