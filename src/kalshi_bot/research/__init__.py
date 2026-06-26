"""Research tools: calibration analysis and edge backtesting on settled data."""

from .calibration import (
    SettlementSample,
    calibration_table,
    fade_longshot_backtest,
    kalshi_fee_cents,
)

__all__ = [
    "SettlementSample",
    "calibration_table",
    "fade_longshot_backtest",
    "kalshi_fee_cents",
]
