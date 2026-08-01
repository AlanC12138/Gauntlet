"""Runtime configuration. No app database in Phase 1 — paths and API keys only."""

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR = REPO_ROOT / "data"


class Settings(BaseSettings):
    # env_file is absolute on purpose: a relative ".env" resolves against the
    # working directory, so running the CLI from anywhere but the repo root
    # would silently miss the key and fail as if none were configured.
    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env", env_prefix="GAUNTLET_", extra="ignore"
    )

    # An explicit validation_alias opts this field out of the GAUNTLET_ prefix —
    # it's the Anthropic SDK's own env var, not ours.
    anthropic_api_key: str | None = Field(default=None, validation_alias="ANTHROPIC_API_KEY")

    model: str = "claude-opus-5"
    max_tokens: int = 16_000

    universe_path: Path = DATA_DIR / "universe.json"
    price_cache_path: Path = DATA_DIR / "cache" / "prices.parquet"

    # Annualized, used for Sharpe/Sortino. 0.0 keeps the eval reference values simple.
    risk_free_rate: float = 0.0


settings = Settings()
