from kalshi_bot.kalshi.ws import OrderBookState


def _snapshot_msg(ticker, yes, no):
    return {"type": "orderbook_snapshot", "msg": {"market_ticker": ticker, "yes": yes, "no": no}}


def _delta_msg(ticker, side, price, delta):
    return {
        "type": "orderbook_delta",
        "msg": {"market_ticker": ticker, "side": side, "price": price, "delta": delta},
    }


def test_snapshot_builds_top_of_book():
    state = OrderBookState()
    snap = state.apply_message(
        _snapshot_msg("MKT", yes=[[40, 100], [41, 50]], no=[[55, 80], [54, 30]])
    )
    assert snap is not None
    assert snap.ticker == "MKT"
    assert snap.yes_bid == 41          # best (highest) YES bid
    assert snap.yes_ask == 100 - 55    # best NO bid is 55 -> YES ask 45


def test_delta_adds_and_improves_level():
    state = OrderBookState()
    state.apply_message(_snapshot_msg("MKT", yes=[[40, 100]], no=[[55, 80]]))
    snap = state.apply_message(_delta_msg("MKT", "yes", 42, 10))  # new best YES bid
    assert snap.yes_bid == 42


def test_delta_removes_level_when_size_hits_zero():
    state = OrderBookState()
    state.apply_message(_snapshot_msg("MKT", yes=[[40, 100], [42, 5]], no=[[55, 80]]))
    snap = state.apply_message(_delta_msg("MKT", "yes", 42, -5))  # remove the 42 level
    assert snap.yes_bid == 40


def test_no_snapshot_until_both_sides_present():
    state = OrderBookState()
    out = state.apply_message(_snapshot_msg("MKT", yes=[[40, 100]], no=[]))
    assert out is None


def test_unknown_message_ignored():
    state = OrderBookState()
    assert state.apply_message({"type": "heartbeat", "msg": {}}) is None
