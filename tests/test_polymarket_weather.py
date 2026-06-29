from kalshi_bot.polymarket.weather import (
    CITY_COORDS,
    city_from_title,
    markets_from_event,
    parse_celsius_bin,
)


def test_parse_celsius_bins():
    assert parse_celsius_bin("21°C or below") == (None, 21)
    assert parse_celsius_bin("22°C") == (22, 22)
    assert parse_celsius_bin("31°C or higher") == (31, None)
    assert parse_celsius_bin("nonsense") is None


def test_city_from_title():
    assert city_from_title("Highest temperature in Hong Kong on June 29?") == "Hong Kong"
    assert city_from_title("Highest temperature in NYC on June 30?") == "NYC"
    assert city_from_title("Who wins?") is None


def test_known_cities_have_coords():
    for city in ["NYC", "London", "Tokyo", "Seoul", "Sao Paulo", "Cape Town"]:
        assert city in CITY_COORDS


def test_markets_from_event_parses_bins_and_prices():
    event = {
        "title": "Highest temperature in London on June 29?",
        "endDate": "2026-06-29T12:00:00Z",
        "markets": [
            {"groupItemTitle": "24°C", "outcomePrices": "[\"0.04\", \"0.96\"]",
             "clobTokenIds": "[\"tok24\", \"tok24n\"]", "volumeNum": "47646"},
            {"groupItemTitle": "25°C", "outcomePrices": "[\"0.90\", \"0.10\"]",
             "clobTokenIds": "[\"tok25\", \"tok25n\"]", "volumeNum": "17864"},
            {"groupItemTitle": "junk", "outcomePrices": "[\"0.5\",\"0.5\"]"},  # skipped
        ],
    }
    mk = markets_from_event(event)
    assert len(mk) == 2
    assert mk[0].city == "London" and mk[0].date == "2026-06-29"
    assert mk[0].bin_title == "24°C" and mk[0].rng == (24, 24)
    assert abs(mk[0].yes_price - 0.04) < 1e-9
    assert mk[0].token_id == "tok24"


def test_markets_from_event_skips_unknown_city():
    event = {"title": "Highest temperature in Atlantis on June 29?",
             "endDate": "2026-06-29", "markets": [{"groupItemTitle": "22°C",
             "outcomePrices": "[\"0.5\",\"0.5\"]"}]}
    assert markets_from_event(event) == []
