"""The Phase 1 agent loop: propose, then verify.

Deliberately a plain function rather than a graph framework. In Phase 2 this
grows the compliance and screening agents plus the Synthesis Agent's bounded
loop-back to the Allocation Agent — keeping that control flow explicit here is
the point (see TECH_STACK.md).
"""

import logging

import anthropic

from gauntlet.agents.allocation import propose_allocation
from gauntlet.agents.verification import verify_allocation
from gauntlet.models import Mandate, RunReport
from gauntlet.universe import load_universe, sector_exposures

logger = logging.getLogger(__name__)


def run_pipeline(mandate: Mandate, client: anthropic.Anthropic | None = None) -> RunReport:
    universe = load_universe()

    logger.info("Allocation Agent: proposing for mandate %r", mandate.name)
    allocation, allocation_usage = propose_allocation(mandate, universe=universe, client=client)
    logger.info("Allocation Agent: proposed %d positions", len(allocation.positions))

    logger.info("Verification Agent: backtesting")
    backtest = verify_allocation(allocation, mandate)
    logger.info(
        "Verification Agent: CAGR %.2f%%, max drawdown %.2f%%",
        backtest.metrics.cagr * 100,
        backtest.metrics.max_drawdown * 100,
    )

    return RunReport(
        mandate=mandate,
        allocation=allocation,
        backtest=backtest,
        sector_exposures=sector_exposures(allocation.weights),
        # One entry today; Screening and Synthesis append here in Phase 2, and a
        # loop-back means the Allocation Agent contributes more than once.
        usage=[allocation_usage],
    )
