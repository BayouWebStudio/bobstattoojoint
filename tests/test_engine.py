from kalshi_bot.engine import Engine
from kalshi_bot.kalshi.feed import MockFeed
from kalshi_bot.paper.broker import PaperBroker
from kalshi_bot.risk.sizing import Sizer
from kalshi_bot.strategy.threshold import ThresholdStrategy


def test_engine_runs_end_to_end_on_mock_feed():
    strategy = ThresholdStrategy(threshold_cents=1.0)
    broker = PaperBroker(starting_cash_usd=1000)
    sizer = Sizer(bankroll_usd=1000, max_position_usd=50)
    engine = Engine(strategy=strategy, sizer=sizer, broker=broker)

    result = engine.run(MockFeed(seed=7).stream(max_ticks=200))

    # The run should complete and produce some simulated activity.
    assert result is broker
    assert len(broker.fills) > 0


def test_mock_feed_is_deterministic():
    a = list(MockFeed(seed=42).stream(max_ticks=20))
    b = list(MockFeed(seed=42).stream(max_ticks=20))
    assert [s.yes_mid for s in a] == [s.yes_mid for s in b]
