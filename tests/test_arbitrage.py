from kalshi_bot.arb import run_arbitrage
from kalshi_bot.kalshi.feed import MockEventFeed
from kalshi_bot.paper.broker import PaperBroker
from kalshi_bot.strategy.arbitrage import ArbitrageDetector, Leg, find_arbitrage


def test_underpriced_arb_detected():
    legs = [Leg("A", 40, 41), Leg("B", 33, 34), Leg("C", 18, 19)]  # asks sum 94
    opp = find_arbitrage(legs, threshold_cents=1)
    assert opp is not None
    assert opp.kind == "underpriced"
    assert opp.combined_price_cents == 94
    assert opp.profit_per_set_cents == 6
    assert all(s.action == "buy" for s in opp.legs)


def test_overpriced_arb_detected():
    legs = [Leg("A", 45, 46), Leg("B", 40, 41), Leg("C", 20, 21)]  # bids sum 105
    opp = find_arbitrage(legs, threshold_cents=1)
    assert opp is not None
    assert opp.kind == "overpriced"
    assert opp.profit_per_set_cents == 5
    assert all(s.action == "sell" for s in opp.legs)


def test_no_arb_when_tight():
    legs = [Leg("A", 44, 46), Leg("B", 34, 36), Leg("C", 19, 21)]  # asks 103, bids 97
    assert find_arbitrage(legs, threshold_cents=1) is None


def test_detector_only_scans_complete_groups():
    det = ArbitrageDetector({"E": ["A", "B", "C"]}, threshold_cents=1)
    det.update("A", 40, 41)
    det.update("B", 33, 34)
    assert det.scan() == []  # C missing
    det.update("C", 18, 19)
    assert len(det.scan()) == 1


def test_runner_executes_legs_on_mock_event_feed():
    det = ArbitrageDetector({MockEventFeed.EVENT: list(MockEventFeed.TICKERS)}, threshold_cents=1)
    broker = PaperBroker()
    taken = run_arbitrage(MockEventFeed().stream(max_ticks=20), det, broker)
    assert len(taken) > 0
    # Each opportunity executes one fill per leg (3 legs).
    assert len(broker.fills) == 3 * len(taken)
