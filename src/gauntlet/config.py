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

    # Default for every agent. The per-agent overrides below exist because the
    # roles have genuinely different requirements: proposing is generative,
    # screening is grounded classification, supervising is the governance layer
    # and wants the strongest model. Running a cheaper proposer under a strong
    # supervisor is a legitimate configuration, not a downgrade — it is the
    # setup where the deterministic checks and loop-back actually earn their
    # keep. Each falls back to `model` when unset.
    model: str = "claude-opus-5"
    allocation_model: str | None = None
    screening_model: str | None = None
    synthesis_model: str | None = None

    # Adaptive thinking is on by default on Opus 5 and counts against max_tokens.
    # A first proposal runs ~8k output tokens; a revision costs more, because
    # "get the drawdown under 15%" is harder than "propose something reasonable".
    # 16k truncated mid-revision in practice, so this carries real headroom.
    max_tokens: int = 32_000

    # Thinking depth. `high` is the API default, but revisions are open-ended
    # here in a way that punishes it: we ask the Allocation Agent to hit a
    # drawdown target it has no tool to measure, so at `high` it deliberates
    # until it truncates — observed blowing past 32k output tokens. `medium`
    # bounds that, and Opus 5 is strong at it.
    effort: str = "medium"

    # Explicit, and load-bearing: the SDK refuses non-streaming requests it
    # estimates will exceed ~10 minutes unless a timeout is set outright. With
    # max_tokens this high, that guard would otherwise reject our own calls.
    request_timeout_seconds: float = 900.0

    @property
    def allocation_model_id(self) -> str:
        return self.allocation_model or self.model

    @property
    def screening_model_id(self) -> str:
        return self.screening_model or self.model

    @property
    def synthesis_model_id(self) -> str:
        return self.synthesis_model or self.model

    universe_path: Path = DATA_DIR / "universe.json"
    price_cache_path: Path = DATA_DIR / "cache" / "prices.parquet"

    # How many ESG notes to put in the Screening Agent's context.
    screening_top_k: int = 6
    # Bound on the propose -> verify -> govern loop. Without this a Synthesis
    # Agent and an Allocation Agent that disagree would run until the budget dies.
    max_iterations: int = 3

    # Annualized, used for Sharpe/Sortino. 0.0 keeps the eval reference values simple.
    risk_free_rate: float = 0.0


settings = Settings()


def build_client():
    """The one place an Anthropic client is constructed.

    Centralized so the timeout above is applied everywhere — a client built
    ad hoc elsewhere would silently reinstate the SDK's long-request guard.
    """
    import anthropic

    return anthropic.Anthropic(
        api_key=settings.anthropic_api_key,
        timeout=settings.request_timeout_seconds,
    )
