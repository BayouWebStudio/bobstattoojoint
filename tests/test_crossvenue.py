from kalshi_bot.strategy.crossvenue import find_cross_venue_arb
from kalshi_bot.venues.base import MockVenue, Quote
from kalshi_bot.xarb import scan_once


def test_quote_from_kalshi_orderbook():
    ob = {"yes": [[39, 100], [40, 50]], "no": [[54, 80], [55, 20]]}
    q = Quote.from_kalshi_orderbook("kalshi", "MKT", ob)
    assert q.yes_bid == 40            # best YES bid (last level)
    assert q.no_bid == 55             # best NO bid
    assert q.yes_ask == 100 - 55      # 45
    assert q.no_ask == 100 - 40       # 60


def test_quote_from_polymarket_books():
    yes_book = {"bids": [{"price": "0.40", "size": 10}], "asks": [{"price": "0.42", "size": 10}]}
    no_book = {"bids": [{"price": "0.55", "size": 10}], "asks": [{"price": "0.58", "size": 10}]}
    q = Quote.from_polymarket_books("polymarket", "MKT", yes_book, no_book)
    assert (q.yes_bid, q.yes_ask) == (40, 42)
    assert (q.no_bid, q.no_ask) == (55, 58)


def test_cross_venue_arb_picks_cheapest_each_side():
    # YES cheapest on A (48), NO cheapest on B (47) -> cost 95, profit 5.
    qa = Quote("A", "E", yes_bid=46, yes_ask=48, no_bid=52, no_ask=54)
    qb = Quote("B", "E", yes_bid=53, yes_ask=55, no_bid=45, no_ask=47)
    opp = find_cross_venue_arb("E", [qa, qb], threshold_cents=1)
    assert opp is not None
    assert opp.yes_venue == "A" and opp.yes_price_cents == 48
    assert opp.no_venue == "B" and opp.no_price_cents == 47
    assert opp.cost_cents == 95 and opp.profit_cents == 5


def test_no_cross_venue_arb_when_too_expensive():
    qa = Quote("A", "E", yes_bid=50, yes_ask=52, no_bid=46, no_ask=48)
    qb = Quote("B", "E", yes_bid=49, yes_ask=51, no_bid=47, no_ask=49)
    # best yes 51 + best no 48 = 99 -> profit 1, ok at threshold 1 but not at 2.
    assert find_cross_venue_arb("E", [qa, qb], threshold_cents=2) is None
    assert find_cross_venue_arb("E", [qa, qb], threshold_cents=1) is not None


def test_scan_once_needs_two_venues():
    only_one = Quote("A", "E", 46, 48, 52, 54)
    venues = {"A": MockVenue("A", {"E": only_one})}
    links = [("E", {"A": "E", "B": "E"})]  # B has no quote
    assert scan_once(links, venues) == []


def test_scan_once_finds_cross_venue_arb():
    venues = {
        "kalshi": MockVenue("kalshi", {"E": Quote("kalshi", "E", 46, 48, 52, 54)}),
        "polymarket": MockVenue("polymarket", {"E": Quote("polymarket", "E", 53, 55, 45, 47)}),
    }
    links = [("E", {"kalshi": "E", "polymarket": "E"})]
    found = scan_once(links, venues, threshold_cents=1)
    assert len(found) == 1
    assert found[0].profit_cents == 5
