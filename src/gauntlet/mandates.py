"""Hardcoded demo mandates.

Phase 1 has no persistence — mandates live here until Postgres arrives in Phase 4.
Windows default to the universe's verified backtest window.
"""

from gauntlet.models import Constraints, Mandate
from gauntlet.universe import load_universe


def _window() -> tuple:
    w = load_universe().backtest_window
    return w.start, w.end


def balanced_growth() -> Mandate:
    start, end = _window()
    return Mandate(
        name="Balanced Growth",
        risk_profile="balanced",
        objective=(
            "Long-term capital appreciation with meaningful drawdown control. "
            "Prioritize diversification across sectors over concentrated conviction."
        ),
        constraints=Constraints(
            max_position_weight=0.10,
            max_sector_weight=0.25,
            min_holdings=12,
            max_holdings=20,
        ),
        start=start,
        end=end,
        rebalance="quarterly",
    )


def conservative_income() -> Mandate:
    start, end = _window()
    return Mandate(
        name="Conservative Income",
        risk_profile="conservative",
        objective=(
            "Capital preservation with modest growth. Favor low-volatility sectors "
            "and meaningful fixed-income exposure. Shallow drawdowns matter more "
            "than keeping up in strong equity markets."
        ),
        constraints=Constraints(
            max_position_weight=0.08,
            max_sector_weight=0.20,
            min_holdings=14,
            max_holdings=20,
        ),
        start=start,
        end=end,
        rebalance="quarterly",
    )


def aggressive_growth_esg() -> Mandate:
    """Carries exclusions, so it exercises the constraint-rendering path that
    Phase 2's screening and compliance agents will enforce for real."""
    start, end = _window()
    return Mandate(
        name="Aggressive Growth (ESG-screened)",
        risk_profile="aggressive",
        objective=(
            "Maximize long-run growth, accepting deep drawdowns. Exclude fossil-fuel "
            "producers and tobacco."
        ),
        constraints=Constraints(
            max_position_weight=0.12,
            max_sector_weight=0.30,
            min_holdings=10,
            max_holdings=18,
            excluded_tickers=["PM"],
            excluded_sectors=["Energy"],
        ),
        start=start,
        end=end,
        rebalance="annual",
    )


DEMO_MANDATES = {
    "balanced": balanced_growth,
    "conservative": conservative_income,
    "aggressive": aggressive_growth_esg,
}


def get_mandate(name: str) -> Mandate:
    try:
        return DEMO_MANDATES[name]()
    except KeyError:
        raise KeyError(
            f"unknown mandate {name!r}; available: {', '.join(DEMO_MANDATES)}"
        ) from None
