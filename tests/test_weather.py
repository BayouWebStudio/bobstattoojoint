import math

from kalshi_bot.weather.forecast import forecast_from_daily
from kalshi_bot.weather.model import parse_range, range_probability
from kalshi_bot.weather.scan import weather_date_from_ticker
from kalshi_bot.weather.value import evaluate


def test_weather_date_parsed_from_ticker_not_close_time():
    # The market closes June 27 but is about June 26's high.
    assert weather_date_from_ticker("KXHIGHCHI-26JUN26-T69") == "2026-06-26"
    assert weather_date_from_ticker("KXHIGHNY-26JUL01-B76.5") == "2026-07-01"
    # Event ticker form: date at end, no trailing dash (this was the bug).
    assert weather_date_from_ticker("KXHIGHCHI-26JUN26") == "2026-06-26"
    assert weather_date_from_ticker("NOPE") is None


def test_parse_range_formats():
    assert parse_range("75° or below") == (None, 75)
    assert parse_range("76° to 77°") == (76, 77)
    assert parse_range("84° or above") == (84, None)
    assert parse_range("nonsense") is None


def test_range_probabilities_sum_to_one_over_partition():
    # Partition: <=75, 76-77, 78-79, 80-81, 82-83, >=84
    ranges = [(None, 75), (76, 77), (78, 79), (80, 81), (82, 83), (84, None)]
    total = sum(range_probability(r, mean=77.0, sigma=2.5) for r in ranges)
    assert abs(total - 1.0) < 1e-9


def test_range_probability_peaks_at_forecast():
    # Forecast 77 -> the 76-77 band should be the most probable.
    ranges = {(None, 75): 0, (76, 77): 0, (78, 79): 0, (84, None): 0}
    probs = {r: range_probability(r, 77.0, 2.5) for r in ranges}
    assert probs[(76, 77)] == max(probs.values())
    assert probs[(84, None)] < 0.02  # deep longshot is tiny


def test_forecast_mean_and_spread_from_models():
    daily = {
        "time": ["2026-06-27"],
        "temperature_2m_max_gfs_seamless": [76.1],
        "temperature_2m_max_ecmwf_ifs025": [73.9],
        "temperature_2m_max_icon_seamless": [77.8],
    }
    fc = forecast_from_daily("Central Park", "2026-06-27", daily, sigma_floor=0.0)
    assert abs(fc.mean_f - (76.1 + 73.9 + 77.8) / 3) < 1e-9
    assert fc.n_models == 3
    assert fc.sigma_f > 0


def test_sigma_floor_applies():
    daily = {"time": ["d"], "temperature_2m_max_gfs_seamless": [80.0]}  # one model, no spread
    fc = forecast_from_daily("S", "d", daily, sigma_floor=2.5)
    assert fc.sigma_f == 2.5


def test_value_buy_yes_when_model_above_ask():
    # Model says 40% but YES asks only 25c -> buy YES (EV ~ +14c before fee).
    bet = evaluate("M", "78 to 79", 0.40, yes_bid=23, yes_ask=25, min_edge_cents=3)
    assert bet is not None and bet.side == "yes" and bet.edge_cents > 0


def test_value_buy_no_when_model_below_bid():
    # Model says 2% but market still bids YES at 10c -> fade (buy NO).
    bet = evaluate("M", "84 or above", 0.02, yes_bid=10, yes_ask=12, min_edge_cents=3)
    assert bet is not None and bet.side == "no"


def test_value_no_bet_when_market_fair():
    # Model 30%, market 29/31 -> neither side clears the edge.
    assert evaluate("M", "x", 0.30, yes_bid=29, yes_ask=31, min_edge_cents=3) is None
