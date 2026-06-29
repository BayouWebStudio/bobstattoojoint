from kalshi_bot.forward import (
    ForwardLedger,
    Position,
    market_quote_cents,
    scan_and_open,
    settle_open,
    settle_position,
)


def _pos(ticker="M", no_cost=96, **kw):
    return Position(
        ticker=ticker, event_ticker="E", category="Test",
        entry_yes_bid=100 - no_cost, entry_yes_ask=100 - no_cost + 2,
        entry_no_cost=no_cost, contracts=10, opened_date="2026-06-26",
        close_time="2026-07-01T00:00:00Z", **kw,
    )


def test_market_quote_cents():
    assert market_quote_cents({"yes_bid_dollars": "0.0400", "yes_ask_dollars": "0.0600"}) == (4, 6)
    assert market_quote_cents({"yes_bid_dollars": None, "yes_ask_dollars": None}) == (0, 0)


def test_settle_no_outcome_is_a_win():
    p = _pos(no_cost=96)  # paid 96 for NO
    changed = settle_position(p, {"status": "settled", "result": "no"})
    assert changed and p.status == "settled" and p.result == "no"
    # payoff 100 - cost 96 - fee(96)=ceil(0.07*0.96*0.04*100)=ceil(0.27)=1 -> +3
    assert p.realized_pnl_cents == 100 - 96 - 1


def test_settle_yes_outcome_is_a_loss():
    p = _pos(no_cost=96)
    settle_position(p, {"status": "settled", "result": "yes"})
    assert p.realized_pnl_cents == 0 - 96 - 1   # NO pays nothing; lose the stake + fee


def test_unsettled_market_is_left_open():
    p = _pos()
    assert settle_position(p, {"status": "active", "result": None}) is False
    assert p.status == "open"


class FakeClient:
    def __init__(self, markets):
        self._markets = markets

    def get_market(self, ticker):
        return self._markets.get(ticker, {"status": "active", "result": None})


def test_settle_open_persists(tmp_path):
    led = ForwardLedger(str(tmp_path / "ledger.json"))
    led.add(_pos("WIN", no_cost=95))
    led.add(_pos("OPEN", no_cost=90))
    led.save()

    client = FakeClient({"WIN": {"status": "settled", "result": "no"}})
    log = tmp_path / "results.csv"
    n = settle_open(led, client, results_log=str(log))
    assert n == 1

    reloaded = ForwardLedger(str(tmp_path / "ledger.json"))
    assert len(reloaded.settled_positions()) == 1
    assert len(reloaded.open_positions()) == 1
    assert "settled=1" in reloaded.summary()

    # The newly-settled position is appended to the permanent results log.
    rows = log.read_text().strip().splitlines()
    assert rows[0].startswith("ticker,")        # header
    assert any(r.startswith("WIN,") for r in rows[1:])


class FakeScanClient:
    def __init__(self, events):
        self._events = events

    def _request(self, method, path, *, signed, params=None):
        return {"events": self._events, "cursor": None}


class FakeSeriesClient:
    def __init__(self, markets_by_series):
        self._by_series = markets_by_series

    def _request(self, method, path, *, signed, params=None):
        ser = params.get("series_ticker")
        return {"markets": self._by_series.get(ser, []), "cursor": None}


def test_scan_series_with_within_hours_filter(tmp_path):
    markets = {
        "KXBTC": [
            {"ticker": "BTC-SOON", "event_ticker": "KXBTC-D1", "yes_bid_dollars": "0.03",
             "yes_ask_dollars": "0.05", "volume_fp": "9000",
             "close_time": "2026-06-26T21:00:00Z"},          # ~9h out -> kept
            {"ticker": "BTC-LATER", "event_ticker": "KXBTC-D2", "yes_bid_dollars": "0.03",
             "yes_ask_dollars": "0.05", "volume_fp": "9000",
             "close_time": "2026-07-10T21:00:00Z"},          # weeks out -> filtered
        ]
    }
    led = ForwardLedger(str(tmp_path / "d.json"))
    opened = scan_and_open(
        led, FakeSeriesClient(markets), today="2026-06-26", series=["KXBTC"],
        within_hours=30, now_iso="2026-06-26T12:00:00Z", min_volume=200,
    )
    assert [p.ticker for p in opened] == ["BTC-SOON"]


def test_scan_opens_one_per_event_nearest_first(tmp_path):
    events = [{
        "event_ticker": "EV1", "category": "Politics",
        "markets": [
            {"ticker": "A", "yes_bid_dollars": "0.04", "yes_ask_dollars": "0.06",
             "volume_fp": "5000", "close_time": "2026-07-01T00:00:00Z"},
            {"ticker": "B", "yes_bid_dollars": "0.03", "yes_ask_dollars": "0.05",
             "volume_fp": "5000", "close_time": "2026-07-01T00:00:00Z"},
        ],
    }, {
        "event_ticker": "EV2", "category": "Sports",
        "markets": [
            {"ticker": "C", "yes_bid_dollars": "0.05", "yes_ask_dollars": "0.07",
             "volume_fp": "9000", "close_time": "2026-08-01T00:00:00Z"},
            {"ticker": "D", "yes_bid_dollars": "0.50", "yes_ask_dollars": "0.52",  # not a longshot
             "volume_fp": "9000", "close_time": "2026-08-01T00:00:00Z"},
        ],
    }]
    led = ForwardLedger(str(tmp_path / "l.json"))
    opened = scan_and_open(led, FakeScanClient(events), today="2026-06-26", max_new=10)
    # One per event (EV1, EV2), the non-longshot D excluded.
    assert [p.ticker for p in opened] == ["A", "C"]
    assert opened[0].entry_no_cost == 96  # 100 - yes_bid(4)
