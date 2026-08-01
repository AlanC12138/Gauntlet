"""Hardcoded demo mandates.

Phase 1 has no persistence — mandates live here until Postgres arrives in Phase 4.
Windows default to the universe's verified backtest window.
"""

from gauntlet.models import AssetClassBand, Constraints, Mandate
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
            # Typical for a growth mandate with stated drawdown control.
            max_drawdown_tolerance=0.25,
            asset_class_bands={
                "fixed_income": AssetClassBand(min_weight=0.10, max_weight=0.35),
                "equity": AssetClassBand(min_weight=0.60, max_weight=0.90),
            },
            max_volatility=0.14,
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
            # Capital-preservation mandates commonly sit at 10-15%.
            max_drawdown_tolerance=0.15,
            # A capital-preservation mandate's objective promised "meaningful
            # fixed-income exposure"; before this, nothing enforced it.
            asset_class_bands={
                "fixed_income": AssetClassBand(min_weight=0.30, max_weight=0.60),
                "equity": AssetClassBand(min_weight=0.35, max_weight=0.65),
            },
            max_volatility=0.10,
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
            "Maximize long-run growth, accepting deep drawdowns. Exclude tobacco and "
            "the GICS Energy sector outright. Note that a sector exclusion is a blunt "
            "proxy for a fossil-fuel screen: flag any remaining hydrocarbon exposure "
            "held indirectly."
        ),
        constraints=Constraints(
            max_position_weight=0.12,
            max_sector_weight=0.30,
            min_holdings=10,
            max_holdings=18,
            excluded_tickers=["PM"],
            excluded_sectors=["Energy"],
            # Aggressive mandates accept deep drawdowns explicitly.
            max_drawdown_tolerance=0.40,
            asset_class_bands={
                "equity": AssetClassBand(min_weight=0.85, max_weight=1.00),
            },
            max_volatility=0.25,
        ),
        start=start,
        end=end,
        rebalance="annual",
    )


def sustainable_transition() -> Mandate:
    """Designed to exercise the Synthesis Agent's loop-back through a genuine
    information asymmetry rather than a staged failure.

    The exclusions live in the objective text, not in `excluded_tickers` — and
    critically, `data/universe.json` records only ticker, name, and GICS sector.
    Nothing in the Allocation Agent's input reveals which utilities operate coal
    generation; DUK, SO and NEE are indistinguishable to it. Only the ESG corpus
    knows, and only the Screening Agent reads that.

    So a defensible allocation can breach the mandate on information the
    proposing agent never had, which is exactly the situation a governance layer
    exists for. Compliance passes; screening is the sole trigger.
    """
    start, end = _window()
    return Mandate(
        name="Sustainable Transition",
        risk_profile="balanced",
        objective=(
            "Long-term growth consistent with a net-zero transition mandate. "
            "Exclude any holding with material thermal coal exposure, tobacco "
            "products, or weapons manufacturing. Defensive positioning and "
            "income are valued, but not at the cost of the sustainability screen."
        ),
        constraints=Constraints(
            max_position_weight=0.10,
            max_sector_weight=0.25,
            min_holdings=12,
            max_holdings=20,
            # Same risk posture as the balanced mandate.
            max_drawdown_tolerance=0.25,
            asset_class_bands={
                "fixed_income": AssetClassBand(min_weight=0.10, max_weight=0.35),
                "equity": AssetClassBand(min_weight=0.60, max_weight=0.90),
            },
            max_volatility=0.14,
        ),
        start=start,
        end=end,
        rebalance="quarterly",
    )


def strict_diversification() -> Mandate:
    """Deterministic loop-back fixture.

    The `sustainable` mandate was built to trigger revision through an
    information asymmetry — the ESG corpus knows which utilities operate coal
    generation and `universe.json` does not. In practice that asymmetry does not
    exist: the Allocation Agent's own world knowledge covers it, so it avoids
    those names unprompted and the screen never fires.

    This mandate triggers on arithmetic instead, which no amount of model
    knowledge can route around. A 12% sector cap is tight enough that a natural
    allocation — several technology names, a couple of large banks — breaches it
    on the first attempt, while remaining satisfiable on revision. That makes it
    a reliable end-to-end exercise of the loop-back rather than a staged failure.
    """
    start, end = _window()
    return Mandate(
        name="Strict Diversification",
        risk_profile="balanced",
        objective=(
            "Maximum diversification. No sector may dominate the portfolio and no "
            "single position may be large enough to drive returns on its own. "
            "Breadth is the objective, even at the cost of expected return."
        ),
        constraints=Constraints(
            max_position_weight=0.08,
            max_sector_weight=0.12,
            min_holdings=14,
            max_holdings=20,
            # Breadth-first, but no stated appetite for deep loss.
            max_drawdown_tolerance=0.30,
            asset_class_bands={
                "fixed_income": AssetClassBand(min_weight=0.05, max_weight=0.30),
                "equity": AssetClassBand(min_weight=0.65, max_weight=0.95),
            },
            max_volatility=0.16,
        ),
        start=start,
        end=end,
        rebalance="quarterly",
    )


DEMO_MANDATES = {
    "balanced": balanced_growth,
    "conservative": conservative_income,
    "aggressive": aggressive_growth_esg,
    "sustainable": sustainable_transition,
    "strict": strict_diversification,
}


def get_mandate(name: str) -> Mandate:
    try:
        return DEMO_MANDATES[name]()
    except KeyError:
        raise KeyError(
            f"unknown mandate {name!r}; available: {', '.join(DEMO_MANDATES)}"
        ) from None
