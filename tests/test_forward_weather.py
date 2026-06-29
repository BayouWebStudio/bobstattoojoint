import kalshi_bot.weather.forecast as fc_mod
from kalshi_bot.forward import ForwardLedger, scan_weather_filtered
from kalshi_bot.weather.forecast import TempForecast


class FakeWxClient:
    """Returns weather markets for KXHIGHNY only, empty for other series."""

    def __init__(self, markets):
        self._markets = markets

    def get_markets(self, *, series_ticker, status, limit):
        return self._markets if series_ticker == "KXHIGHNY" else []


def _mkt(ticker, sub, yb, ya, vol=5000):
    return {
        "ticker": ticker, "event_ticker": "KXHIGHNY-26JUN28", "yes_sub_title": sub,
        "yes_bid_dollars": f"{yb/100:.2f}", "yes_ask_dollars": f"{ya/100:.2f}",
        "volume_fp": str(vol), "close_time": "2026-06-29T04:59:00Z",
    }


def test_weather_scan_picks_highest_ev_value_fade(tmp_path, monkeypatch):
    # Forecast ~78F (sigma 3.5). The market bids an "88 or above" tail up to
    # 12c, but the forecast says that is only ~0.2% -> a strongly +EV NO fade.
    # A "90 or above" tail at 8c is also +EV but smaller. Pick the bigger edge.
    monkeypatch.setattr(
        fc_mod, "fetch_forecast",
        lambda *a, **k: TempForecast("Central Park", "2026-06-28", 78.0, 3.5, [78.0]),
    )
    markets = [
        _mkt("KXHIGHNY-26JUN28-T89", "90° or above", yb=6, ya=8),
        _mkt("KXHIGHNY-26JUN28-T87", "88° or above", yb=10, ya=12),  # bigger NO margin -> bigger EV
    ]
    led = ForwardLedger(str(tmp_path / "wx.json"))
    opened = scan_weather_filtered(
        led, FakeWxClient(markets), today="2026-06-26",
        min_lead_days=2, max_lead_days=3, min_edge_cents=2.0, entry="mid",
    )
    assert len(opened) == 1
    p = opened[0]
    assert p.ticker == "KXHIGHNY-26JUN28-T87"
    assert p.entry_no_cost == round(100 - (10 + 12) / 2)  # passive mid -> 89
    assert p.entry_mode == "mid"
    assert p.edge_cents is not None and p.edge_cents >= 2.0


def test_weather_scan_skips_negative_ev_longshot(tmp_path, monkeypatch):
    # Forecast ~94F makes ">=95" genuinely plausible (~44%) -> fading a deep
    # cheap NO is sharply -EV, so nothing should be opened.
    monkeypatch.setattr(
        fc_mod, "fetch_forecast",
        lambda *a, **k: TempForecast("Central Park", "2026-06-28", 94.0, 3.5, [94.0]),
    )
    markets = [_mkt("KXHIGHNY-26JUN28-T94", "95° or above", yb=3, ya=5)]
    led = ForwardLedger(str(tmp_path / "wx.json"))
    opened = scan_weather_filtered(
        led, FakeWxClient(markets), today="2026-06-26", min_edge_cents=2.0,
    )
    assert opened == []   # forecast says it's likely -> fading it is -EV
