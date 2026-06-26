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


def test_weather_scan_fades_only_forecast_confirmed_longshot(tmp_path, monkeypatch):
    # Forecast says ~80F: a ">=95" range is ~0% (confirmed dead -> fade),
    # a "78 to 79" range is plausible and also not a longshot anyway.
    monkeypatch.setattr(
        fc_mod, "fetch_forecast",
        lambda *a, **k: TempForecast("Central Park", "2026-06-28", 80.0, 3.5, [80.0]),
    )
    markets = [
        _mkt("KXHIGHNY-26JUN28-T94", "95° or above", yb=3, ya=5),   # deep longshot, dead
        _mkt("KXHIGHNY-26JUN28-B78.5", "78° to 79°", yb=30, ya=33),  # not a longshot
    ]
    led = ForwardLedger(str(tmp_path / "wx.json"))
    opened = scan_weather_filtered(
        led, FakeWxClient(markets), today="2026-06-26",
        min_lead_days=2, max_lead_days=3, filter_prob=0.07,
    )
    assert len(opened) == 1
    p = opened[0]
    assert p.ticker == "KXHIGHNY-26JUN28-T94"
    assert p.entry_no_cost == 97          # 100 - yes_bid(3)
    assert p.model_prob is not None and p.model_prob < 0.07
    assert p.weather_date == "2026-06-28"


def test_weather_scan_skips_live_longshot(tmp_path, monkeypatch):
    # Forecast ~94F makes ">=95" genuinely plausible -> filter must NOT fade it.
    monkeypatch.setattr(
        fc_mod, "fetch_forecast",
        lambda *a, **k: TempForecast("Central Park", "2026-06-28", 94.0, 3.5, [94.0]),
    )
    markets = [_mkt("KXHIGHNY-26JUN28-T94", "95° or above", yb=3, ya=5)]
    led = ForwardLedger(str(tmp_path / "wx.json"))
    opened = scan_weather_filtered(
        led, FakeWxClient(markets), today="2026-06-26", filter_prob=0.07,
    )
    assert opened == []   # forecast says it could happen -> not faded
