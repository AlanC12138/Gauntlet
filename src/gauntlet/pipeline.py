"""The propose / verify / govern loop.

    Allocation Agent  ->  proposes weights
           |
           +-- Verification Agent    (backtest, deterministic)
           +-- Compliance Agent      (rules engine, deterministic)
           +-- Screening Agent       (RAG-grounded LLM judgment)
           |
    Synthesis Agent   ->  approve, or revise and send back with feedback
           |
           +---- (bounded loop back to the Allocation Agent)

Deliberately a plain `while` loop rather than a graph framework. The control
flow *is* the interesting part of this project, so it should be readable in one
screen rather than distributed across a framework's abstractions.

The three assessments are independent of one another — none reads another's
output — which is what makes running them against the same proposal meaningful.
They execute sequentially here; Phase 4's job queue is where the backtest moves
off the request thread.
"""

import logging

import anthropic

from gauntlet.agents.allocation import AllocationValidationError, propose_allocation
from gauntlet.agents.compliance import check_compliance
from gauntlet.agents.screening import screen_allocation
from gauntlet.agents.synthesis import synthesize
from gauntlet.agents.verification import verify_allocation
from gauntlet.config import settings
from gauntlet.models import Iteration, Mandate, RunReport
from gauntlet.retrieval import BM25Retriever, Retriever
from gauntlet.universe import load_universe

logger = logging.getLogger(__name__)


def run_pipeline(
    mandate: Mandate,
    client: anthropic.Anthropic | None = None,
    retriever: Retriever | None = None,
    max_iterations: int | None = None,
) -> RunReport:
    universe = load_universe()
    retriever = retriever or BM25Retriever()
    client = client or anthropic.Anthropic(api_key=settings.anthropic_api_key)
    limit = max_iterations or settings.max_iterations

    iterations: list[Iteration] = []
    malformed: list[str] = []
    usage = []
    feedback: str | None = None

    for index in range(1, limit + 1):
        logger.info("--- Iteration %d/%d ---", index, limit)

        logger.info("Allocation Agent: proposing%s", " (revision)" if feedback else "")
        try:
            allocation, allocation_usage = propose_allocation(
                mandate, universe=universe, feedback=feedback, client=client
            )
        except AllocationValidationError as exc:
            # A malformed proposal is a revision trigger, not a crash. It never
            # reaches the assessments — there is nothing coherent to assess — but
            # it consumes an iteration and its tokens are still billed.
            logger.warning("Allocation Agent proposal rejected before assessment: %s", exc)
            usage.append(exc.usage)
            malformed.append(str(exc))
            feedback = (
                f"Your previous proposal was rejected before assessment: {exc}\n"
                "Return positive weights that sum to exactly 1.0, using only tickers "
                "from the provided universe, with no duplicates."
            )
            continue

        usage.append(allocation_usage)
        logger.info("Allocation Agent: proposed %d positions", len(allocation.positions))

        # The three assessments. Independent by construction — none sees another's
        # output, so agreement between them is evidence rather than an echo.
        backtest = verify_allocation(allocation, mandate)
        compliance = check_compliance(allocation, mandate, universe=universe)
        screening, screening_usage = screen_allocation(
            allocation, mandate, retriever=retriever, client=client
        )
        usage.append(screening_usage)

        verdict, synthesis_usage = synthesize(
            mandate, allocation, backtest, compliance, screening, iteration=index, client=client
        )
        usage.append(synthesis_usage)

        iterations.append(
            Iteration(
                index=index,
                allocation=allocation,
                backtest=backtest,
                compliance=compliance,
                screening=screening,
                verdict=verdict,
            )
        )

        if verdict.decision == "approve":
            logger.info("Approved on iteration %d", index)
            break

        feedback = verdict.revision_feedback
        if index == limit:
            logger.warning(
                "Iteration budget (%d) exhausted without approval; returning the last "
                "proposal unapproved",
                limit,
            )

    if not iterations:
        raise RuntimeError(
            f"Allocation Agent produced {len(malformed)} malformed proposal(s) in "
            f"{limit} attempts and never reached assessment. Last error: {malformed[-1]}"
        )

    final = iterations[-1]
    return RunReport(
        mandate=mandate,
        allocation=final.allocation,
        backtest=final.backtest,
        sector_exposures=final.compliance.sector_exposures,
        compliance=final.compliance,
        screening=final.screening,
        verdict=final.verdict,
        iterations=iterations,
        malformed_proposals=malformed,
        approved=final.verdict.decision == "approve",
        usage=usage,
    )
