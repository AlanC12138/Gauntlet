"""Constraint feasibility — which mandates are satisfiable, and within what ranges.

## Why this exists

A mandate can be perfectly well-formed and still impossible. A 30% fixed-income
floor is unremarkable on its own; paired with an 8% position cap against a
universe holding two bond funds, it caps out at 16% and can never be met. That
exact combination shipped, and cost three live iterations and real money before
anyone noticed — the Allocation Agent kept "failing" at something arithmetically
unachievable.

Every relationship below is decidable **before any model is called** and before
any backtest runs. That is the whole point: infeasibility is arithmetic over the
universe, so it should fail in milliseconds at construction time rather than in
dollars at run time.

## Pre-execution only, deliberately

Constraints on *weights* (position caps, holdings counts, asset-class bands) are
checkable here exactly. Constraints on *results* (`max_drawdown_tolerance`,
`max_volatility`) are not: knowing whether a 12% drawdown is reachable means
solving an optimization over historical prices, not evaluating an inequality.
Those are reported as `None` ranges and left to Phase 5, which can afford to
backtest a most-defensive-feasible portfolio and quote its drawdown as an
*existence proof* — "we can construct 11%" — never as an impossibility proof.

## Single source of truth

This module backs both the compliance engine's sanity checks and the frontend's
range-locking. If a widget reimplemented the arithmetic it would drift from the
engine, which is the failure mode that produced the bug above.
"""

import math

from pydantic import BaseModel

from gauntlet.models import Constraints
from gauntlet.universe import ECONOMIC_BUCKET, Constituent, Universe, load_universe

# Absorbs float noise in comparisons like 20 * 0.05 >= 1.0.
EPSILON = 1e-9

# Post-execution constraints, reported but not bounded here. See module docstring.
DEFERRED_FIELDS = ("max_drawdown_tolerance", "max_volatility")


class FieldRange(BaseModel):
    """The legal range for one field, holding every other field fixed.

    `locked` means the field has no freedom left — lower and upper have met —
    which is what a frontend should render as a disabled control rather than a
    slider pinned at one value.
    """

    lower: float | None = None
    upper: float | None = None
    locked: bool = False
    note: str | None = None


class FeasibilityReport(BaseModel):
    feasible: bool
    problems: list[str] = []
    ranges: dict[str, FieldRange] = {}


def available_instruments(
    constraints: Constraints, universe: Universe | None = None
) -> list[Constituent]:
    """Universe minus the mandate's exclusions.

    Mirrors the compliance engine's exclusion logic exactly, including that a
    sector exclusion only reaches instruments whose weight counts toward that
    sector (`constituent` treatment) — a broad fund is not excluded by a sector
    ban, because its weight was never attributed to that sector to begin with.
    """
    universe = universe or load_universe()
    excluded_tickers = set(constraints.excluded_tickers)
    excluded_sectors = set(constraints.excluded_sectors)
    return [
        c
        for c in universe.constituents
        if c.ticker not in excluded_tickers
        and not (c.sector in excluded_sectors and c.sector_cap_treatment == "constituent")
    ]


def _bucket_counts(instruments: list[Constituent]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for c in instruments:
        bucket = ECONOMIC_BUCKET[c.asset_class]
        counts[bucket] = counts.get(bucket, 0) + 1
    return counts


def _bucket_capacity(bucket: str, constraints: Constraints, counts: dict[str, int]) -> float:
    """Most weight reachable in a bucket without breaching the position cap.

    Bounded by holdings as well as instrument count: you cannot use twenty bond
    funds if the mandate allows only fourteen positions in total.
    """
    usable = min(counts.get(bucket, 0), constraints.max_holdings)
    return usable * constraints.max_position_weight


def find_problems(constraints: Constraints, universe: Universe | None = None) -> list[str]:
    """Every reason this constraint set cannot be satisfied. Empty means feasible."""
    universe = universe or load_universe()
    instruments = available_instruments(constraints, universe)
    counts = _bucket_counts(instruments)
    problems: list[str] = []

    if constraints.min_holdings > constraints.max_holdings:
        problems.append(
            f"min_holdings ({constraints.min_holdings}) exceeds max_holdings "
            f"({constraints.max_holdings})."
        )

    if len(instruments) < constraints.min_holdings:
        problems.append(
            f"Only {len(instruments)} instrument(s) remain after exclusions, below the "
            f"{constraints.min_holdings}-holding minimum."
        )

    # Reaching 100% invested at all.
    reachable = constraints.max_holdings * constraints.max_position_weight
    if reachable < 1.0 - EPSILON:
        problems.append(
            f"{constraints.max_holdings} holdings capped at "
            f"{constraints.max_position_weight:.1%} reach only {reachable:.0%} of the "
            f"portfolio; 100% is unreachable."
        )

    bands = constraints.asset_class_bands
    if bands:
        floor_sum = sum(b.min_weight for b in bands.values())
        if floor_sum > 1.0 + EPSILON:
            problems.append(
                f"Asset-class minimums sum to {floor_sum:.0%}, above 100%."
            )

        ceiling_sum = sum(b.max_weight for b in bands.values())
        # Only binding when the bands partition the whole portfolio; a bucket with
        # no band declared can absorb the remainder.
        unbanded = set(counts) - set(bands)
        if not unbanded and ceiling_sum < 1.0 - EPSILON:
            problems.append(
                f"Asset-class maximums sum to {ceiling_sum:.0%}, below 100%, and every "
                f"bucket present is banded — the portfolio cannot be fully invested."
            )

        for bucket, band in bands.items():
            if band.min_weight > band.max_weight + EPSILON:
                problems.append(
                    f"{bucket}: minimum {band.min_weight:.0%} exceeds maximum "
                    f"{band.max_weight:.0%}."
                )

            capacity = _bucket_capacity(bucket, constraints, counts)
            if band.min_weight > capacity + EPSILON:
                problems.append(
                    f"{bucket}: floor of {band.min_weight:.0%} is unreachable — "
                    f"{counts.get(bucket, 0)} instrument(s) available, capped at "
                    f"{constraints.max_position_weight:.1%} each and "
                    f"{constraints.max_holdings} holdings, reach at most {capacity:.0%}."
                )

    # Sector caps only bind on sector-attributed equity; broad funds are exempt,
    # so they supply equity capacity a sector cap cannot restrict.
    equity_band = bands.get("equity")
    if equity_band is not None and equity_band.min_weight > 0:
        sectors = {c.sector for c in instruments if c.sector_cap_treatment == "constituent"}
        exempt_equity = sum(
            1
            for c in instruments
            if ECONOMIC_BUCKET[c.asset_class] == "equity"
            and c.sector_cap_treatment != "constituent"
        )
        capacity = len(sectors) * constraints.max_sector_weight + min(
            exempt_equity, constraints.max_holdings
        ) * constraints.max_position_weight
        if equity_band.min_weight > capacity + EPSILON:
            problems.append(
                f"equity: floor of {equity_band.min_weight:.0%} exceeds what "
                f"{len(sectors)} sector(s) capped at {constraints.max_sector_weight:.0%} "
                f"plus {exempt_equity} exempt broad fund(s) can supply ({capacity:.0%})."
            )

    return problems


def feasible_ranges(
    constraints: Constraints, universe: Universe | None = None
) -> dict[str, FieldRange]:
    """Legal range for each field, holding the others at their current values.

    Interdependent by nature: raising `max_holdings` widens the legal range for
    `max_position_weight`, which widens the reachable asset-class floors. A
    frontend should therefore re-request all ranges on every change rather than
    validating one field at a time.
    """
    universe = universe or load_universe()
    instruments = available_instruments(constraints, universe)
    counts = _bucket_counts(instruments)
    bands = constraints.asset_class_bands
    ranges: dict[str, FieldRange] = {}

    # --- max_position_weight -------------------------------------------------
    position_floors = [1.0 / constraints.max_holdings]
    driver = f"reaching 100% across {constraints.max_holdings} holdings"
    for bucket, band in bands.items():
        usable = min(counts.get(bucket, 0), constraints.max_holdings)
        if usable and band.min_weight > 0:
            needed = band.min_weight / usable
            if needed > max(position_floors):
                driver = f"the {bucket} floor of {band.min_weight:.0%}"
            position_floors.append(needed)
    ranges["max_position_weight"] = FieldRange(
        lower=max(position_floors),
        upper=1.0,
        note=f"Lower bound set by {driver}.",
    )

    # --- max_holdings --------------------------------------------------------
    needed_for_full = math.ceil(1.0 / constraints.max_position_weight - EPSILON)
    needed_for_bands = sum(
        math.ceil(b.min_weight / constraints.max_position_weight - EPSILON)
        for b in bands.values()
    )
    holdings_lower = max(constraints.min_holdings, needed_for_full, needed_for_bands)
    ranges["max_holdings"] = FieldRange(
        lower=holdings_lower,
        upper=len(instruments),
        locked=holdings_lower >= len(instruments),
        note=(
            f"At least {needed_for_full} position(s) are needed to reach 100% at a "
            f"{constraints.max_position_weight:.1%} cap"
            + (f"; {needed_for_bands} to satisfy the asset-class floors" if bands else "")
            + "."
        ),
    )

    # --- min_holdings --------------------------------------------------------
    min_upper = min(constraints.max_holdings, len(instruments))
    ranges["min_holdings"] = FieldRange(
        lower=1,
        upper=min_upper,
        locked=min_upper <= 1,
        note=f"{len(instruments)} instrument(s) available after exclusions.",
    )

    # --- max_sector_weight ---------------------------------------------------
    sectors = {c.sector for c in instruments if c.sector_cap_treatment == "constituent"}
    equity_band = bands.get("equity")
    sector_lower = 0.0
    if equity_band is not None and equity_band.min_weight > 0 and sectors:
        exempt_equity = sum(
            1
            for c in instruments
            if ECONOMIC_BUCKET[c.asset_class] == "equity"
            and c.sector_cap_treatment != "constituent"
        )
        exempt_capacity = min(exempt_equity, constraints.max_holdings) * (
            constraints.max_position_weight
        )
        shortfall = max(0.0, equity_band.min_weight - exempt_capacity)
        sector_lower = shortfall / len(sectors)
    ranges["max_sector_weight"] = FieldRange(
        lower=sector_lower,
        upper=1.0,
        note=(
            f"{len(sectors)} sector(s) available after exclusions."
            if sector_lower == 0
            else f"Lower bound set by the equity floor across {len(sectors)} sector(s)."
        ),
    )

    # --- asset-class bands ---------------------------------------------------
    for bucket, band in bands.items():
        capacity = _bucket_capacity(bucket, constraints, counts)
        other_floors = sum(b.min_weight for k, b in bands.items() if k != bucket)
        floor_upper = min(capacity, band.max_weight, max(0.0, 1.0 - other_floors))
        ranges[f"asset_class_bands.{bucket}.min_weight"] = FieldRange(
            lower=0.0,
            upper=floor_upper,
            locked=floor_upper <= EPSILON,
            note=(
                f"{counts.get(bucket, 0)} instrument(s) capped at "
                f"{constraints.max_position_weight:.1%} supply at most {capacity:.0%}."
            ),
        )
        ranges[f"asset_class_bands.{bucket}.max_weight"] = FieldRange(
            lower=band.min_weight,
            upper=1.0,
            note=None,
        )

    # --- deferred to Phase 5 -------------------------------------------------
    for field in DEFERRED_FIELDS:
        ranges[field] = FieldRange(
            lower=None,
            upper=None,
            note=(
                "Measured after execution. Whether a given level is reachable requires "
                "backtesting a candidate portfolio, not arithmetic — deferred to Phase 5."
            ),
        )

    return ranges


def assess(constraints: Constraints, universe: Universe | None = None) -> FeasibilityReport:
    universe = universe or load_universe()
    problems = find_problems(constraints, universe)
    return FeasibilityReport(
        feasible=not problems,
        problems=problems,
        ranges=feasible_ranges(constraints, universe),
    )
