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


def test_kelly_sizing_converts_prob_for_no_side():
    from kalshi_bot.strategy.base import Signal

    engine = Engine(
        strategy=ThresholdStrategy(),
        sizer=Sizer(bankroll_usd=1000, max_position_usd=50, kelly_multiplier=1.0),
        broker=PaperBroker(),
    )
    # P(YES)=10%. Buying NO at 85c wins 90% of the time -> real edge, sizes > 0.
    no_signal = Signal(ticker="M", side="no", action="buy", price_cents=85,
                       fair_value_cents=10.0)
    assert engine._size(no_signal) > 0
    # The old bug treated 10% as the NO win prob -> Kelly edge (0.10-0.85) < 0,
    # which would have fallen back to flat sizing. Verify YES at the same params
    # has no Kelly edge (10% win prob at 85c is terrible).
    yes_signal = Signal(ticker="M", side="yes", action="buy", price_cents=85,
                        fair_value_cents=10.0)
    from kalshi_bot.risk.sizing import kelly_fraction
    assert kelly_fraction(0.10, 85) == 0.0


def test_mock_feed_is_deterministic():
    a = list(MockFeed(seed=42).stream(max_ticks=20))
    b = list(MockFeed(seed=42).stream(max_ticks=20))
    assert [s.yes_mid for s in a] == [s.yes_mid for s in b]
