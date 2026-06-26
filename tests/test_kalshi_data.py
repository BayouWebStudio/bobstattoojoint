from kalshi_bot.kalshi.client import normalize_orderbook
from kalshi_bot.kalshi.history import candlesticks_to_snapshots


def test_normalize_current_orderbook_fp_schema():
    # Real schema: dollar-string levels under orderbook_fp.
    payload = {
        "orderbook_fp": {
            "yes_dollars": [["0.6200", "5.00"], ["0.7500", "75.00"]],
            "no_dollars": [["0.0700", "70.21"], ["0.1900", "33.53"]],
        }
    }
    ob = normalize_orderbook(payload)
    assert ob["yes"][-1] == [75, 75.0]   # best YES bid 75c
    assert ob["no"][-1] == [19, 33.53]   # best NO bid 19c


def test_normalize_legacy_orderbook_schema():
    payload = {"orderbook": {"yes": [[40, 100]], "no": [[55, 80]]}}
    ob = normalize_orderbook(payload)
    assert ob["yes"] == [[40, 100]]
    assert ob["no"] == [[55, 80]]


def test_normalize_empty_book():
    assert normalize_orderbook({"orderbook_fp": {"yes_dollars": [], "no_dollars": []}}) == {
        "yes": [], "no": []
    }


def test_candlesticks_to_snapshots():
    candles = [
        {"end_period_ts": 1779422400,
         "yes_bid": {"close_dollars": "0.3700"}, "yes_ask": {"close_dollars": "0.3900"}},
        {"end_period_ts": 1779508800,  # no quote -> skipped
         "yes_bid": {"close_dollars": "0.0000"}, "yes_ask": {"close_dollars": "0.0000"}},
        {"end_period_ts": 1779595200,
         "yes_bid": {"close_dollars": "0.4100"}, "yes_ask": {"close_dollars": "0.4300"}},
    ]
    snaps = candlesticks_to_snapshots(candles, "MKT")
    assert len(snaps) == 2
    assert (snaps[0].yes_bid, snaps[0].yes_ask) == (37, 39)
    assert (snaps[1].yes_bid, snaps[1].yes_ask) == (41, 43)
