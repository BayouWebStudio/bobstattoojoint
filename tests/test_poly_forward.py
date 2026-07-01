import kalshi_bot.polymarket.forward as pf
from kalshi_bot.polymarket.forward import PolyLedger, PolyPosition, scan_poly_weather, settle_poly


def _event(slug="highest-temperature-in-london-on-june-29-2026"):
    return {
        "title": "Highest temperature in London on June 29?",
        "slug": slug, "endDate": "2026-06-29T12:00:00Z", "closed": False,
        "markets": [
            {"id": "m24", "groupItemTitle": "24°C", "outcomePrices": "[\"0.05\",\"0.95\"]",
             "bestBid": "0.04", "bestAsk": "0.05",
             "clobTokenIds": "[\"t24\",\"t24n\"]", "volumeNum": "40000"},
            {"id": "m25", "groupItemTitle": "25°C", "outcomePrices": "[\"0.30\",\"0.70\"]",
             "bestBid": "0.29", "bestAsk": "0.31",
             "clobTokenIds": "[\"t25\",\"t25n\"]", "volumeNum": "40000"},
        ],
    }


def test_scan_opens_best_value_bet_at_executable_price(tmp_path, monkeypatch):
    # Forecast 25C, sigma 1. The 24C bin's YES edge (~+19c at the 5c ask)
    # exceeds the model-error cap (15c) and is skipped; the 25C bin (model ~38%
    # vs 31c ask, ~+7c) is the best remaining value bet. Entry cost must be the
    # executable ASK (31c), not the mid (30c). calibrate=False to avoid network.
    monkeypatch.setattr(pf, "fetch_temp_events", lambda s, closed: [_event()])
    monkeypatch.setattr(pf, "fetch_forecast_c", lambda *a, **k: (25.0, 1.0))
    led = PolyLedger(str(tmp_path / "p.json"))
    opened = scan_poly_weather(led, today="2026-06-27", min_lead_days=1, max_lead_days=3,
                               min_edge_cents=4.0, max_edge_cents=15.0, min_volume=0,
                               calibrate=False)
    assert len(opened) == 1
    p = opened[0]
    assert p.city == "London" and p.bin_title == "25°C" and p.side == "yes"
    assert p.entry_cost_cents == 31.0     # ask, not mid
    assert 4.0 <= p.edge_cents <= 15.0


def test_scan_skips_bins_without_executable_quote(tmp_path, monkeypatch):
    # A bin with no order book at all (bestBid/bestAsk missing) must be skipped
    # even if its stale last-trade price makes it look like huge value.
    event = _event()
    event["markets"] = [{
        "id": "m24", "groupItemTitle": "24°C", "outcomePrices": "[\"0.001\",\"0.999\"]",
        "clobTokenIds": "[\"t24\",\"t24n\"]", "volumeNum": "40000",
    }]
    monkeypatch.setattr(pf, "fetch_temp_events", lambda s, closed: [event])
    monkeypatch.setattr(pf, "fetch_forecast_c", lambda *a, **k: (24.0, 1.0))
    led = PolyLedger(str(tmp_path / "p.json"))
    opened = scan_poly_weather(led, today="2026-06-27", min_lead_days=1, max_lead_days=3,
                               min_edge_cents=4.0, min_volume=0, calibrate=False)
    assert opened == []


def test_scan_skips_out_of_lead_window(tmp_path, monkeypatch):
    monkeypatch.setattr(pf, "fetch_temp_events", lambda s, closed: [_event()])
    monkeypatch.setattr(pf, "fetch_forecast_c", lambda *a, **k: (25.0, 1.0))
    led = PolyLedger(str(tmp_path / "p.json"))
    # today far before the event -> out of window
    opened = scan_poly_weather(led, today="2026-06-01", min_lead_days=1, max_lead_days=3,
                               calibrate=False, min_volume=0)
    assert opened == []


def test_settle_resolves_via_gamma(tmp_path, monkeypatch):
    led = PolyLedger(str(tmp_path / "p.json"))
    led.add(PolyPosition(
        market_id="m25", event_slug="slug-x", city="London", weather_date="2026-06-29",
        bin_title="25°C", side="yes", entry_cost_cents=30.0, market_yes=0.30,
        model_prob=0.7, edge_cents=40.0, contracts=10, opened_date="2026-06-27",
        close_time="2026-06-29T12:00:00",
    ))
    led.save()

    resolved_event = [{
        "slug": "slug-x", "closed": True,
        "markets": [{"groupItemTitle": "25°C", "outcomePrices": "[\"1\",\"0\"]"}],  # 25C won
    }]

    class FakeResp:
        status_code = 200
        def json(self): return resolved_event

    class FakeSession:
        def get(self, *a, **k): return FakeResp()

    n = settle_poly(led, session=FakeSession(), results_log=str(tmp_path / "log.csv"))
    assert n == 1
    p = led.settled_positions()[0]
    assert p.result_yes is True
    assert p.realized_pnl_cents == 100 - 30   # YES won, paid 30c


def test_settle_loss_when_bin_loses(tmp_path, monkeypatch):
    led = PolyLedger(str(tmp_path / "p.json"))
    led.add(PolyPosition(
        market_id="m25", event_slug="slug-y", city="Paris", weather_date="2026-06-29",
        bin_title="25°C", side="yes", entry_cost_cents=30.0, market_yes=0.30,
        model_prob=0.7, edge_cents=40.0, contracts=10, opened_date="2026-06-27",
        close_time="2026-06-29T12:00:00",
    ))

    class FakeResp:
        status_code = 200
        def json(self):
            return [{"slug": "slug-y", "closed": True,
                     "markets": [{"groupItemTitle": "25°C", "outcomePrices": "[\"0\",\"1\"]"}]}]

    class FakeSession:
        def get(self, *a, **k): return FakeResp()

    settle_poly(led, session=FakeSession(), results_log=str(tmp_path / "log.csv"))
    p = led.settled_positions()[0]
    assert p.result_yes is False
    assert p.realized_pnl_cents == 0 - 30     # YES lost
