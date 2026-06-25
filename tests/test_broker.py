from kalshi_bot.paper.broker import PaperBroker


def test_open_and_close_yes_profit():
    b = PaperBroker(starting_cash_usd=1000)
    # Buy 10 YES at 40c.
    b.execute("MKT", "yes", "buy", 10, 40)
    assert b.positions["MKT"].contracts == 10
    assert b.realized_pnl_usd == 0.0

    # Sell 10 YES at 60c -> profit (0.60-0.40)*10 = $2.00.
    fill = b.execute("MKT", "yes", "sell", 10, 60)
    assert b.positions["MKT"].contracts == 0
    assert abs(fill.realized_pnl_usd - 2.0) < 1e-9
    assert abs(b.realized_pnl_usd - 2.0) < 1e-9


def test_average_price_blends_on_add():
    b = PaperBroker()
    b.execute("MKT", "yes", "buy", 10, 40)
    b.execute("MKT", "yes", "buy", 10, 60)
    assert abs(b.positions["MKT"].avg_price_cents - 50.0) < 1e-9


def test_no_side_increases_short_yes_exposure():
    b = PaperBroker()
    # Buying NO at 30c == short YES at an implied YES price of 70c.
    b.execute("MKT", "no", "buy", 5, 30)
    pos = b.positions["MKT"]
    assert pos.contracts == -5
    assert abs(pos.avg_price_cents - 70.0) < 1e-9


def test_unrealized_marks_to_market():
    b = PaperBroker()
    b.execute("MKT", "yes", "buy", 10, 40)
    # Mark at 50c -> unrealized (0.50-0.40)*10 = $1.00.
    assert abs(b.unrealized_pnl_usd({"MKT": 50.0}) - 1.0) < 1e-9
