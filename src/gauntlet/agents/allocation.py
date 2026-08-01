"""Allocation Agent — proposes portfolio weights from a mandate.

The only LLM in the Phase 1 loop. It emits an `AllocationProposal` via structured
output; this module then validates it into an `Allocation` before anything
downstream sees it. That boundary matters: the agent's job is to propose, and
nothing here treats its output as trustworthy just because it parsed.
"""

import logging

import anthropic

from gauntlet.config import build_client, settings
from gauntlet.models import Allocation, AllocationProposal, Mandate, Position
from gauntlet.universe import Universe, load_universe
from gauntlet.usage import LLMUsage, usage_from_response

logger = logging.getLogger(__name__)

# Beyond this, the weights aren't float noise — the agent misunderstood the task
# and normalizing would paper over it.
MAX_WEIGHT_SUM_DRIFT = 0.05


class AllocationValidationError(ValueError):
    """A proposal that did not survive validation.

    Carries the call's token spend because the tokens were still billed — the
    pipeline needs them for honest cost accounting even though the proposal was
    discarded. Also carries the feedback the Allocation Agent should receive on
    its next attempt: a malformed proposal is a revision trigger, not a crash.
    """

    def __init__(self, message: str, usage: LLMUsage) -> None:
        super().__init__(message)
        self.usage = usage


class AllocationTruncatedError(RuntimeError):
    """The response was cut off before the proposal was complete.

    Distinct from a validation failure: there is no proposal to critique, and
    re-asking with identical parameters would very likely truncate identically.
    So the pipeline does not retry on this — it preserves whatever iterations
    already completed rather than discarding paid-for work.
    """

    def __init__(self, message: str, usage: LLMUsage) -> None:
        super().__init__(message)
        self.usage = usage

SYSTEM_PROMPT = """\
You are a portfolio construction analyst. Given an investment mandate and a fixed \
investable universe, you propose a set of portfolio weights.

Ground your proposal in portfolio theory: diversify across sectors and risk factors, \
size positions according to the mandate's risk profile, and avoid concentration that \
isn't compensated by expected return. Explain each position in terms of the role it \
plays in the portfolio, not in terms of a price forecast.

Hard requirements:
- Use ONLY tickers from the provided universe. Do not invent tickers.
- Weights must be positive fractions summing to exactly 1.0. No shorts, no leverage.
- Respect every constraint in the mandate.

Your allocation will be backtested against real historical price data and checked \
against the mandate's constraints programmatically. Propose something that will \
survive that, not something that merely reads well.\
"""


def _render_constraints(mandate: Mandate) -> str:
    c = mandate.constraints
    lines = [
        f"- Maximum weight in any single position: {c.max_position_weight:.1%}",
        f"- Maximum weight in any single GICS sector: {c.max_sector_weight:.1%}",
        f"- Number of holdings: between {c.min_holdings} and {c.max_holdings}",
    ]
    if c.max_drawdown_tolerance is not None:
        # Stated so the agent can aim at it, though it cannot verify it: the
        # drawdown is only known once the allocation is backtested. Getting this
        # wrong is the expected reason for a revision cycle, not a failure.
        lines.append(
            f"- Maximum peak-to-trough drawdown over the backtest window: "
            f"{c.max_drawdown_tolerance:.1%}. This is measured by executing your "
            f"allocation against historical prices, so you cannot verify it in "
            f"advance — construct defensively enough to stay within it."
        )
    if c.max_volatility is not None:
        lines.append(
            f"- Maximum annualized volatility over the backtest window: "
            f"{c.max_volatility:.1%}. Also measured after execution."
        )
    for bucket, band in c.asset_class_bands.items():
        lines.append(
            f"- {bucket.replace('_', ' ').title()} exposure must be between "
            f"{band.min_weight:.0%} and {band.max_weight:.0%} of the portfolio."
        )
    if c.excluded_tickers:
        lines.append(f"- Excluded tickers (must not appear): {', '.join(c.excluded_tickers)}")
    if c.excluded_sectors:
        lines.append(f"- Excluded sectors (must not appear): {', '.join(c.excluded_sectors)}")
    return "\n".join(lines)


def _build_prompt(mandate: Mandate, universe: Universe, feedback: str | None) -> str:
    sections = [
        "# Mandate",
        f"Name: {mandate.name}",
        f"Risk profile: {mandate.risk_profile}",
        f"Objective: {mandate.objective}",
        f"Backtest window: {mandate.start} to {mandate.end}",
        f"Rebalance schedule: {mandate.rebalance}",
        f"Initial capital: ${mandate.initial_capital:,.0f}",
        "",
        "# Constraints",
        _render_constraints(mandate),
        "",
        "# Investable universe",
        "Ticker, GICS sector, name. Instruments marked diversified are broad-market or",
        "non-equity and are exempt from sector caps.",
        "",
        universe.catalog(),
    ]
    if feedback:
        sections += [
            "",
            "# Revision required",
            "A previous proposal was rejected. Address every point below in this revision:",
            feedback,
        ]
    return "\n".join(sections)


def _validate(proposal: AllocationProposal, universe: Universe) -> Allocation:
    """Turn a raw proposal into a validated, normalized Allocation."""
    if not proposal.positions:
        raise ValueError("Allocation Agent returned no positions")

    unknown = sorted({p.ticker for p in proposal.positions} - set(universe.tickers))
    if unknown:
        raise ValueError(f"Allocation Agent proposed tickers outside the universe: {unknown}")

    duplicates = sorted(
        {p.ticker for p in proposal.positions if
         sum(q.ticker == p.ticker for q in proposal.positions) > 1}
    )
    if duplicates:
        raise ValueError(f"Allocation Agent returned duplicate positions: {duplicates}")

    non_positive = sorted(p.ticker for p in proposal.positions if p.weight <= 0)
    if non_positive:
        raise ValueError(f"Allocation Agent returned non-positive weights for: {non_positive}")

    total = sum(p.weight for p in proposal.positions)
    if abs(total - 1.0) > MAX_WEIGHT_SUM_DRIFT:
        raise ValueError(f"Allocation Agent's weights sum to {total:.4f}, expected ~1.0")
    if total != 1.0:
        logger.debug("Normalizing proposed weights from %.9f to 1.0", total)

    return Allocation(
        strategy_summary=proposal.strategy_summary,
        positions=[
            Position(ticker=p.ticker, weight=p.weight / total, rationale=p.rationale)
            for p in proposal.positions
        ],
    )


def propose_allocation(
    mandate: Mandate,
    universe: Universe | None = None,
    feedback: str | None = None,
    client: anthropic.Anthropic | None = None,
) -> tuple[Allocation, LLMUsage]:
    """Ask Claude for an allocation and validate it against the universe.

    Returns the validated allocation and the call's token spend. Usage is
    returned rather than logged so the pipeline can aggregate it across agents
    once Phase 2 adds Screening and Synthesis.

    `feedback` carries rejection reasons back from a prior proposal — the hook the
    Synthesis Agent's loop-back uses in Phase 2. Unused by the Phase 1 loop.
    """
    universe = universe or load_universe()
    client = client or build_client()

    response = client.messages.parse(
        model=settings.allocation_model_id,
        max_tokens=settings.max_tokens,
        output_config={"effort": settings.effort},
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": _build_prompt(mandate, universe, feedback)}],
        output_format=AllocationProposal,
    )

    if response.stop_reason == "refusal":
        raise RuntimeError(f"Allocation Agent request was refused: {response.stop_details}")
    if response.stop_reason == "max_tokens":
        raise AllocationTruncatedError(
            "Allocation Agent hit max_tokens before completing its proposal "
            f"({settings.max_tokens}). Lower GAUNTLET_EFFORT or raise "
            "GAUNTLET_MAX_TOKENS.",
            usage_from_response("Allocation Agent", settings.allocation_model_id, response),
        )
    if response.parsed_output is None:
        raise RuntimeError(f"Allocation Agent returned unparseable output ({response.stop_reason})")

    usage = usage_from_response("Allocation Agent", settings.allocation_model_id, response)
    logger.info(
        "Allocation Agent: %d in / %d out tokens (~$%s)",
        usage.input_tokens,
        usage.output_tokens,
        f"{usage.cost_usd:.4f}" if usage.cost_usd is not None else "unknown",
    )
    try:
        return _validate(response.parsed_output, universe), usage
    except ValueError as exc:
        # Re-raise with the usage attached so the pipeline can bill the attempt
        # and route it back for revision instead of dying.
        raise AllocationValidationError(str(exc), usage) from exc
