"""Environment-driven configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass

# API base URLs per environment.
_BASE_URLS = {
    "demo": "https://demo-api.kalshi.co/trade-api/v2",
    "prod": "https://api.elections.kalshi.com/trade-api/v2",
}
_WS_URLS = {
    "demo": "wss://demo-api.kalshi.co/trade-api/ws/v2",
    "prod": "wss://api.elections.kalshi.com/trade-api/ws/v2",
}


@dataclass(frozen=True)
class Settings:
    """Runtime settings loaded from the environment."""

    api_key_id: str | None
    private_key_path: str | None
    env: str
    max_position_usd: float
    max_daily_loss_usd: float

    @property
    def base_url(self) -> str:
        return _BASE_URLS[self.env]

    @property
    def ws_url(self) -> str:
        return _WS_URLS[self.env]

    @property
    def has_credentials(self) -> bool:
        return bool(self.api_key_id and self.private_key_path)


def load_settings() -> Settings:
    """Load settings from environment variables (and any pre-loaded .env)."""
    env = os.getenv("KALSHI_ENV", "demo").lower()
    if env not in _BASE_URLS:
        raise ValueError(f"KALSHI_ENV must be one of {list(_BASE_URLS)}, got {env!r}")

    return Settings(
        api_key_id=os.getenv("KALSHI_API_KEY_ID") or None,
        private_key_path=os.getenv("KALSHI_PRIVATE_KEY_PATH") or None,
        env=env,
        max_position_usd=float(os.getenv("MAX_POSITION_USD", "50")),
        max_daily_loss_usd=float(os.getenv("MAX_DAILY_LOSS_USD", "100")),
    )
