from kalshi_bot.risk.sizing import Sizer, kelly_fraction


def test_kelly_zero_when_no_edge():
    # Fair price equals market price -> no edge.
    assert kelly_fraction(0.50, 50) == 0.0
    # Negative edge (overpaying) -> clamped to zero.
    assert kelly_fraction(0.40, 50) == 0.0


def test_kelly_positive_edge():
    # q=0.6, c=0.5 -> (0.6-0.5)/(1-0.5) = 0.2
    assert abs(kelly_fraction(0.60, 50) - 0.2) < 1e-9


def test_sizer_respects_position_cap():
    sizer = Sizer(bankroll_usd=10_000, max_position_usd=50, kelly_multiplier=1.0)
    # Big edge would size huge, but the $50 cap binds: 50 / $0.50 = 100 contracts.
    n = sizer.contracts_for(prob=0.90, price_cents=50)
    assert n == 100


def test_sizer_zero_on_no_edge():
    sizer = Sizer(bankroll_usd=10_000, max_position_usd=50)
    assert sizer.contracts_for(prob=0.50, price_cents=50) == 0
