"""Pre-execution feasibility.

The regression case is real: a 30% fixed-income floor under an 8% position cap
against a universe holding two bond funds is impossible, and it shipped. These
tests pin the arithmetic that now catches that class of error in milliseconds
instead of in live iterations.
"""

import pytest

from gauntlet.feasibility import (
    DEFERRED_FIELDS,
    assess,
    available_instruments,
    feasible_ranges,
    find_problems,
)
from gauntlet.mandates import DEMO_MANDATES, get_mandate
from gauntlet.models import AssetClassBand, Constraints

# The universe before yesterday's fix carried only AGG and TLT.
PRE_FIX_BOND_EXCLUSIONS = ["SHY", "IEF", "TIP", "LQD", "HYG"]


def test_every_demo_mandate_is_feasible():
    for key in DEMO_MANDATES:
        problems = find_problems(get_mandate(key).constraints)
        assert problems == [], f"{key}: {problems}"


def test_default_constraints_are_feasible():
    """`Constraints()` is what an unspecified mandate falls back to; it must not
    be a trap."""
    assert find_problems(Constraints()) == []


# --- the regression -----------------------------------------------------------


def test_the_historical_infeasible_mandate_is_caught():
    constraints = Constraints(
        max_position_weight=0.08,
        min_holdings=14,
        max_holdings=20,
        excluded_tickers=PRE_FIX_BOND_EXCLUSIONS,
        asset_class_bands={"fixed_income": AssetClassBand(min_weight=0.30, max_weight=0.60)},
    )
    problems = find_problems(constraints)
    assert any("fixed_income" in p and "unreachable" in p for p in problems)
    # The message must carry the arithmetic, not just say "infeasible" — that is
    # what makes it actionable in a UI.
    assert any("16%" in p for p in problems)


def test_the_same_mandate_is_feasible_against_the_current_universe():
    """Same numbers, full bond menu. Confirms the fix was the universe, not the
    mandate."""
    constraints = Constraints(
        max_position_weight=0.08,
        min_holdings=14,
        max_holdings=20,
        asset_class_bands={"fixed_income": AssetClassBand(min_weight=0.30, max_weight=0.60)},
    )
    assert find_problems(constraints) == []


# --- individual failure modes -------------------------------------------------


def test_cannot_reach_full_investment():
    problems = find_problems(
        Constraints(max_position_weight=0.06, min_holdings=10, max_holdings=14)
    )
    assert any("100%" in p and "unreachable" in p for p in problems)


def test_holdings_bounds_inverted():
    problems = find_problems(Constraints(min_holdings=20, max_holdings=10))
    assert any("min_holdings" in p for p in problems)


def test_band_minimums_summing_above_one():
    problems = find_problems(
        Constraints(
            asset_class_bands={
                "equity": AssetClassBand(min_weight=0.70),
                "fixed_income": AssetClassBand(min_weight=0.50),
            }
        )
    )
    assert any("sum to" in p for p in problems)


def test_band_inverted_within_a_bucket():
    problems = find_problems(
        Constraints(
            asset_class_bands={"equity": AssetClassBand(min_weight=0.80, max_weight=0.40)}
        )
    )
    assert any("exceeds maximum" in p for p in problems)


def test_exclusions_can_starve_the_holdings_floor():
    universe_tickers = [c.ticker for c in available_instruments(Constraints())]
    problems = find_problems(
        Constraints(min_holdings=10, excluded_tickers=universe_tickers[:-5])
    )
    assert any("after exclusions" in p for p in problems)


def test_sector_exclusion_does_not_remove_broad_funds():
    """A sector ban reaches instruments whose weight counts toward that sector.
    A broad fund's weight was never attributed to it, so it survives — matching
    the compliance engine rather than diverging from it."""
    remaining = {c.ticker for c in available_instruments(Constraints(excluded_sectors=["Energy"]))}
    assert "XOM" not in remaining and "XLE" not in remaining
    assert "SPY" in remaining


# --- ranges -------------------------------------------------------------------


def test_position_cap_lower_bound_reflects_full_investment():
    ranges = feasible_ranges(Constraints(max_holdings=20, max_position_weight=0.10))
    assert ranges["max_position_weight"].lower == pytest.approx(1 / 20)


def test_position_cap_lower_bound_tightens_for_a_bond_floor():
    """With few instruments in a bucket, the floor — not full investment — sets
    the binding lower bound on the position cap."""
    ranges = feasible_ranges(
        Constraints(
            max_holdings=20,
            max_position_weight=0.08,
            excluded_tickers=PRE_FIX_BOND_EXCLUSIONS,
            asset_class_bands={"fixed_income": AssetClassBand(min_weight=0.30)},
        )
    )
    # Two bond funds must carry 30% between them: 15% each.
    assert ranges["max_position_weight"].lower == pytest.approx(0.15)
    assert "fixed_income" in ranges["max_position_weight"].note


def test_bucket_floor_upper_bound_is_its_capacity():
    constraints = Constraints(
        max_position_weight=0.08,
        max_holdings=20,
        asset_class_bands={"fixed_income": AssetClassBand(min_weight=0.30, max_weight=0.60)},
    )
    ranges = feasible_ranges(constraints)
    # 7 bond funds at 8% = 56%, below the 60% band maximum, so capacity binds.
    assert ranges["asset_class_bands.fixed_income.min_weight"].upper == pytest.approx(0.56)


def test_bucket_floor_upper_bound_leaves_room_for_other_floors():
    ranges = feasible_ranges(
        Constraints(
            max_position_weight=0.50,
            max_holdings=20,
            asset_class_bands={
                "fixed_income": AssetClassBand(min_weight=0.20),
                "equity": AssetClassBand(min_weight=0.60),
            },
        )
    )
    # Capacity is huge at a 50% cap, so the other bucket's floor is what binds.
    assert ranges["asset_class_bands.fixed_income.min_weight"].upper == pytest.approx(0.40)


def test_min_holdings_cannot_exceed_max_or_availability():
    ranges = feasible_ranges(Constraints(max_holdings=12))
    assert ranges["min_holdings"].upper == 12


def test_post_execution_fields_are_reported_but_unbounded():
    """Reachability of a drawdown target is an optimization, not an inequality.
    Returning a bound here would be a guess presented as arithmetic."""
    ranges = feasible_ranges(Constraints())
    for field in DEFERRED_FIELDS:
        assert ranges[field].lower is None and ranges[field].upper is None
        assert "after execution" in ranges[field].note


def test_ranges_are_interdependent():
    """Raising max_holdings must widen the legal position-cap range — the reason
    a widget has to re-request every field rather than validate one."""
    tight = feasible_ranges(Constraints(max_holdings=10))["max_position_weight"].lower
    loose = feasible_ranges(Constraints(max_holdings=40))["max_position_weight"].lower
    assert loose < tight


# --- report -------------------------------------------------------------------


def test_assess_combines_problems_and_ranges():
    report = assess(get_mandate("conservative").constraints)
    assert report.feasible and report.problems == []
    assert "max_position_weight" in report.ranges

    broken = assess(Constraints(max_position_weight=0.06, max_holdings=14))
    assert not broken.feasible and broken.problems
    # Ranges still come back on an infeasible set — that is how a UI shows the
    # user the way out.
    assert broken.ranges["max_position_weight"].lower == pytest.approx(1 / 14)
