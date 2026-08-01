"""Synthesis Agent — supervisor with loop-back authority.

Reviews the backtest, the compliance report, and the screening report, then
either approves the allocation with a memo or sends it back to the Allocation
Agent with specific revision feedback.

## The authority split

Not every input to this agent carries the same weight, and the asymmetry is
enforced in code rather than requested in the prompt:

  * **Compliance is binding.** If the deterministic engine reports a violation,
    the verdict is `revise` — full stop. The LLM's opinion is not consulted,
    because a supervisor that can talk its way past an arithmetic breach makes
    the deterministic engine decorative. Its reasoning is still recorded.

  * **Screening is advisory.** Those findings come from an LLM reading retrieved
    documents. Weighing a threshold-dependent exposure against the mandate's
    intent is exactly the judgment this agent exists to make, so it may approve
    over a screening flag — but it has to say why.

  * **Backtest results are informational.** Poor historical performance is not
    a rule breach. The agent may cite it in the memo or in feedback, but a
    drawdown is not grounds for rejection on its own.
"""

import logging

import anthropic
from pydantic import BaseModel

from gauntlet.config import settings
from gauntlet.models import (
    Allocation,
    BacktestResult,
    ComplianceReport,
    Mandate,
    ScreeningReport,
    SynthesisVerdict,
)
from gauntlet.usage import LLMUsage, usage_from_response

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are the supervising analyst on an investment committee. You review a proposed \
portfolio allocation together with three independent assessments and decide whether \
it is approved or must be revised.

The three inputs do not carry equal weight:

- COMPLIANCE is a deterministic rules engine, not a model. Its findings are \
arithmetic facts about the allocation. If it reports any violation, you must \
return decision "revise". You cannot approve over it or argue with it.

- SCREENING is a judgment call made by an analyst reading retrieved documents. \
You may approve despite a screening finding if the finding is threshold-dependent, \
outside the mandate's stated exclusions, or otherwise not a genuine breach — but \
you must justify that in your reasoning.

- BACKTEST results are informational. Weak historical performance is not a rule \
breach and is not on its own grounds for rejection.

When you return "revise", `revision_feedback` goes verbatim to the analyst who \
built the allocation. Make it specific and actionable: name the offending \
positions, state which rule or exclusion they breach, and say what has to change. \
Do not propose a full replacement portfolio — that is their job, not yours.

When you return "approve", write `memo` as a concise investment-committee memo: \
what the strategy is, what the backtest showed, how it stands against the \
constraints and screens, and what risks remain. Note material caveats honestly, \
including that backtest returns come from a survivorship-biased universe and \
exclude transaction costs.\
"""


class SynthesisProposal(BaseModel):
    """Raw supervisor output, before the compliance override is applied."""

    decision: str
    reasoning: str
    revision_feedback: str | None = None
    memo: str | None = None


def _render_compliance(report: ComplianceReport) -> str:
    if report.passed:
        return f"PASS — no violations across {len(report.checks_run)} deterministic checks."
    lines = [f"FAIL — {len(report.violations)} violation(s):"]
    lines += [f"  [{v.rule}] {v.message}" for v in report.violations]
    return "\n".join(lines)


def _render_screening(report: ScreeningReport) -> str:
    if not report.findings:
        return f"PASS — no findings across {len(report.tickers_screened)} holdings screened."
    lines = [f"{'PASS' if report.passed else 'FAIL'} — {len(report.findings)} finding(s):"]
    for f in report.findings:
        verb = "RECOMMENDS EXCLUSION" if f.recommend_exclude else "flag only"
        lines.append(f"  {f.ticker} [{f.category}] {verb}: {f.concern}")
        lines.append(f'    grounded in {f.source}: "{f.excerpt[:200]}"')
    return "\n".join(lines)


def _render_backtest(result: BacktestResult) -> str:
    m = result.metrics
    return (
        f"  Window            {result.start} to {result.end} ({m.years:.2f}y, "
        f"{result.rebalance} rebalance)\n"
        f"  Total return      {m.total_return:.2%}\n"
        f"  CAGR              {m.cagr:.2%}\n"
        f"  Volatility (ann.) {m.annualized_volatility:.2%}\n"
        f"  Max drawdown      {m.max_drawdown:.2%}\n"
        f"  Sharpe / Sortino  {m.sharpe_ratio:.2f} / {m.sortino_ratio:.2f}"
    )


def _build_prompt(
    mandate: Mandate,
    allocation: Allocation,
    backtest: BacktestResult,
    compliance: ComplianceReport,
    screening: ScreeningReport,
    iteration: int,
) -> str:
    holdings = "\n".join(
        f"  {p.ticker:<6} {p.weight:>7.2%}  {p.rationale}"
        for p in sorted(allocation.positions, key=lambda p: -p.weight)
    )
    exposures = "\n".join(
        f"  {sector:<24} {weight:>7.2%}" for sector, weight in compliance.sector_exposures.items()
    )
    return "\n".join(
        [
            f"# Mandate: {mandate.name} ({mandate.risk_profile})",
            mandate.objective,
            "",
            (f"Constraints: max {mandate.constraints.max_position_weight:.0%} per position, "
            f"max {mandate.constraints.max_sector_weight:.0%} per sector, "
            f"{mandate.constraints.min_holdings}-{mandate.constraints.max_holdings} holdings."),
            (f"Excluded tickers: {', '.join(mandate.constraints.excluded_tickers) or 'none'}. "
            f"Excluded sectors: {', '.join(mandate.constraints.excluded_sectors) or 'none'}."),
            "",
            f"# Proposed allocation (revision {iteration})",
            allocation.strategy_summary,
            "",
            holdings,
            "",
            "# Sector exposure",
            exposures,
            "",
            "# Backtest (Verification Agent)",
            _render_backtest(backtest),
            "",
            "# Compliance (deterministic rules engine — binding)",
            _render_compliance(compliance),
            "",
            "# Screening (ESG/controversy, RAG-grounded — advisory)",
            _render_screening(screening),
        ]
    )


def synthesize(
    mandate: Mandate,
    allocation: Allocation,
    backtest: BacktestResult,
    compliance: ComplianceReport,
    screening: ScreeningReport,
    iteration: int = 1,
    client: anthropic.Anthropic | None = None,
) -> tuple[SynthesisVerdict, LLMUsage]:
    client = client or anthropic.Anthropic(api_key=settings.anthropic_api_key)

    response = client.messages.parse(
        model=settings.synthesis_model_id,
        max_tokens=settings.max_tokens,
        system=SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": _build_prompt(
                    mandate, allocation, backtest, compliance, screening, iteration
                ),
            }
        ],
        output_format=SynthesisProposal,
    )

    if response.stop_reason == "refusal":
        raise RuntimeError(f"Synthesis Agent request was refused: {response.stop_details}")
    if response.parsed_output is None:
        raise RuntimeError(f"Synthesis Agent returned unparseable output ({response.stop_reason})")

    proposal = response.parsed_output
    decision = "revise" if proposal.decision.strip().lower() == "revise" else "approve"

    # The deterministic override. A compliance violation is not something the
    # supervisor gets to weigh — see the module docstring.
    if not compliance.passed and decision == "approve":
        logger.warning(
            "Synthesis Agent tried to approve over %d compliance violation(s); "
            "overriding to 'revise'",
            len(compliance.violations),
        )
        decision = "revise"
        proposal.revision_feedback = "\n".join(
            [
                ("The deterministic compliance engine rejected this allocation. "
                "These are arithmetic facts about the weights, not opinions:"),
                *(f"  - [{v.rule}] {v.message}" for v in compliance.violations),
                "",
                "Every violation above must be resolved in the revision.",
                f"Supervisor's additional notes: {proposal.reasoning}",
            ]
        )

    verdict = SynthesisVerdict(
        decision=decision,
        reasoning=proposal.reasoning,
        revision_feedback=proposal.revision_feedback if decision == "revise" else None,
        memo=proposal.memo if decision == "approve" else None,
    )

    if decision == "revise" and not verdict.revision_feedback:
        raise RuntimeError("Synthesis Agent returned 'revise' with no revision feedback")

    logger.info("Synthesis: %s", verdict.decision.upper())
    return verdict, usage_from_response("Synthesis Agent", settings.synthesis_model_id, response)
