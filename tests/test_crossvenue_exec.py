from kalshi_bot.execution.base import Fill
from kalshi_bot.execution.crossvenue import CrossVenueExecutor
from kalshi_bot.paper.broker import PaperBroker
from kalshi_bot.risk.guard import RiskGuard
from kalshi_bot.strategy.crossvenue import CrossVenueArb


def make_arb():
    # Buy YES@kalshi 48c + NO@polymarket 47c = 95c -> 5c/set guaranteed.
    return CrossVenueArb(
        event="E",
        yes_venue="kalshi", yes_market_id="K", yes_price_cents=48,
        no_venue="polymarket", no_market_id="P", no_price_cents=47,
        cost_cents=95, profit_cents=5,
    )


class FailingBroker:
    """Broker whose execute always raises (simulates a rejected leg)."""

    def execute(self, *a, **k):
        raise RuntimeError("venue rejected order")


def test_both_legs_fill_and_lock_profit():
    brokers = {"kalshi": PaperBroker(), "polymarket": PaperBroker()}
    ex = CrossVenueExecutor(brokers=brokers)
    result = ex.execute(make_arb(), contracts=10)

    assert result.status == "filled"
    assert len(result.fills) == 2
    assert abs(result.locked_profit_usd - 0.05 * 10) < 1e-9   # 5c * 10
    assert abs(ex.locked_profit_usd - 0.5) < 1e-9
    # YES leg on kalshi, NO leg on polymarket.
    assert brokers["kalshi"].positions["K"].contracts == 10    # long YES
    assert brokers["polymarket"].positions["P"].contracts == -10  # NO == short YES


def test_second_leg_failure_unwinds_first_leg():
    brokers = {"kalshi": PaperBroker(), "polymarket": FailingBroker()}
    ex = CrossVenueExecutor(brokers=brokers)
    result = ex.execute(make_arb(), contracts=5)

    assert result.status == "unwound"
    assert ex.locked_profit_usd == 0.0
    # The YES leg was bought then sold back -> net flat, no naked exposure.
    assert brokers["kalshi"].positions["K"].contracts == 0


def test_first_leg_failure_takes_no_exposure():
    brokers = {"kalshi": FailingBroker(), "polymarket": PaperBroker()}
    ex = CrossVenueExecutor(brokers=brokers)
    result = ex.execute(make_arb(), contracts=5)

    assert result.status == "failed"
    assert result.fills == []
    # The NO-side broker was never touched.
    assert brokers["polymarket"].positions == {}


def test_failed_unwind_latches_killswitch_and_reports_exposed():
    # YES and NO are the SAME failing broker: first leg "fills" is impossible
    # here, so use a broker that fills once then fails to force the exposed path.
    class FillThenFail:
        def __init__(self):
            self.calls = 0

        def execute(self, ticker, side, action, contracts, price_cents):
            self.calls += 1
            if self.calls == 1:
                return Fill(ticker, side, action, contracts, price_cents, 0.0)
            raise RuntimeError("rejected")

    broker = FillThenFail()
    guard = RiskGuard()
    ex = CrossVenueExecutor(brokers={"kalshi": broker, "polymarket": broker}, guard=guard)
    result = ex.execute(make_arb(), contracts=1)

    assert result.status == "exposed"   # leg2 failed AND unwind failed
    assert guard.tripped                 # naked exposure latches the kill-switch


def test_guard_blocks_when_tripped():
    guard = RiskGuard()
    guard.stop()
    brokers = {"kalshi": PaperBroker(), "polymarket": PaperBroker()}
    ex = CrossVenueExecutor(brokers=brokers, guard=guard)
    result = ex.execute(make_arb(), contracts=1)

    assert result.status == "halted"
    assert brokers["kalshi"].positions == {}
