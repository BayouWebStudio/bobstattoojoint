import kalshi_bot.weather.precip as precip_mod
from kalshi_bot.forward import ForwardLedger, scan_precip
from kalshi_bot.weather.precip import rain_probability_from_daily


def test_rain_probability_averages_models():
    daily = {
        "time": ["2026-06-30"],
        "precipitation_probability_max_gfs_seamless": [3],
        "precipitation_probability_max_ecmwf_ifs025": [9],
        "precipitation_probability_max_icon_seamless": [0],
    }
    p = rain_probability_from_daily(daily)
    assert abs(p - (3 + 9 + 0) / 3 / 100) < 1e-9   # ~0.04


def test_rain_probability_none_when_missing():
    assert rain_probability_from_daily({"time": ["d"]}) is None


class FakeRainClient:
    def __init__(self, markets):
        self._markets = markets

    def get_markets(self, *, series_ticker, status, limit):
        return self._markets if series_ticker == "KXRAINNYC" else []


def test_scan_precip_fades_dry_forecast(tmp_path, monkeypatch):
    # Forecast ~4% rain; market bids YES (rain) at 11c -> fade (buy NO@89) is +EV.
    monkeypatch.setattr(precip_mod, "fetch_rain_probability", lambda *a, **k: 0.04)
    markets = [{
        "ticker": "KXRAINNYC-26JUN30-T0", "event_ticker": "KXRAINNYC-26JUN30",
        "yes_sub_title": "Rain in NYC", "yes_bid_dollars": "0.11", "yes_ask_dollars": "0.50",
        "volume_fp": "50", "close_time": "2026-07-01T04:00:00Z",
    }]
    led = ForwardLedger(str(tmp_path / "rain.json"))
    opened = scan_precip(led, FakeRainClient(markets), today="2026-06-29",
                         min_lead_days=1, max_lead_days=3, min_edge_cents=3.0)
    assert len(opened) == 1
    p = opened[0]
    assert p.category == "Rain"
    assert p.entry_no_cost == 89          # 100 - yes_bid(11)
    assert p.model_prob == 0.04
    assert p.edge_cents >= 3.0


def test_scan_precip_skips_when_rain_likely(tmp_path, monkeypatch):
    # Forecast 70% rain -> fading at 89c is sharply -EV, skip.
    monkeypatch.setattr(precip_mod, "fetch_rain_probability", lambda *a, **k: 0.70)
    markets = [{
        "ticker": "KXRAINNYC-26JUN30-T0", "event_ticker": "KXRAINNYC-26JUN30",
        "yes_sub_title": "Rain in NYC", "yes_bid_dollars": "0.11", "yes_ask_dollars": "0.50",
        "volume_fp": "50", "close_time": "2026-07-01T04:00:00Z",
    }]
    led = ForwardLedger(str(tmp_path / "rain.json"))
    opened = scan_precip(led, FakeRainClient(markets), today="2026-06-29", min_edge_cents=3.0)
    assert opened == []
