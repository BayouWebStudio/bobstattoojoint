"""Replay snapshots through a strategy and report performance metrics.

The backtester reuses the real :class:`~kalshi_bot.engine.Engine` step logic and
:class:`~kalshi_bot.paper.broker.PaperBroker`, so a strategy behaves identically
in backtest and paper trading. After each snapshot it marks the book to market
and records an equity point, from which it derives the headline metrics.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass

from ..engine import Engine
from ..kalshi.feed import MarketSnapshot
from ..paper.broker import PaperBroker
from ..risk.sizing import Sizer
from ..strategy.base import Strategy


@dataclass
class BacktestReport:
    trades: int
    starting_equity_usd: float
    final_equity_usd: float
    realized_pnl_usd: float
    return_pct: float
    max_drawdown_pct: float
    win_rate: float
    sharpe: float
    equity_curve: list[float]

    def __str__(self) -> str:
        return (
            f"trades={self.trades} "
            f"return={self.return_pct:+.2f}% "
            f"final=${self.final_equity_usd:.2f} "
            f"realized=${self.realized_pnl_usd:+.2f} "
            f"max_drawdown={self.max_drawdown_pct:.2f}% "
            f"win_rate={self.win_rate:.0%} "
            f"sharpe={self.sharpe:.2f}"
        )


def _max_drawdown_pct(curve: list[float]) -> float:
    peak = curve[0]
    worst = 0.0
    for v in curve:
        peak = max(peak, v)
        if peak > 0:
            worst = max(worst, (peak - v) / peak)
    return worst * 100.0


def _sharpe(curve: list[float]) -> float:
    """Annualization-agnostic Sharpe of per-step equity returns."""
    if len(curve) < 2:
        return 0.0
    rets = [
        (curve[i] - curve[i - 1]) / curve[i - 1]
        for i in range(1, len(curve))
        if curve[i - 1] != 0
    ]
    if len(rets) < 2:
        return 0.0
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    sd = math.sqrt(var)
    return (mean / sd) * math.sqrt(len(rets)) if sd > 0 else 0.0


def backtest(
    strategy: Strategy,
    snapshots: Iterable[MarketSnapshot],
    *,
    bankroll_usd: float = 1000.0,
    max_position_usd: float = 50.0,
) -> BacktestReport:
    broker = PaperBroker(starting_cash_usd=bankroll_usd)
    sizer = Sizer(bankroll_usd=bankroll_usd, max_position_usd=max_position_usd)
    engine = Engine(strategy=strategy, sizer=sizer, broker=broker)

    marks: dict[str, float] = {}
    equity_curve = [bankroll_usd]
    for snap in snapshots:
        marks[snap.ticker] = snap.yes_mid
        engine.step(snap)
        equity_curve.append(broker.cash_usd + broker.unrealized_pnl_usd(marks))

    wins = sum(1 for f in broker.fills if f.realized_pnl_usd > 0)
    closing = sum(1 for f in broker.fills if f.realized_pnl_usd != 0)
    final_equity = equity_curve[-1]

    return BacktestReport(
        trades=len(broker.fills),
        starting_equity_usd=bankroll_usd,
        final_equity_usd=final_equity,
        realized_pnl_usd=broker.realized_pnl_usd,
        return_pct=(final_equity - bankroll_usd) / bankroll_usd * 100.0,
        max_drawdown_pct=_max_drawdown_pct(equity_curve),
        win_rate=(wins / closing) if closing else 0.0,
        sharpe=_sharpe(equity_curve),
        equity_curve=equity_curve,
    )
