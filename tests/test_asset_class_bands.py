"""Asset-class bands and the volatility ceiling.

Bands are pre-execution (checkable from weights); the volatility ceiling is
post-execution, the same category as drawdown tolerance.
"""

import pandas as pd
import pytest

from gauntlet.agents.compliance import check_compliance
from gauntlet.backtest import run_backtest
from gauntlet.mandates import DEMO_MANDATES, get_mandate
from gauntlet.models import Allocation, AssetClassBand, Constraints, Position
from gauntlet.universe import asset_class_exposures

PRICES = pd.DataFrame(
    {"SPY": [100.0, 112.0, 104.0, 118.0], "AGG": [100.0, 100.5, 101.0, 101.5]},
    index=pd.to_datetime(["2020-01-31", "2020-02-29", "2020-03-31", "2020-04-30"]),
)


def _alloc(**weights: float) -> Allocation:
    return Allocation(
        strategy_summary="t",
        positions=[Position(ticker=t, weight=w, rationale="x") for t, w in weights.items()],
    )


def _mandate(**constraint_kwargs):
    base = {"min_holdings": 1, "max_position_weight": 1.0, "max_sector_weight": 1.0}
    return get_mandate("balanced").model_copy(
        update={"constraints": Constraints(**{**base, **constraint_kwargs})}
    )


def _rules(report) -> set[str]:
    return {v.rule for v in report.violations}


# --- bucket mapping ---------------------------------------------------------


def test_equity_etfs_count_as_equity_not_as_their_own_class():
    """The bucket question differs from the sector-cap question: SPY is exempt
    from sector caps but is unambiguously equity exposure. Conflating them would
    let a bond floor be satisfied by an index fund."""
    exposures = asset_class_exposures({"SPY": 0.4, "XLK": 0.2, "AAPL": 0.2, "AGG": 0.2})
    assert exposures == pytest.approx({"equity": 0.8, "fixed_income": 0.2})


def test_commodities_are_their_own_bucket():
    exposures = asset_class_exposures({"GLD": 0.1, "TLT": 0.3, "SPY": 0.6})
    assert exposures == pytest.approx({"equity": 0.6, "fixed_income": 0.3, "commodity": 0.1})


# --- band enforcement -------------------------------------------------------


def test_too_little_fixed_income_is_a_violation():
    mandate = _mandate(
        asset_class_bands={"fixed_income": AssetClassBand(min_weight=0.30, max_weight=0.60)}
    )
    report = check_compliance(_alloc(SPY=0.9, AGG=0.1), mandate)
    assert "asset_class_bands" in _rules(report)
    violation = next(v for v in report.violations if v.rule == "asset_class_bands")
    assert violation.detail["bucket"] == "fixed_income"
    assert "below" in violation.message


def test_too_much_fixed_income_is_a_violation():
    mandate = _mandate(
        asset_class_bands={"fixed_income": AssetClassBand(min_weight=0.10, max_weight=0.35)}
    )
    report = check_compliance(_alloc(SPY=0.4, AGG=0.6), mandate)
    assert "asset_class_bands" in _rules(report)
    assert "above" in next(v for v in report.violations if v.rule == "asset_class_bands").message


def test_within_the_band_passes():
    mandate = _mandate(
        asset_class_bands={"fixed_income": AssetClassBand(min_weight=0.30, max_weight=0.60)}
    )
    assert check_compliance(_alloc(SPY=0.6, AGG=0.4), mandate).passed


def test_a_missing_bucket_reads_as_zero_not_as_absent():
    """Holding no bonds at all must breach a bond floor, not silently skip it."""
    mandate = _mandate(
        asset_class_bands={"fixed_income": AssetClassBand(min_weight=0.20, max_weight=0.60)}
    )
    report = check_compliance(_alloc(SPY=1.0), mandate)
    assert "asset_class_bands" in _rules(report)


def test_bands_are_checked_without_a_backtest():
    """Pre-execution: no dependency on having run anything."""
    mandate = _mandate(
        asset_class_bands={"fixed_income": AssetClassBand(min_weight=0.30, max_weight=0.60)}
    )
    report = check_compliance(_alloc(SPY=1.0), mandate)
    assert "asset_class_bands" in report.checks_run
    assert "asset_class_bands" in _rules(report)


# --- volatility ceiling -----------------------------------------------------


def test_volatility_above_the_ceiling_is_a_violation():
    backtest = run_backtest({"SPY": 1.0}, PRICES, rebalance="none")
    mandate = _mandate(max_volatility=0.05)
    report = check_compliance(_alloc(SPY=1.0), mandate, backtest=backtest)
    assert "max_volatility" in _rules(report)


def test_volatility_below_the_ceiling_passes():
    backtest = run_backtest({"AGG": 1.0}, PRICES, rebalance="none")
    mandate = _mandate(max_volatility=0.50)
    report = check_compliance(_alloc(AGG=1.0), mandate, backtest=backtest)
    assert report.passed
    assert "max_volatility" in report.checks_run


def test_volatility_check_skipped_without_a_backtest():
    report = check_compliance(_alloc(SPY=1.0), _mandate(max_volatility=0.001))
    assert "max_volatility" not in report.checks_run
    assert "max_volatility" not in _rules(report)


# --- the demo mandates ------------------------------------------------------


def test_every_demo_mandate_carries_both_risk_limits():
    for key in DEMO_MANDATES:
        c = get_mandate(key).constraints
        assert c.max_drawdown_tolerance is not None, key
        assert c.max_volatility is not None, key


def test_risk_limits_are_ordered_by_risk_profile():
    vol = {k: get_mandate(k).constraints.max_volatility for k in
           ("conservative", "balanced", "aggressive")}
    assert vol["conservative"] < vol["balanced"] < vol["aggressive"]


def test_conservative_mandate_now_enforces_its_stated_bond_exposure():
    """Its objective promised 'meaningful fixed-income exposure'. That used to be
    prose enforced by nothing."""
    bands = get_mandate("conservative").constraints.asset_class_bands
    assert bands["fixed_income"].min_weight >= 0.25


def test_bands_are_internally_satisfiable():
    """Guards against bands whose minimums sum above 1.0 — unsatisfiable by
    construction, which would burn the whole iteration budget every run."""
    for key in DEMO_MANDATES:
        bands = get_mandate(key).constraints.asset_class_bands
        assert sum(b.min_weight for b in bands.values()) <= 1.0, key
        assert sum(b.max_weight for b in bands.values()) >= 1.0, key
