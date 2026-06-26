"""Forward (out-of-sample) paper test of the fade-longshot edge.

A backtest can overfit; the honest validation is a *forward* test — pick live
longshots today, record the paper NO entry, and check the realized outcome only
after the market settles in the future. This module persists positions to a JSON
ledger (committed to the repo so it survives the ephemeral container) and lets a
later run settle them and tally out-of-sample P&L.

Workflow:
  forward scan    -> open new paper NO positions on live deep longshots
  forward settle  -> mark any now-settled positions and record realized P&L
  forward status  -> show open positions and running forward performance

Entry is the *aggressive* (cross-the-spread) NO price, ``100 - yes_bid`` — the
conservative case (~+2.5% in-sample), so the forward test does not flatter the
edge. Positions are capped per event for diversification (the key caveat from
the in-sample analysis: outcomes cluster within events).
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path

from .kalshi.client import KalshiClient
from .research.calibration import kalshi_fee_cents

log = logging.getLogger("kalshi_bot.forward")

DEFAULT_LEDGER = "data/forward_ledger.json"


@dataclass
class Position:
    ticker: str
    event_ticker: str
    category: str
    entry_yes_bid: int
    entry_yes_ask: int
    entry_no_cost: int  # what we paid for NO = 100 - yes_bid
    contracts: int
    opened_date: str
    close_time: str
    status: str = "open"            # "open" | "settled"
    result: str | None = None       # "yes" | "no"
    realized_pnl_cents: float | None = None


def market_quote_cents(m: dict) -> tuple[int, int] | None:
    """Extract (yes_bid_cents, yes_ask_cents) from a raw market dict, or None."""
    try:
        yb = round(float(m.get("yes_bid_dollars") or 0) * 100)
        ya = round(float(m.get("yes_ask_dollars") or 0) * 100)
    except (TypeError, ValueError):
        return None
    return yb, ya


class ForwardLedger:
    def __init__(self, path: str = DEFAULT_LEDGER):
        self.path = Path(path)
        self.positions: list[Position] = []
        if self.path.exists():
            raw = json.loads(self.path.read_text())
            self.positions = [Position(**p) for p in raw.get("positions", [])]

    def open_tickers(self) -> set[str]:
        return {p.ticker for p in self.positions}

    def open_positions(self) -> list[Position]:
        return [p for p in self.positions if p.status == "open"]

    def settled_positions(self) -> list[Position]:
        return [p for p in self.positions if p.status == "settled"]

    def add(self, position: Position) -> None:
        self.positions.append(position)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps({"positions": [asdict(p) for p in self.positions]}, indent=2)
        )

    def summary(self) -> str:
        settled = self.settled_positions()
        realized = sum(p.realized_pnl_cents or 0 for p in settled)
        capital = sum(p.entry_no_cost for p in settled)
        wins = sum(1 for p in settled if (p.realized_pnl_cents or 0) > 0)
        roi = (100 * realized / capital) if capital else 0.0
        win_rate = (wins / len(settled)) if settled else 0.0
        return (
            f"open={len(self.open_positions())} settled={len(settled)} "
            f"realized={realized:+.1f}c ROI={roi:+.1f}% win={win_rate:.0%}"
        )


def scan_and_open(
    ledger: ForwardLedger,
    client: KalshiClient,
    *,
    today: str,
    min_yes_ask: int = 2,
    max_yes_ask: int = 15,
    min_volume: float = 1000.0,
    contracts: int = 10,
    max_new: int = 40,
    max_pages: int = 8,
) -> list[Position]:
    """Scan live open markets for deep longshots and open paper NO positions.

    At most one position per event (diversification), entries sorted by nearest
    settlement first so the forward test produces results sooner. Skips markets
    already in the ledger.
    """
    held = ledger.open_tickers() | {p.ticker for p in ledger.settled_positions()}
    seen_events: set[str] = set()
    candidates: list[Position] = []
    cursor = None

    for _ in range(max_pages):
        params = {"limit": 200, "with_nested_markets": "true", "status": "open"}
        if cursor:
            params["cursor"] = cursor
        data = client._request("GET", "/events", signed=False, params=params)
        for e in data.get("events", []):
            event_ticker = e.get("event_ticker", "")
            for m in (e.get("markets") or []):
                q = market_quote_cents(m)
                if q is None:
                    continue
                yb, ya = q
                try:
                    vol = float(m.get("volume_fp") or 0)
                except (TypeError, ValueError):
                    vol = 0
                if not (min_yes_ask <= ya <= max_yes_ask and yb >= 1 and vol >= min_volume):
                    continue
                if m["ticker"] in held or event_ticker in seen_events:
                    continue
                seen_events.add(event_ticker)
                candidates.append(Position(
                    ticker=m["ticker"],
                    event_ticker=event_ticker,
                    category=e.get("category", "?"),
                    entry_yes_bid=yb,
                    entry_yes_ask=ya,
                    entry_no_cost=100 - yb,
                    contracts=contracts,
                    opened_date=today,
                    close_time=m.get("close_time", ""),
                ))
        cursor = data.get("cursor")
        if not cursor:
            break

    # Nearest settlement first, then take up to max_new.
    candidates.sort(key=lambda p: p.close_time or "9999")
    opened = candidates[:max_new]
    for p in opened:
        ledger.add(p)
    if opened:
        ledger.save()
    return opened


def settle_position(position: Position, market: dict) -> bool:
    """If ``market`` has settled, finalize the position's P&L. Returns True if changed."""
    status = market.get("status", "")
    result = market.get("result")
    if status not in ("settled", "finalized") or result not in ("yes", "no"):
        return False
    # NO contract pays 100c if the market resolves NO, else 0.
    payoff = 100 if result == "no" else 0
    fee = kalshi_fee_cents(position.entry_no_cost)
    position.result = result
    position.realized_pnl_cents = payoff - position.entry_no_cost - fee
    position.status = "settled"
    return True


def settle_open(ledger: ForwardLedger, client: KalshiClient) -> int:
    """Check each open position; settle the ones whose market has resolved."""
    changed = 0
    for p in ledger.open_positions():
        try:
            market = client.get_market(p.ticker)
        except Exception as exc:  # noqa: BLE001
            log.warning("could not fetch %s: %s", p.ticker, exc)
            continue
        if settle_position(p, market):
            changed += 1
            log.info("settled %s -> %s  P&L %+.1fc", p.ticker, p.result, p.realized_pnl_cents)
    if changed:
        ledger.save()
    return changed
