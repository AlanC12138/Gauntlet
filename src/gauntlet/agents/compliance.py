"""Constraint-Compliance Agent — deterministic rules engine.

**No LLM runs here, deliberately.** Concentration limits and sector caps are
arithmetic over stated weights. An LLM judging them would be non-reproducible,
unauditable, and would undermine the premise that the proposal is *verified*
rather than re-assessed by another model. Every check below is a pure function
of (allocation, constraints, universe) — same inputs, same answer, forever.

Each check returns machine-readable `rule` identifiers so the Phase 3 eval
harness can score per-rule precision and recall against labeled violation cases.

Sector caps use the `sector_cap_treatment` field from universe.json rather than
inferring exemptions here: instruments marked `diversified` (broad equity) or
`non_equity` (bonds, commodities) carry no single-sector attribution and are
exempt by construction. See data/README.md for why that's a stated
simplification rather than look-through.
"""

import logging

from gauntlet.models import Allocation, BacktestResult, ComplianceReport, Mandate, Violation
from gauntlet.universe import (
    Universe,
    asset_class_exposures,
    load_universe,
    sector_exposures,
)

logger = logging.getLogger(__name__)

# Weights are normalized upstream, so anything beyond float noise is a real fault.
WEIGHT_SUM_TOLERANCE = 1e-6

# Checkable from the weights alone.
PRE_EXECUTION_CHECKS = [
    "weights_sum_to_one",
    "max_position_weight",
    "max_sector_weight",
    "holdings_count",
    "excluded_tickers",
    "excluded_sectors",
    "asset_class_bands",
]

# Require the backtest to have run. Reported in `checks_run` only when the result
# was actually supplied, so a skipped check never reads as a passed one.
POST_EXECUTION_CHECKS = ["max_drawdown_tolerance", "max_volatility"]


def _pct(x: float) -> str:
    return f"{x * 100:.2f}%"


def check_compliance(
    allocation: Allocation,
    mandate: Mandate,
    universe: Universe | None = None,
    backtest: BacktestResult | None = None,
) -> ComplianceReport:
    """Check an allocation against its mandate.

    `backtest` is optional so the weight-level rules stay checkable without
    executing anything — useful in tests and for a fast pre-flight. Omitting it
    when the mandate sets `max_drawdown_tolerance` skips that check and leaves it
    out of `checks_run`, so the report never implies a check that did not run.
    """
    universe = universe or load_universe()
    constraints = mandate.constraints
    weights = allocation.weights
    exposures = sector_exposures(weights)
    violations: list[Violation] = []

    # --- weights sum to 1.0 -------------------------------------------------
    total = sum(weights.values())
    if abs(total - 1.0) > WEIGHT_SUM_TOLERANCE:
        violations.append(
            Violation(
                rule="weights_sum_to_one",
                message=f"Weights sum to {_pct(total)}, must be 100.00%.",
                detail={"actual": f"{total:.9f}", "expected": "1.0"},
            )
        )

    # --- concentration limit ------------------------------------------------
    for ticker, weight in sorted(weights.items(), key=lambda kv: -kv[1]):
        if weight > constraints.max_position_weight + WEIGHT_SUM_TOLERANCE:
            violations.append(
                Violation(
                    rule="max_position_weight",
                    message=(
                        f"{ticker} is {_pct(weight)} of the portfolio, exceeding the "
                        f"{_pct(constraints.max_position_weight)} single-position limit."
                    ),
                    detail={
                        "ticker": ticker,
                        "weight": f"{weight:.6f}",
                        "limit": f"{constraints.max_position_weight:.6f}",
                    },
                )
            )

    # --- sector caps --------------------------------------------------------
    for sector, exposure in exposures.items():
        # `diversified` and `non_equity` are exempt by construction.
        if sector in {"diversified", "non_equity"}:
            continue
        if exposure > constraints.max_sector_weight + WEIGHT_SUM_TOLERANCE:
            violations.append(
                Violation(
                    rule="max_sector_weight",
                    message=(
                        f"{sector} exposure is {_pct(exposure)}, exceeding the "
                        f"{_pct(constraints.max_sector_weight)} sector cap."
                    ),
                    detail={
                        "sector": sector,
                        "exposure": f"{exposure:.6f}",
                        "limit": f"{constraints.max_sector_weight:.6f}",
                    },
                )
            )

    # --- holdings count -----------------------------------------------------
    count = len(weights)
    if count < constraints.min_holdings:
        violations.append(
            Violation(
                rule="holdings_count",
                message=(
                    f"Portfolio holds {count} positions, below the minimum of "
                    f"{constraints.min_holdings}."
                ),
                detail={"count": str(count), "minimum": str(constraints.min_holdings)},
            )
        )
    elif count > constraints.max_holdings:
        violations.append(
            Violation(
                rule="holdings_count",
                message=(
                    f"Portfolio holds {count} positions, above the maximum of "
                    f"{constraints.max_holdings}."
                ),
                detail={"count": str(count), "maximum": str(constraints.max_holdings)},
            )
        )

    # --- explicit ticker exclusions -----------------------------------------
    for ticker in constraints.excluded_tickers:
        if ticker in weights:
            violations.append(
                Violation(
                    rule="excluded_tickers",
                    message=f"{ticker} is excluded by the mandate but holds {_pct(weights[ticker])}.",
                    detail={"ticker": ticker, "weight": f"{weights[ticker]:.6f}"},
                )
            )

    # --- explicit sector exclusions -----------------------------------------
    for excluded in constraints.excluded_sectors:
        offenders = [
            t
            for t in weights
            if universe.by_ticker(t).sector == excluded
            and universe.by_ticker(t).sector_cap_treatment == "constituent"
        ]
        if offenders:
            violations.append(
                Violation(
                    rule="excluded_sectors",
                    message=(
                        f"Sector {excluded} is excluded by the mandate but the portfolio "
                        f"holds {', '.join(sorted(offenders))}."
                    ),
                    detail={
                        "sector": excluded,
                        "tickers": ", ".join(sorted(offenders)),
                        "exposure": f"{exposures.get(excluded, 0.0):.6f}",
                    },
                )
            )

    # --- asset-class bands ---------------------------------------------------
    if constraints.asset_class_bands:
        buckets = asset_class_exposures(weights)
        for bucket, band in constraints.asset_class_bands.items():
            actual = buckets.get(bucket, 0.0)
            if actual < band.min_weight - WEIGHT_SUM_TOLERANCE:
                violations.append(
                    Violation(
                        rule="asset_class_bands",
                        message=(
                            f"{bucket.replace('_', ' ')} exposure is {_pct(actual)}, below "
                            f"the mandate's {_pct(band.min_weight)} minimum."
                        ),
                        detail={"bucket": bucket, "actual": f"{actual:.6f}",
                                "minimum": f"{band.min_weight:.6f}"},
                    )
                )
            elif actual > band.max_weight + WEIGHT_SUM_TOLERANCE:
                violations.append(
                    Violation(
                        rule="asset_class_bands",
                        message=(
                            f"{bucket.replace('_', ' ')} exposure is {_pct(actual)}, above "
                            f"the mandate's {_pct(band.max_weight)} maximum."
                        ),
                        detail={"bucket": bucket, "actual": f"{actual:.6f}",
                                "maximum": f"{band.max_weight:.6f}"},
                    )
                )

    # --- post-execution: realized risk ---------------------------------------
    checks_run = list(PRE_EXECUTION_CHECKS)
    tolerance = constraints.max_drawdown_tolerance
    vol_ceiling = constraints.max_volatility
    wants_post = tolerance is not None or vol_ceiling is not None

    if wants_post and backtest is not None:
        checks_run += POST_EXECUTION_CHECKS

        if tolerance is not None:
            # metrics.max_drawdown is negative; tolerance is a positive magnitude.
            realized = backtest.metrics.max_drawdown
            if realized < -tolerance - WEIGHT_SUM_TOLERANCE:
                violations.append(
                    Violation(
                        rule="max_drawdown_tolerance",
                        message=(
                            f"The allocation fell {_pct(abs(realized))} peak-to-trough over "
                            f"{backtest.start}..{backtest.end}, exceeding the mandate's "
                            f"{_pct(tolerance)} drawdown tolerance."
                        ),
                        detail={
                            "realized_drawdown": f"{realized:.6f}",
                            "tolerance": f"{-tolerance:.6f}",
                            "window": f"{backtest.start}..{backtest.end}",
                        },
                    )
                )

        if vol_ceiling is not None:
            realized_vol = backtest.metrics.annualized_volatility
            if realized_vol > vol_ceiling + WEIGHT_SUM_TOLERANCE:
                violations.append(
                    Violation(
                        rule="max_volatility",
                        message=(
                            f"Annualized volatility was {_pct(realized_vol)} over "
                            f"{backtest.start}..{backtest.end}, exceeding the mandate's "
                            f"{_pct(vol_ceiling)} ceiling."
                        ),
                        detail={
                            "realized_volatility": f"{realized_vol:.6f}",
                            "ceiling": f"{vol_ceiling:.6f}",
                        },
                    )
                )
    elif wants_post:
        logger.warning(
            "Mandate sets post-execution risk limits but no backtest was supplied; "
            "skipping those checks"
        )

    report = ComplianceReport(
        passed=not violations,
        violations=violations,
        checks_run=checks_run,
        sector_exposures=exposures,
    )
    logger.info(
        "Compliance: %s (%d violation(s) across %d checks)",
        "PASS" if report.passed else "FAIL",
        len(violations),
        len(checks_run),
    )
    return report
