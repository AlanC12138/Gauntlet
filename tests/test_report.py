"""Rendering smoke tests.

These exist because a syntax error in report.py once survived a fully green
suite — nothing imported the module. Cheap tests that merely *execute* the
renderer catch that class of failure.
"""

import pandas as pd
import pytest

from gauntlet.backtest import run_backtest
from gauntlet.mandates import get_mandate
from gauntlet.models import (
    Allocation,
    ComplianceReport,
    Iteration,
    Position,
    RunReport,
    ScreeningFinding,
    ScreeningReport,
    SynthesisVerdict,
    Violation,
)
from gauntlet.report import render
from gauntlet.usage import LLMUsage

PRICES = pd.DataFrame(
    {"SPY": [100.0, 110.0, 121.0], "AGG": [100.0, 100.0, 100.0]},
    index=pd.to_datetime(["2020-01-31", "2020-02-29", "2020-03-31"]),
)


def _report(*, approved: bool, findings=None, violations=None, rounds=1) -> RunReport:
    allocation = Allocation(
        strategy_summary="Core-satellite.",
        positions=[
            Position(ticker="SPY", weight=0.6, rationale="beta"),
            Position(ticker="AGG", weight=0.4, rationale="ballast"),
        ],
    )
    backtest = run_backtest({"SPY": 0.6, "AGG": 0.4}, PRICES, rebalance="none")
    compliance = ComplianceReport(
        passed=not violations,
        violations=violations or [],
        checks_run=["max_position_weight"],
        sector_exposures={"diversified": 0.6, "non_equity": 0.4},
    )
    screening = ScreeningReport(
        passed=not any(f.recommend_exclude for f in (findings or [])),
        findings=findings or [],
        tickers_screened=["SPY", "AGG"],
    )
    verdict = SynthesisVerdict(
        decision="approve" if approved else "revise",
        reasoning="because",
        memo="Committee memo.\nSecond line." if approved else None,
        revision_feedback=None if approved else "Drop the offending position.",
    )
    iteration = Iteration(
        index=1,
        allocation=allocation,
        backtest=backtest,
        compliance=compliance,
        screening=screening,
        verdict=verdict,
    )
    return RunReport(
        mandate=get_mandate("balanced"),
        allocation=allocation,
        backtest=backtest,
        sector_exposures=compliance.sector_exposures,
        compliance=compliance,
        screening=screening,
        verdict=verdict,
        iterations=[iteration.model_copy(update={"index": i + 1}) for i in range(rounds)],
        approved=approved,
        usage=[
            LLMUsage(
                agent="Allocation Agent",
                model="claude-opus-5",
                input_tokens=1500,
                output_tokens=3000,
                cost_usd=0.0825,
            )
        ],
    )


def test_approved_report_renders():
    out = render(_report(approved=True))
    assert "VERDICT: APPROVED" in out
    assert "Investment committee memo" in out
    assert "Committee memo." in out
    assert "$0.0825" in out


def test_rejected_report_renders_without_a_memo():
    violations = [Violation(rule="max_position_weight", message="SPY is 60.00%, over 10.00%.")]
    out = render(_report(approved=False, violations=violations))
    assert "VERDICT: NOT APPROVED" in out
    assert "Investment committee memo" not in out
    assert "[max_position_weight]" in out
    assert "FAIL" in out


def test_loop_back_is_visible_in_the_output():
    out = render(_report(approved=True, rounds=2))
    assert "loop-back exercised" in out
    assert "Revision history" in out


def test_screening_findings_render_with_their_source():
    findings = [
        ScreeningFinding(
            ticker="SPY",
            category="Tobacco",
            concern="indirect exposure",
            excerpt="quoted text",
            source="tobacco-and-nicotine",
            recommend_exclude=False,
        )
    ]
    out = render(_report(approved=True, findings=findings))
    assert "tobacco-and-nicotine" in out
    assert "[flag]" in out


def test_single_iteration_does_not_claim_a_loop_back():
    out = render(_report(approved=True, rounds=1))
    assert "loop-back exercised" not in out
    assert "Revision history" not in out


@pytest.mark.parametrize("approved", [True, False])
def test_caveat_always_present(approved):
    """The survivorship-bias note must never be conditional on the verdict."""
    assert "survivorship-biased" in render(_report(approved=approved))
