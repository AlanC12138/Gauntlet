"""Backtest-derived bounds for the post-execution constraints.

The property under test is not "the witness is optimal" — it is not, and the code
says so. It is that the witness is *valid*: a portfolio that breaches the
constraints it was built from proves nothing, and quoting its drawdown would be
worse than quoting none.
"""

import pytest

from gauntlet.mandates import get_mandate
from gauntlet.models import Allocation, Position
from gauntlet.reachability import _defensive_weights, cached_witness, defensive_witness
from gauntlet.universe import load_universe


def _as_allocation(weights: dict[str, float]) -> Allocation:
    return Allocation(
        strategy_summary="defensive witness",
        positions=[
            Position(ticker=t, weight=w, rationale="low volatility")
            for t, w in weights.items()
        ],
    )


@pytest.fixture(scope="module")
def witness():
    """Skips rather than fails when prices are unavailable — the same posture the
    eval harness takes toward its real-data cases."""
    from gauntlet.data.prices import PriceStoreUnavailable

    try:
        return defensive_witness(get_mandate("conservative").constraints)
    except (PriceStoreUnavailable, RuntimeError) as exc:
        pytest.skip(f"price store unavailable: {exc}")


def test_the_witness_satisfies_the_constraints_it_was_built_from(witness):
    """The load-bearing test. A witness that fails compliance is not evidence of
    anything, because no such portfolio could actually be run."""
    from gauntlet.agents.compliance import check_compliance

    mandate = get_mandate("conservative")
    report = check_compliance(
        _as_allocation(witness.weights), mandate, universe=load_universe()
    )

    # Post-execution rules are excluded: the witness exists precisely to find out
    # what drawdown is achievable, so requiring it to already meet the target
    # would be circular.
    pre_execution = [
        v for v in report.violations if v.rule not in {"max_drawdown_tolerance", "max_volatility"}
    ]
    assert pre_execution == []


def test_weights_sum_to_one(witness):
    assert abs(sum(witness.weights.values()) - 1.0) < 1e-9


def test_it_reports_drawdown_as_a_positive_magnitude(witness):
    """`max_drawdown_tolerance` is stated as "must not fall more than X", so the
    witness has to be comparable to it without a sign flip at the call site."""
    assert witness.max_drawdown > 0
    assert 0 < witness.annualized_volatility < 1


def test_the_bound_is_a_note_not_a_hard_floor(witness):
    """The asymmetry this whole module exists for.

    Real numbers make the point: the conservative mandate targets 15% drawdown,
    this heuristic reaches ~15.7%, and a live agent run achieved 14.02%. Had the
    witness been published as `lower`, a widget would have locked the client out
    of a target the system demonstrably meets.
    """
    from gauntlet.feasibility import assess

    ranges = assess(get_mandate("conservative").constraints, include_risk_bounds=True).ranges

    for field in ("max_drawdown_tolerance", "max_volatility"):
        assert ranges[field].lower is None, f"{field} must not publish a hard floor"
        assert not ranges[field].locked
        assert "not proven impossible" in ranges[field].note or "unproven" in ranges[field].note


def test_risk_bounds_are_opt_in():
    """The mandate builder calls this on every keystroke; it must not backtest."""
    from gauntlet.feasibility import assess

    ranges = assess(get_mandate("conservative").constraints).ranges

    assert ranges["max_drawdown_tolerance"].note is not None
    assert "arithmetic" in ranges["max_drawdown_tolerance"].note


def test_a_missing_price_store_yields_no_bound_not_a_wrong_one(monkeypatch):
    """No witness means "no bound available", never "unreachable" — failing to
    construct one says nothing about what is achievable."""
    from gauntlet import reachability

    monkeypatch.setattr(reachability, "_CACHE", {})
    monkeypatch.setattr(
        reachability,
        "defensive_witness",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("store down")),
    )

    assert cached_witness(get_mandate("conservative").constraints) is None


def test_asset_class_floors_are_filled_before_discretionary_weight():
    """Filling greedily by volatility first would exhaust the position budget
    before reaching a bond floor and produce a non-compliant portfolio."""
    import pandas as pd

    from gauntlet.universe import ECONOMIC_BUCKET

    constraints = get_mandate("conservative").constraints
    universe = load_universe()

    # Equities look calmest, so a naive greedy fill would take them first and
    # leave no room for the fixed-income floor.
    volatility = pd.Series(
        {
            c.ticker: (0.01 if ECONOMIC_BUCKET[c.asset_class] == "equity" else 0.99)
            for c in universe.constituents
        }
    )

    weights = _defensive_weights(constraints, universe, volatility)
    bond_weight = sum(
        w
        for t, w in weights.items()
        if ECONOMIC_BUCKET[next(c for c in universe.constituents if c.ticker == t).asset_class]
        == "fixed_income"
    )
    floor = constraints.asset_class_bands["fixed_income"].min_weight

    assert bond_weight >= floor - 1e-9


def test_every_demo_mandate_yields_a_compliant_witness():
    """Across all five presets, not just the one that motivated the feature."""
    from gauntlet.agents.compliance import check_compliance
    from gauntlet.data.prices import PriceStoreUnavailable
    from gauntlet.mandates import DEMO_MANDATES

    universe = load_universe()
    for key in DEMO_MANDATES:
        mandate = get_mandate(key)
        try:
            w = defensive_witness(mandate.constraints, universe)
        except (PriceStoreUnavailable, RuntimeError) as exc:
            pytest.skip(f"price store unavailable: {exc}")

        report = check_compliance(_as_allocation(w.weights), mandate, universe=universe)
        pre_execution = [
            v
            for v in report.violations
            if v.rule not in {"max_drawdown_tolerance", "max_volatility"}
        ]
        assert pre_execution == [], f"{key}: {[v.message for v in pre_execution]}"
