"""Backtesting: record market snapshots and replay them through a strategy."""

from .record import read_snapshots, write_snapshots
from .runner import BacktestReport, backtest

__all__ = ["read_snapshots", "write_snapshots", "backtest", "BacktestReport"]
