"""The eval harness, run as tests.

`gauntlet eval` is the reportable artifact; these make it a CI gate too, so a
regression in the backtester or the rules engine fails the build rather than
waiting to be noticed in a report someone chose to run.
"""

import pytest

from gauntlet.evals import backtest_math, compliance
from gauntlet.evals.runner import render, run_all

# --- backtest math ------------------------------------------------------------


def test_backtest_math_suite_passes():
    result = backtest_math.run()
    assert result.passed, "; ".join(f"{c.name}: {c.detail}" for c in result.failures)


def test_backtest_math_covers_every_metric():
    """A suite that silently stopped checking a metric would keep passing."""
    agreed = {a.metric for a in backtest_math.run().agreements}
    required = {
        "equity_curve",
        "annualized_volatility",
        "sharpe_ratio",
        "sortino_ratio",
        "cagr",
        "max_drawdown",
    }
    assert required <= agreed, f"missing: {required - agreed}"


def test_backtest_math_includes_real_data():
    """Guards against synthetic-only quietly becoming the normal path.

    Two failure modes look identical from here and must not be treated alike:
    an *unreachable* store means the database simply isn't running, which is not
    a defect and skips; a *reachable but empty* store means the cache is cold,
    which is a real and fixable problem, and fails.
    """
    import pytest
    from sqlalchemy import text
    from sqlalchemy.exc import SQLAlchemyError

    from gauntlet.db import get_engine

    try:
        with get_engine().connect() as conn:
            conn.execute(text("select 1"))
    except SQLAlchemyError:
        pytest.skip("price store unreachable; real-data cases cannot run")

    names = {c.name for c in backtest_math.run().cases}
    assert any(n.startswith("real_") for n in names), (
        "real-data cases were skipped — the price cache is probably cold; "
        "run `gauntlet fetch` (or `gauntlet import-prices`)"
    )


def test_agreement_is_far_tighter_than_anything_financially_material():
    for agreement in backtest_math.run().agreements:
        assert agreement.max_absolute_error < 1e-8, agreement


# --- the regression the harness found -----------------------------------------


def test_constant_return_path_does_not_explode_sharpe():
    """A zero-variance path once produced a Sharpe around 1e15: the guard tested
    `std > 0`, but floating point gives ~1e-16 rather than 0. Found by the eval
    harness on its first run."""
    import pandas as pd

    from gauntlet.backtest import run_backtest

    prices = pd.DataFrame(
        {"A": [100.0 * 1.01**i for i in range(21)]},
        index=pd.date_range("2020-01-01", periods=21, freq="D"),
    )
    metrics = run_backtest({"A": 1.0}, prices, rebalance="none").metrics
    assert metrics.sharpe_ratio == 0.0
    assert metrics.sortino_ratio == 0.0
    assert metrics.annualized_volatility == pytest.approx(0.0, abs=1e-12)


# --- compliance ---------------------------------------------------------------


def test_compliance_suite_passes():
    result = compliance.run()
    assert result.passed, "; ".join(f"{c.name}: {c.detail}" for c in result.failures)


def test_compliance_is_perfect_on_every_rule():
    """Deterministic engine: 100% is the pass mark, not a score."""
    for score in compliance.run().rule_scores:
        assert score.perfect, (
            f"{score.rule}: {score.false_positives} FP, {score.false_negatives} FN"
        )


def test_every_rule_has_a_positive_case():
    """Perfect scores over rules that never fired would be false reassurance.
    `weights_sum_to_one` was uncovered in the first version of the suite."""
    for score in compliance.run().rule_scores:
        assert score.true_positives > 0, f"{score.rule} is never exercised"


def test_labels_are_not_read_off_the_engine():
    """Sanity check that the labels assert something: at least one case must
    expect no violations and at least one must expect several."""
    cases = compliance.labeled_cases()
    assert any(not c.expected_rules for c in cases)
    assert any(len(c.expected_rules) >= 3 for c in cases)


def test_boundary_cases_are_present():
    """Off-by-one in a comparison is the classic rules-engine defect."""
    names = {c.name for c in compliance.labeled_cases()}
    assert sum(n.startswith("boundary_") for n in names) >= 5


# --- runner -------------------------------------------------------------------


def test_run_all_reports_both_suites():
    report = run_all()
    assert {s.suite for s in report.suites} == {"backtest_math", "compliance"}
    assert report.passed


def test_render_produces_readable_output():
    text = render(run_all(["compliance"]))
    assert "compliance" in text and "precision" in text
    assert "OVERALL: PASS" in text
