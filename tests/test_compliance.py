"""Constraint-Compliance Agent. Deterministic, so every case is exact."""

import pytest

from gauntlet.agents.compliance import PRE_EXECUTION_CHECKS, check_compliance
from gauntlet.mandates import get_mandate
from gauntlet.models import Allocation, Constraints, Position


def _alloc(**weights: float) -> Allocation:
    return Allocation(
        strategy_summary="test",
        positions=[Position(ticker=t, weight=w, rationale="x") for t, w in weights.items()],
    )


@pytest.fixture
def mandate():
    return get_mandate("balanced")


def _rules(report) -> set[str]:
    return {v.rule for v in report.violations}


def test_clean_allocation_passes(mandate):
    # 15 names, none above 10%, no sector above 25%, and 15% fixed income —
    # inside the balanced mandate's 10-35% band.
    weights = {
        "AAPL": 0.07, "MSFT": 0.07, "JPM": 0.07, "V": 0.06, "JNJ": 0.07,
        "UNH": 0.06, "PG": 0.07, "KO": 0.06, "HON": 0.07, "CAT": 0.06,
        "NEE": 0.07, "AMT": 0.07, "SPY": 0.05, "AGG": 0.10, "TLT": 0.05,
    }
    report = check_compliance(_alloc(**weights), mandate)
    assert report.passed
    assert report.violations == []
    # Post-execution checks are absent: no backtest was supplied.
    assert report.checks_run == PRE_EXECUTION_CHECKS


def test_position_over_limit_is_flagged(mandate):
    report = check_compliance(_alloc(AAPL=0.5, MSFT=0.5), mandate)
    assert not report.passed
    assert "max_position_weight" in _rules(report)
    offender = next(v for v in report.violations if v.rule == "max_position_weight")
    assert offender.detail["ticker"] in {"AAPL", "MSFT"}


def test_position_exactly_at_the_limit_passes(mandate):
    """Boundary: 10.00% against a 10% cap is compliant, not a violation."""
    weights = {t: 0.10 for t in ["AAPL", "JPM", "JNJ", "PG", "HON", "NEE", "AMT", "KO", "CAT", "SO"]}
    report = check_compliance(_alloc(**weights), mandate)
    assert "max_position_weight" not in _rules(report)


def test_sector_concentration_is_flagged(mandate):
    # Four IT names at 9% each = 36% Information Technology, over the 25% cap,
    # while every individual position stays under the 10% position limit.
    weights = {
        "AAPL": 0.09, "MSFT": 0.09, "NVDA": 0.09, "AVGO": 0.09,
        "JPM": 0.08, "JNJ": 0.08, "PG": 0.08, "KO": 0.08,
        "HON": 0.08, "NEE": 0.08, "AMT": 0.08, "CAT": 0.08,
    }
    report = check_compliance(_alloc(**weights), mandate)
    assert "max_sector_weight" in _rules(report)
    assert "max_position_weight" not in _rules(report)
    violation = next(v for v in report.violations if v.rule == "max_sector_weight")
    assert violation.detail["sector"] == "Information Technology"


def test_diversified_and_non_equity_are_exempt_from_sector_caps(mandate):
    """60% in broad funds and bonds must not trip a 25% sector cap — they carry
    no single-sector attribution by construction."""
    weights = {
        "SPY": 0.10, "QQQ": 0.10, "IWM": 0.10,   # 30% diversified
        "AGG": 0.10, "TLT": 0.10, "GLD": 0.10,   # 30% non-equity
        "AAPL": 0.05, "JPM": 0.05, "JNJ": 0.05, "PG": 0.05,
        "HON": 0.05, "NEE": 0.05, "AMT": 0.05, "KO": 0.05,
    }
    report = check_compliance(_alloc(**weights), mandate)
    assert "max_sector_weight" not in _rules(report)
    assert report.sector_exposures["diversified"] == pytest.approx(0.30)
    assert report.sector_exposures["non_equity"] == pytest.approx(0.30)


def test_too_few_holdings_is_flagged(mandate):
    report = check_compliance(_alloc(SPY=0.5, AGG=0.5), mandate)
    assert "holdings_count" in _rules(report)


def test_too_many_holdings_is_flagged():
    mandate = get_mandate("balanced").model_copy(
        update={"constraints": Constraints(min_holdings=2, max_holdings=3)}
    )
    report = check_compliance(_alloc(SPY=0.25, AGG=0.25, GLD=0.25, TLT=0.25), mandate)
    assert "holdings_count" in _rules(report)


def test_excluded_ticker_is_flagged():
    mandate = get_mandate("balanced").model_copy(
        update={"constraints": Constraints(min_holdings=1, excluded_tickers=["PM"])}
    )
    report = check_compliance(_alloc(PM=0.05, SPY=0.95), mandate)
    assert "excluded_tickers" in _rules(report)


def test_excluded_sector_is_flagged():
    mandate = get_mandate("balanced").model_copy(
        update={"constraints": Constraints(min_holdings=1, excluded_sectors=["Energy"])}
    )
    report = check_compliance(_alloc(XOM=0.05, CVX=0.05, SPY=0.90), mandate)
    assert "excluded_sectors" in _rules(report)
    violation = next(v for v in report.violations if v.rule == "excluded_sectors")
    assert "CVX" in violation.detail["tickers"] and "XOM" in violation.detail["tickers"]


def test_weights_not_summing_to_one_is_flagged(mandate):
    """Allocation's own validator normally prevents this, so construct it directly."""
    allocation = Allocation.model_construct(
        strategy_summary="t",
        positions=[Position(ticker="SPY", weight=0.5, rationale="x")],
    )
    report = check_compliance(allocation, mandate)
    assert "weights_sum_to_one" in _rules(report)


def test_multiple_independent_violations_all_surface():
    mandate = get_mandate("balanced").model_copy(
        update={"constraints": Constraints(max_position_weight=0.05, min_holdings=10)}
    )
    report = check_compliance(_alloc(AAPL=0.6, XOM=0.4), mandate)
    assert {"max_position_weight", "holdings_count", "max_sector_weight"} <= _rules(report)
