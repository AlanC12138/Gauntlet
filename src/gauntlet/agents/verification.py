"""Verification Agent — executes the backtest.

No LLM. The whole premise of the project is that the allocation is checked by
running it against real historical prices, so this agent's output is a
computation, not a judgment.
"""

import logging

from gauntlet.backtest import run_backtest
from gauntlet.config import settings
from gauntlet.data.prices import price_panel
from gauntlet.models import Allocation, BacktestResult, Mandate

logger = logging.getLogger(__name__)


def verify_allocation(allocation: Allocation, mandate: Mandate) -> BacktestResult:
    weights = allocation.weights
    logger.info(
        "Backtesting %d positions over %s..%s (%s rebalance)",
        len(weights),
        mandate.start,
        mandate.end,
        mandate.rebalance,
    )
    prices = price_panel(list(weights), mandate.start, mandate.end)
    return run_backtest(
        weights=weights,
        prices=prices,
        rebalance=mandate.rebalance,
        initial_capital=mandate.initial_capital,
        risk_free_rate=settings.risk_free_rate,
    )
