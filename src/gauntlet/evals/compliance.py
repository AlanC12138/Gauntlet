"""Eval suite 2 — compliance-checker precision and recall.

Every case below carries a hand-derived label: the set of rules that *should*
fire, worked out from the mandate's meaning rather than by running the engine and
recording what it did. That distinction is the whole value of the suite. Labels
read off the implementation would make this a tautology that passes forever,
including through a rewrite that silently changes behavior.

## What a perfect score means here

The engine is deterministic, so unlike an LLM classifier the expected result is
100% precision and 100% recall. That does not make the measurement pointless — it
makes it a *conformance* test. The labels are an independent statement of the
specification, so a divergence means either the code or the spec is wrong, and
either way something needs attention. Rule engines in regulated settings are
validated exactly this way.

Read the number accordingly: 100% is the pass mark, not an achievement, and
anything below it is a defect rather than a score.

## Case mix

Three kinds, and the last is the one that catches real bugs:

* **Positives** — each rule violated in isolation, so a failure localizes.
* **Boundaries** — sitting exactly on a limit, which must NOT fire. Off-by-one
  in a comparison is the most common rules-engine defect.
* **Tricky negatives** — allocations that superficially look like breaches and
  are not: 60% in broad-market funds against a 25% sector cap, gold against an
  equity floor, a sector exclusion that must spare a diversified fund. An engine
  that flags everything scores perfect recall; only these catch it.
"""

import logging
from dataclasses import dataclass, field

import pandas as pd

from gauntlet.agents.compliance import (
    POST_EXECUTION_CHECKS,
    PRE_EXECUTION_CHECKS,
    check_compliance,
)
from gauntlet.backtest import run_backtest
from gauntlet.evals.models import CaseResult, EvalSuiteResult, RuleScore
from gauntlet.models import Allocation, AssetClassBand, Constraints, Mandate, Position

logger = logging.getLogger(__name__)

ALL_RULES = list(PRE_EXECUTION_CHECKS) + list(POST_EXECUTION_CHECKS)

_DATES = pd.to_datetime(["2020-01-31", "2020-02-29", "2020-03-31", "2020-04-30"])

# SPY peaks at 120 then falls to 76; held 50/50 with flat AGG the portfolio
# troughs at exactly -20% from its peak. Volatility is high by construction.
CRASH = pd.DataFrame(
    {"SPY": [100.0, 120.0, 76.0, 90.0], "AGG": [100.0] * 4}, index=_DATES
)
# Barely moves: drawdown and volatility both near zero.
CALM = pd.DataFrame(
    {"SPY": [100.0, 100.5, 100.2, 100.8], "AGG": [100.0] * 4}, index=_DATES
)


@dataclass
class LabeledCase:
    name: str
    constraints: Constraints
    weights: dict[str, float]
    # Ground truth, derived by hand from the mandate's meaning.
    expected_rules: set[str] = field(default_factory=set)
    prices: pd.DataFrame | None = None
    note: str = ""


def _permissive(**overrides) -> Constraints:
    """Constraints that fire nothing, so each case isolates one rule."""
    base = {
        "max_position_weight": 1.0,
        "max_sector_weight": 1.0,
        "min_holdings": 1,
        "max_holdings": 60,
    }
    return Constraints(**{**base, **overrides})


def labeled_cases() -> list[LabeledCase]:
    cases: list[LabeledCase] = []

    # ---- clean -------------------------------------------------------------
    cases.append(
        LabeledCase(
            name="clean_diversified",
            constraints=Constraints(
                max_position_weight=0.10, max_sector_weight=0.25, min_holdings=8, max_holdings=20
            ),
            weights={
                "AAPL": 0.09, "MSFT": 0.09, "JPM": 0.09, "JNJ": 0.09, "PG": 0.09,
                "HON": 0.09, "NEE": 0.09, "AMT": 0.09, "SPY": 0.09, "AGG": 0.09,
                "TLT": 0.09, "GLD": 0.01,
            },
            expected_rules=set(),
        )
    )

    # ---- one rule at a time ------------------------------------------------
    cases.append(
        LabeledCase(
            name="position_over_cap",
            constraints=_permissive(max_position_weight=0.10),
            weights={"AAPL": 0.5, "SPY": 0.5},
            expected_rules={"max_position_weight"},
        )
    )
    cases.append(
        LabeledCase(
            name="sector_over_cap",
            constraints=_permissive(max_sector_weight=0.25),
            weights={"AAPL": 0.15, "MSFT": 0.15, "NVDA": 0.10, "AGG": 0.60},
            expected_rules={"max_sector_weight"},
            note="40% Information Technology against a 25% cap.",
        )
    )
    cases.append(
        LabeledCase(
            name="too_few_holdings",
            constraints=_permissive(min_holdings=10),
            weights={"SPY": 0.5, "AGG": 0.5},
            expected_rules={"holdings_count"},
        )
    )
    cases.append(
        LabeledCase(
            name="too_many_holdings",
            constraints=_permissive(max_holdings=3),
            weights={"SPY": 0.25, "AGG": 0.25, "TLT": 0.25, "GLD": 0.25},
            expected_rules={"holdings_count"},
        )
    )
    cases.append(
        LabeledCase(
            name="excluded_ticker_held",
            constraints=_permissive(excluded_tickers=["PM"]),
            weights={"PM": 0.1, "SPY": 0.9},
            expected_rules={"excluded_tickers"},
        )
    )
    cases.append(
        LabeledCase(
            name="excluded_sector_held",
            constraints=_permissive(excluded_sectors=["Energy"]),
            weights={"XOM": 0.1, "CVX": 0.1, "SPY": 0.8},
            expected_rules={"excluded_sectors"},
        )
    )
    cases.append(
        LabeledCase(
            name="bond_floor_breached",
            constraints=_permissive(
                asset_class_bands={"fixed_income": AssetClassBand(min_weight=0.30, max_weight=0.60)}
            ),
            weights={"SPY": 0.9, "AGG": 0.1},
            expected_rules={"asset_class_bands"},
        )
    )
    cases.append(
        LabeledCase(
            name="bond_ceiling_breached",
            constraints=_permissive(
                asset_class_bands={"fixed_income": AssetClassBand(min_weight=0.05, max_weight=0.30)}
            ),
            weights={"SPY": 0.3, "AGG": 0.7},
            expected_rules={"asset_class_bands"},
        )
    )
    cases.append(
        LabeledCase(
            name="drawdown_over_tolerance",
            constraints=_permissive(max_drawdown_tolerance=0.10),
            weights={"SPY": 0.5, "AGG": 0.5},
            prices=CRASH,
            expected_rules={"max_drawdown_tolerance"},
            note="Realized -20% against a 10% tolerance.",
        )
    )
    cases.append(
        LabeledCase(
            name="volatility_over_ceiling",
            constraints=_permissive(max_volatility=0.05),
            weights={"SPY": 0.5, "AGG": 0.5},
            prices=CRASH,
            expected_rules={"max_volatility"},
        )
    )

    # ---- boundaries: exactly at the limit must NOT fire ---------------------
    cases.append(
        LabeledCase(
            name="boundary_position_exactly_at_cap",
            constraints=_permissive(max_position_weight=0.25),
            weights={"AAPL": 0.25, "JPM": 0.25, "SPY": 0.25, "AGG": 0.25},
            expected_rules=set(),
        )
    )
    cases.append(
        LabeledCase(
            name="boundary_sector_exactly_at_cap",
            constraints=_permissive(max_sector_weight=0.20),
            weights={"AAPL": 0.10, "MSFT": 0.10, "AGG": 0.80},
            expected_rules=set(),
            note="Information Technology lands on exactly 20.00%.",
        )
    )
    cases.append(
        LabeledCase(
            name="boundary_holdings_exactly_at_min",
            constraints=_permissive(min_holdings=2),
            weights={"SPY": 0.5, "AGG": 0.5},
            expected_rules=set(),
        )
    )
    cases.append(
        LabeledCase(
            name="boundary_holdings_exactly_at_max",
            constraints=_permissive(max_holdings=2),
            weights={"SPY": 0.5, "AGG": 0.5},
            expected_rules=set(),
        )
    )
    cases.append(
        LabeledCase(
            name="boundary_drawdown_exactly_at_tolerance",
            constraints=_permissive(max_drawdown_tolerance=0.20),
            weights={"SPY": 0.5, "AGG": 0.5},
            prices=CRASH,
            expected_rules=set(),
            note="Exactly -20.00% against a 20% tolerance.",
        )
    )
    cases.append(
        LabeledCase(
            name="boundary_band_exactly_at_floor",
            constraints=_permissive(
                asset_class_bands={"fixed_income": AssetClassBand(min_weight=0.40, max_weight=0.60)}
            ),
            weights={"SPY": 0.6, "AGG": 0.4},
            expected_rules=set(),
        )
    )

    # ---- tricky negatives: look like breaches, are not ----------------------
    cases.append(
        LabeledCase(
            name="broad_funds_do_not_trip_sector_caps",
            constraints=_permissive(max_sector_weight=0.10),
            weights={"SPY": 0.4, "QQQ": 0.3, "IWM": 0.3},
            expected_rules=set(),
            note="100% equity, zero sector-attributed weight. Exempt by construction.",
        )
    )
    cases.append(
        LabeledCase(
            name="bonds_do_not_satisfy_an_equity_floor",
            constraints=_permissive(
                asset_class_bands={"equity": AssetClassBand(min_weight=0.60, max_weight=1.0)}
            ),
            weights={"AGG": 0.5, "TLT": 0.3, "SPY": 0.2},
            expected_rules={"asset_class_bands"},
            note="Only 20% is equity; the bond sleeve must not count toward it.",
        )
    )
    cases.append(
        LabeledCase(
            name="gold_is_not_equity_and_not_fixed_income",
            constraints=_permissive(
                asset_class_bands={"fixed_income": AssetClassBand(min_weight=0.30, max_weight=0.60)}
            ),
            weights={"GLD": 0.4, "SPY": 0.3, "AGG": 0.3},
            expected_rules=set(),
            note="Fixed income is exactly 30%; gold sits in its own bucket.",
        )
    )
    cases.append(
        LabeledCase(
            name="sector_exclusion_spares_broad_funds",
            constraints=_permissive(excluded_sectors=["Information Technology"]),
            weights={"SPY": 0.5, "QQQ": 0.5},
            expected_rules=set(),
            note="QQQ is tech-heavy but its weight is never attributed to a sector.",
        )
    )
    cases.append(
        LabeledCase(
            name="sector_etf_does_count_toward_its_sector",
            constraints=_permissive(max_sector_weight=0.20),
            weights={"XLK": 0.4, "AGG": 0.6},
            expected_rules={"max_sector_weight"},
            note="XLK is `constituent` treatment — the mirror of the case above.",
        )
    )
    cases.append(
        LabeledCase(
            name="calm_path_passes_a_tight_drawdown_limit",
            constraints=_permissive(max_drawdown_tolerance=0.01, max_volatility=0.50),
            weights={"SPY": 0.5, "AGG": 0.5},
            prices=CALM,
            expected_rules=set(),
        )
    )
    cases.append(
        LabeledCase(
            name="risk_limits_unset_are_never_checked",
            constraints=_permissive(),
            weights={"SPY": 0.5, "AGG": 0.5},
            prices=CRASH,
            expected_rules=set(),
            note="A -20% drawdown with no tolerance declared is not a violation.",
        )
    )

    cases.append(
        LabeledCase(
            name="weights_do_not_sum_to_one",
            constraints=_permissive(),
            weights={"SPY": 0.5, "AGG": 0.3},
            expected_rules={"weights_sum_to_one"},
            note="80% invested. Bypasses the Allocation validator so the engine is "
            "what gets tested.",
        )
    )

    # ---- several at once ----------------------------------------------------
    cases.append(
        LabeledCase(
            name="multiple_simultaneous_violations",
            constraints=Constraints(
                max_position_weight=0.10,
                max_sector_weight=0.15,
                min_holdings=10,
                max_holdings=20,
                excluded_tickers=["PM"],
            ),
            weights={"AAPL": 0.4, "MSFT": 0.3, "PM": 0.3},
            expected_rules={
                "max_position_weight",
                "max_sector_weight",
                "holdings_count",
                "excluded_tickers",
            },
        )
    )

    return cases


def _build_allocation(weights: dict[str, float]) -> Allocation:
    positions = [Position(ticker=t, weight=w, rationale="eval") for t, w in weights.items()]
    payload = {"strategy_summary": "eval", "positions": positions}
    if abs(sum(weights.values()) - 1.0) > 1e-9:
        # Deliberately malformed cases bypass the model validator so the engine,
        # not Pydantic, is the thing under test.
        return Allocation.model_construct(**payload)
    return Allocation(**payload)


def _mandate_for(constraints: Constraints) -> Mandate:
    import datetime as dt

    return Mandate(
        name="eval",
        risk_profile="balanced",
        objective="eval",
        constraints=constraints,
        start=dt.date(2013, 1, 1),
        end=dt.date(2025, 12, 31),
    )


def run() -> EvalSuiteResult:
    cases = labeled_cases()
    counts = {rule: {"tp": 0, "fp": 0, "fn": 0, "tn": 0} for rule in ALL_RULES}
    results: list[CaseResult] = []

    for case in cases:
        backtest = None
        if case.prices is not None:
            backtest = run_backtest(case.weights, case.prices, rebalance="none")

        report = check_compliance(
            _build_allocation(case.weights), _mandate_for(case.constraints), backtest=backtest
        )
        predicted = {v.rule for v in report.violations}

        for rule in ALL_RULES:
            should = rule in case.expected_rules
            did = rule in predicted
            if should and did:
                counts[rule]["tp"] += 1
            elif not should and did:
                counts[rule]["fp"] += 1
            elif should and not did:
                counts[rule]["fn"] += 1
            else:
                counts[rule]["tn"] += 1

        missed = case.expected_rules - predicted
        spurious = predicted - case.expected_rules
        detail = ""
        if missed:
            detail += f"missed {sorted(missed)}; "
        if spurious:
            detail += f"spurious {sorted(spurious)}"
        results.append(
            CaseResult(name=case.name, passed=not (missed or spurious), detail=detail.strip("; "))
        )

    scores = [
        RuleScore(
            rule=rule,
            true_positives=c["tp"],
            false_positives=c["fp"],
            false_negatives=c["fn"],
            true_negatives=c["tn"],
        )
        for rule, c in counts.items()
    ]

    # A rule with no positive case is untested, and a perfect score over rules
    # that never fired would be a false reassurance. Treated as a suite failure
    # rather than a footnote — the first version of this suite silently left
    # weights_sum_to_one uncovered.
    uncovered = sorted(s.rule for s in scores if s.true_positives == 0)
    if uncovered:
        results.append(
            CaseResult(
                name="__rule_coverage__",
                passed=False,
                detail=f"no positive case exercises: {uncovered}",
            )
        )

    exact = sum(r.passed for r in results)
    total_fp = sum(s.false_positives for s in scores)
    total_fn = sum(s.false_negatives for s in scores)
    return EvalSuiteResult(
        suite="compliance",
        passed=exact == len(results),
        summary=(
            f"{exact}/{len(results)} cases labeled exactly; "
            f"{total_fp} false positive(s), {total_fn} false negative(s) across "
            f"{len(ALL_RULES)} rules, all with positive coverage"
            if not uncovered
            else f"{exact}/{len(results)} cases labeled exactly; "
            f"UNCOVERED RULES: {uncovered}"
        ),
        cases=results,
        rule_scores=scores,
    )
