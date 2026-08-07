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
from typing import Protocol

import anthropic

from gauntlet.agents.allocation import (
    AllocationTruncatedError,
    AllocationValidationError,
    propose_allocation,
)
from gauntlet.agents.compliance import check_compliance
from gauntlet.agents.screening import screen_allocation
from gauntlet.agents.synthesis import synthesize
from gauntlet.agents.verification import verify_allocation
from gauntlet.config import build_client, settings
from gauntlet.models import Iteration, Mandate, RunReport
from gauntlet.retrieval import BM25Retriever, Retriever
from gauntlet.tracing import set_iteration
from gauntlet.universe import load_universe

logger = logging.getLogger(__name__)


class EventSink(Protocol):
    """Where stage events go. A Protocol rather than an import of the Redis
    publisher, so the loop stays testable — and runnable — with no Redis."""

    def __call__(
        self, stage: str, status: str, detail: str = "", iteration: int = 0
    ) -> None: ...


def _ignore_events(stage: str, status: str, detail: str = "", iteration: int = 0) -> None:
    """Default sink. The loop emits unconditionally and this discards it, so
    progress reporting adds no branching to the control flow being demonstrated."""


def run_pipeline(
    mandate: Mandate,
    client: anthropic.Anthropic | None = None,
    retriever: Retriever | None = None,
    max_iterations: int | None = None,
    on_event: EventSink | None = None,
) -> RunReport:
    emit = on_event or _ignore_events
    universe = load_universe()
    retriever = retriever or BM25Retriever()
    client = client or build_client()
    limit = max_iterations or settings.max_iterations

    iterations: list[Iteration] = []
    malformed: list[str] = []
    usage = []
    feedback: str | None = None

    for index in range(1, limit + 1):
        logger.info("--- Iteration %d/%d ---", index, limit)
        emit("iteration", "started", f"Iteration {index} of {limit}", index)
        # Stamps every agent call in this iteration, so a loop-back's two
        # proposals can be read side by side against the feedback between them.
        # A plain set rather than a scoped context: the loop is sequential, each
        # iteration supersedes the last, and `trace_run` unwinds the whole thing.
        set_iteration(index)

        logger.info("Allocation Agent: proposing%s", " (revision)" if feedback else "")
        emit(
            "allocation",
            "started",
            "Revising after feedback" if feedback else "Proposing an allocation",
            index,
        )
        try:
            allocation, allocation_usage = propose_allocation(
                mandate, universe=universe, feedback=feedback, client=client
            )
        except AllocationValidationError as exc:
            # A malformed proposal is a revision trigger, not a crash. It never
            # reaches the assessments — there is nothing coherent to assess — but
            # it consumes an iteration and its tokens are still billed.
            logger.warning("Allocation Agent proposal rejected before assessment: %s", exc)
            emit("allocation", "rejected", str(exc), index)
            usage.append(exc.usage)
            malformed.append(str(exc))
            feedback = (
                f"Your previous proposal was rejected before assessment: {exc}\n"
                "Return positive weights that sum to exactly 1.0, using only tickers "
                "from the provided universe, with no duplicates."
            )
            continue
        except AllocationTruncatedError as exc:
            # Retrying identical parameters would truncate identically, so this
            # ends the loop. Earlier iterations were fully assessed and paid for;
            # returning them unapproved beats discarding them.
            usage.append(exc.usage)
            emit("allocation", "truncated", str(exc), index)
            if iterations:
                logger.warning("%s — returning the last completed iteration unapproved", exc)
                break
            raise

        usage.append(allocation_usage)
        logger.info("Allocation Agent: proposed %d positions", len(allocation.positions))
        emit("allocation", "finished", f"Proposed {len(allocation.positions)} positions", index)

        # The three assessments. Screening never sees the others' output, so its
        # agreement is evidence rather than an echo. Compliance is the one
        # exception, and deliberately so: post-execution rules (drawdown
        # tolerance) are constraints on what the allocation *did*, so the
        # backtest has to run first. That ordering is the point — it is the only
        # constraint a proposer cannot satisfy by reading its own instructions.
        emit("verification", "started", "Backtesting the proposal", index)
        backtest = verify_allocation(allocation, mandate)
        emit(
            "verification",
            "finished",
            f"CAGR {backtest.metrics.cagr:.2%}, max drawdown "
            f"{backtest.metrics.max_drawdown:.2%}",
            index,
        )

        emit("compliance", "started", "Checking deterministic rules", index)
        compliance = check_compliance(allocation, mandate, universe=universe, backtest=backtest)
        emit(
            "compliance",
            "finished" if compliance.passed else "violated",
            "Passed all checks"
            if compliance.passed
            else f"{len(compliance.violations)} violation(s): "
            + ", ".join(v.rule for v in compliance.violations),
            index,
        )

        emit("screening", "started", "Screening against the ESG corpus", index)
        screening, screening_usage = screen_allocation(
            allocation, mandate, retriever=retriever, client=client
        )
        usage.append(screening_usage)
        emit(
            "screening",
            "finished" if screening.passed else "flagged",
            f"{len(screening.findings)} finding(s)",
            index,
        )

        emit("synthesis", "started", "Supervisor reviewing all three assessments", index)
        verdict, synthesis_usage = synthesize(
            mandate, allocation, backtest, compliance, screening, iteration=index, client=client
        )
        usage.append(synthesis_usage)
        emit(
            "synthesis",
            "approved" if verdict.decision == "approve" else "revise",
            verdict.reasoning[:200],
            index,
        )

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
