"""The post-execution constraint.

Every other rule checks the weights, which a proposer told the limits can satisfy
directly. This one checks what the allocation *did*, which is only knowable after
the backtest runs — so it is the one constraint a competent proposer can still
miss, and the one that makes the loop-back load-bearing rather than ornamental.
"""

import pandas as pd
import pytest

from gauntlet.agents.compliance import check_compliance
from gauntlet.backtest import run_backtest
from gauntlet.mandates import get_mandate
from gauntlet.models import Allocation, Constraints, Position

# SPY rises 20%, then falls to 76, then partially recovers; AGG is flat.
# Held 50/50 the portfolio peaks at 1.10 and troughs at 0.88 — exactly a 20%
# drawdown, measured from the running peak rather than from the start.
CRASH_PRICES = pd.DataFrame(
    {"SPY": [100.0, 120.0, 76.0, 90.0], "AGG": [100.0, 100.0, 100.0, 100.0]},
    index=pd.to_datetime(["2020-01-31", "2020-02-29", "2020-03-31", "2020-04-30"]),
)
CALM_PRICES = pd.DataFrame(
    {"SPY": [100.0, 102.0, 101.0, 105.0], "AGG": [100.0, 100.0, 100.0, 100.0]},
    index=pd.to_datetime(["2020-01-31", "2020-02-29", "2020-03-31", "2020-04-30"]),
)


def _alloc() -> Allocation:
    return Allocation(
        strategy_summary="t",
        positions=[
            Position(ticker="SPY", weight=0.5, rationale="x"),
            Position(ticker="AGG", weight=0.5, rationale="x"),
        ],
    )


def _mandate(tolerance: float | None):
    return get_mandate("balanced").model_copy(
        update={
            "constraints": Constraints(
                min_holdings=1, max_position_weight=1.0, max_drawdown_tolerance=tolerance
            )
        }
    )


def _rules(report) -> set[str]:
    return {v.rule for v in report.violations}


def test_breaching_the_tolerance_is_a_violation():
    backtest = run_backtest(_alloc().weights, CRASH_PRICES, rebalance="none")
    assert backtest.metrics.max_drawdown == pytest.approx(-0.20)

    report = check_compliance(_alloc(), _mandate(0.15), backtest=backtest)
    assert not report.passed
    assert "max_drawdown_tolerance" in _rules(report)

    violation = next(v for v in report.violations if v.rule == "max_drawdown_tolerance")
    assert "20.00%" in violation.message and "15.00%" in violation.message


def test_staying_within_the_tolerance_passes():
    backtest = run_backtest(_alloc().weights, CRASH_PRICES, rebalance="none")
    report = check_compliance(_alloc(), _mandate(0.25), backtest=backtest)
    assert report.passed
    assert "max_drawdown_tolerance" in report.checks_run


def test_a_calm_path_passes_a_tight_tolerance():
    backtest = run_backtest(_alloc().weights, CALM_PRICES, rebalance="none")
    report = check_compliance(_alloc(), _mandate(0.05), backtest=backtest)
    assert report.passed


def test_omitting_the_backtest_skips_rather_than_passes_the_check():
    """A skipped check must never be reported as a passed one — otherwise a
    caller that forgets the backtest gets a false clean bill of health."""
    report = check_compliance(_alloc(), _mandate(0.01))
    assert "max_drawdown_tolerance" not in report.checks_run
    assert "max_drawdown_tolerance" not in _rules(report)


def test_no_tolerance_means_no_check_even_with_a_backtest():
    backtest = run_backtest(_alloc().weights, CRASH_PRICES, rebalance="none")
    report = check_compliance(_alloc(), _mandate(None), backtest=backtest)
    assert report.passed
    assert "max_drawdown_tolerance" not in report.checks_run


def test_weight_rules_still_evaluate_without_a_backtest():
    """Pre-execution checks must not become dependent on execution."""
    over_concentrated = Allocation(
        strategy_summary="t", positions=[Position(ticker="SPY", weight=1.0, rationale="x")]
    )
    mandate = get_mandate("balanced").model_copy(
        update={"constraints": Constraints(max_position_weight=0.10, min_holdings=1)}
    )
    report = check_compliance(over_concentrated, mandate)
    assert "max_position_weight" in _rules(report)


def test_drawdown_exactly_at_the_tolerance_passes():
    """Boundary: a 20.00% fall against a 20% tolerance is compliant."""
    backtest = run_backtest(_alloc().weights, CRASH_PRICES, rebalance="none")
    report = check_compliance(_alloc(), _mandate(0.20), backtest=backtest)
    assert "max_drawdown_tolerance" not in _rules(report)


def test_demo_mandates_carry_plausible_tolerances():
    """Guards against a tolerance set to force a demo outcome. Ordering by risk
    profile is the sanity check: conservative < balanced < aggressive."""
    tol = {
        k: get_mandate(k).constraints.max_drawdown_tolerance
        for k in ("conservative", "balanced", "aggressive")
    }
    assert all(t is not None for t in tol.values())
    assert tol["conservative"] < tol["balanced"] < tol["aggressive"]
    assert 0.05 <= tol["conservative"] <= 0.20, "implausible for capital preservation"
    assert 0.30 <= tol["aggressive"] <= 0.60, "implausible for an aggressive mandate"
