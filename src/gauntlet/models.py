"""Pydantic contracts passed between agents.

These are the whole inter-agent protocol — there is no shared mutable state. The
split between `AllocationProposal` (what the LLM emits) and `Allocation` (what the
rest of the system consumes) is deliberate: the proposal carries no numeric
constraints, so a bad weight surfaces as an explainable validation error rather
than a schema rejection inside the SDK.
"""

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from gauntlet.usage import LLMUsage, total_cost

RebalanceFrequency = Literal["none", "monthly", "quarterly", "annual"]
RiskProfile = Literal["conservative", "balanced", "aggressive"]

# Weights are normalized before this check, so the tolerance only needs to absorb
# float error, not model sloppiness.
WEIGHT_SUM_TOLERANCE = 1e-6


class Constraints(BaseModel):
    """Numeric/structural rules. Phase 2's compliance engine enforces these;
    Phase 1 only passes them to the Allocation Agent as instructions."""

    max_position_weight: float = 0.10
    max_sector_weight: float = 0.25
    min_holdings: int = 8
    max_holdings: int = 20
    excluded_tickers: list[str] = Field(default_factory=list)
    excluded_sectors: list[str] = Field(default_factory=list)


class Mandate(BaseModel):
    name: str
    risk_profile: RiskProfile
    objective: str
    constraints: Constraints = Field(default_factory=Constraints)
    start: date
    end: date
    initial_capital: float = 1_000_000.0
    rebalance: RebalanceFrequency = "quarterly"

    @model_validator(mode="after")
    def _window_is_ordered(self) -> "Mandate":
        if self.start >= self.end:
            raise ValueError(f"start {self.start} must precede end {self.end}")
        return self


# --- Allocation Agent output ------------------------------------------------


class ProposedPosition(BaseModel):
    """One holding as the LLM emits it. No bounds — see module docstring."""

    ticker: str
    weight: float
    rationale: str


class AllocationProposal(BaseModel):
    """Raw LLM output. Weights are not yet validated or normalized."""

    strategy_summary: str
    positions: list[ProposedPosition]


class Position(BaseModel):
    ticker: str
    weight: float
    rationale: str


class Allocation(BaseModel):
    """A validated, normalized proposal. Weights sum to 1.0."""

    strategy_summary: str
    positions: list[Position]

    @model_validator(mode="after")
    def _weights_sum_to_one(self) -> "Allocation":
        total = sum(p.weight for p in self.positions)
        if abs(total - 1.0) > WEIGHT_SUM_TOLERANCE:
            raise ValueError(f"weights sum to {total:.9f}, expected 1.0")
        return self

    @property
    def weights(self) -> dict[str, float]:
        return {p.ticker: p.weight for p in self.positions}


# --- Verification Agent output ----------------------------------------------


class PerformanceMetrics(BaseModel):
    """Formulas are documented in backtest/metrics.py — the Phase 3 eval harness
    checks these against hand-computed reference values, so the definitions are
    part of the contract, not an implementation detail."""

    total_return: float
    cagr: float
    annualized_volatility: float
    sharpe_ratio: float
    sortino_ratio: float
    max_drawdown: float
    calmar_ratio: float
    trading_days: int
    years: float


class BacktestResult(BaseModel):
    start: date
    end: date
    initial_capital: float
    final_value: float
    rebalance: RebalanceFrequency
    metrics: PerformanceMetrics
    calendar_year_returns: dict[str, float]
    equity_curve: list[tuple[date, float]]
    drawdown_curve: list[tuple[date, float]]


# --- Constraint-Compliance Agent output -------------------------------------


class Violation(BaseModel):
    """One broken rule. `rule` is machine-readable so the eval harness can score
    per-rule precision/recall; `message` is what the Synthesis Agent reads."""

    rule: str
    message: str
    detail: dict[str, str] = Field(default_factory=dict)


class ComplianceReport(BaseModel):
    """Deterministic. No LLM touches this — that is the point of the agent."""

    passed: bool
    violations: list[Violation]
    checks_run: list[str]
    sector_exposures: dict[str, float]


# --- Screening Agent output --------------------------------------------------


class ScreeningFinding(BaseModel):
    ticker: str
    category: str
    concern: str
    # Verbatim support from a retrieved document. An unsupported finding is a
    # hallucination, and the validator rejects it.
    excerpt: str
    source: str
    recommend_exclude: bool


class ScreeningProposal(BaseModel):
    """Raw Screening Agent output, before grounding validation. Same
    proposal/validated split as AllocationProposal — see module docstring."""

    findings: list[ScreeningFinding]


class ScreeningReport(BaseModel):
    passed: bool
    findings: list[ScreeningFinding]
    tickers_screened: list[str]
    # Findings dropped because their excerpt appeared in no retrieved document.
    ungrounded_findings_dropped: int = 0


# --- Synthesis Agent output --------------------------------------------------


class SynthesisVerdict(BaseModel):
    decision: Literal["approve", "revise"]
    reasoning: str
    # Populated only on "revise" — fed back to the Allocation Agent verbatim.
    revision_feedback: str | None = None
    # Populated only on "approve" — the investment-committee memo.
    memo: str | None = None


class Iteration(BaseModel):
    """One trip around the propose/verify/govern loop."""

    index: int
    allocation: Allocation
    backtest: BacktestResult
    compliance: ComplianceReport
    screening: ScreeningReport
    verdict: SynthesisVerdict


class RunReport(BaseModel):
    """The full propose/verify/govern run, including any loop-back iterations."""

    mandate: Mandate
    allocation: Allocation
    backtest: BacktestResult
    sector_exposures: dict[str, float]
    compliance: ComplianceReport
    screening: ScreeningReport
    verdict: SynthesisVerdict
    # Every assessed attempt, in order. len() > 1 means the Synthesis Agent sent
    # it back.
    iterations: list[Iteration] = Field(default_factory=list)
    # Proposals rejected by validation before any assessment ran — malformed
    # weights, unknown tickers, duplicates. These consume an iteration and are
    # billed, so they are recorded rather than silently retried.
    malformed_proposals: list[str] = Field(default_factory=list)
    approved: bool = False
    usage: list[LLMUsage] = Field(default_factory=list)

    @property
    def total_cost_usd(self) -> float | None:
        return total_cost(self.usage)

    @property
    def total_tokens(self) -> int:
        return sum(u.total_tokens for u in self.usage)
