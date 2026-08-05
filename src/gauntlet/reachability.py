"""Backtest-derived bounds for the post-execution constraints.

## What this can and cannot prove

`max_drawdown_tolerance` and `max_volatility` are constraints on what an
allocation *did*, so unlike every bound in `feasibility.py` they are not
decidable by arithmetic over the universe. Deciding them exactly would mean
minimizing realized drawdown over all portfolios satisfying the weight
constraints — a non-convex search over 13 years of prices, not an inequality.

So this deliberately answers a weaker question that is still useful, and is
careful about which direction it answers in:

* It **constructs one defensive portfolio** that satisfies every pre-execution
  constraint, backtests it, and reports what it achieved.
* A tolerance at or above that number is therefore **demonstrably reachable** —
  there is a witness, and the frontend can unlock the control with confidence.
* A tolerance below it is **not proven impossible**. A better portfolio may
  exist; this heuristic simply did not find one.

That asymmetry is the whole design. Reporting the witness as a hard floor would
be claiming an impossibility proof this cannot supply, and would lock a client
out of targets that are actually achievable. The note travels with the bound so
the distinction survives into the UI.

## The heuristic

Rank the available instruments by realized volatility over the backtest window,
then fill the portfolio from the calmest end, satisfying asset-class floors
first and respecting position caps, sector caps and band ceilings throughout.

Low volatility is a proxy for low drawdown, not a guarantee — the two diverge for
assets that fall steadily rather than sharply. It is chosen because it is
computable from the same cached panel the backtest already loads, and because
being a *proxy* is acceptable when the output is an existence proof: a worse
witness understates what is reachable, which errs toward telling a client
something is harder than it is rather than easier.
"""

import logging

import pandas as pd
from pydantic import BaseModel

from gauntlet.config import settings
from gauntlet.models import Constraints
from gauntlet.universe import ECONOMIC_BUCKET, Universe, load_universe

logger = logging.getLogger(__name__)

TRADING_DAYS = 252


class RiskWitness(BaseModel):
    """A constructed portfolio and the risk levels it actually achieved."""

    weights: dict[str, float]
    max_drawdown: float
    annualized_volatility: float
    holdings: int


def _instrument_volatility(prices: pd.DataFrame) -> pd.Series:
    returns = prices.pct_change().dropna(how="all")
    return returns.std() * (TRADING_DAYS**0.5)


def _defensive_weights(
    constraints: Constraints,
    universe: Universe,
    volatility: pd.Series,
) -> dict[str, float]:
    """Fill the portfolio from the calmest instruments, floors first.

    Two passes, and the order matters: asset-class floors are satisfied before
    any discretionary weight is placed, because a floor is a hard requirement
    while "add more of the calmest thing" is only a preference. Filling greedily
    first would routinely exhaust the position budget before reaching a bond
    floor and produce a portfolio that fails compliance.
    """
    from gauntlet.feasibility import available_instruments

    instruments = available_instruments(constraints, universe)
    by_ticker = {c.ticker: c for c in instruments}
    ranked = [t for t in volatility.sort_values().index if t in by_ticker]

    cap = constraints.max_position_weight
    bands = constraints.asset_class_bands

    weights: dict[str, float] = {}
    bucket_weight: dict[str, float] = {}
    sector_weight: dict[str, float] = {}

    def headroom(ticker: str) -> float:
        """How much more weight this instrument can take without a breach."""
        c = by_ticker[ticker]
        bucket = ECONOMIC_BUCKET[c.asset_class]
        room = cap - weights.get(ticker, 0.0)

        band = bands.get(bucket)
        if band is not None:
            room = min(room, band.max_weight - bucket_weight.get(bucket, 0.0))
        if c.sector_cap_treatment == "constituent":
            room = min(
                room, constraints.max_sector_weight - sector_weight.get(c.sector, 0.0)
            )
        return max(0.0, room)

    def place(ticker: str, amount: float) -> float:
        amount = min(amount, headroom(ticker))
        if amount <= 0:
            return 0.0
        if ticker not in weights and len(weights) >= constraints.max_holdings:
            return 0.0
        c = by_ticker[ticker]
        bucket = ECONOMIC_BUCKET[c.asset_class]
        weights[ticker] = weights.get(ticker, 0.0) + amount
        bucket_weight[bucket] = bucket_weight.get(bucket, 0.0) + amount
        if c.sector_cap_treatment == "constituent":
            sector_weight[c.sector] = sector_weight.get(c.sector, 0.0) + amount
        return amount

    # Pass 1: asset-class floors, calmest instrument in each bucket first.
    for bucket, band in bands.items():
        if band.min_weight <= 0:
            continue
        remaining = band.min_weight
        for ticker in ranked:
            if remaining <= 1e-12:
                break
            if ECONOMIC_BUCKET[by_ticker[ticker].asset_class] != bucket:
                continue
            remaining -= place(ticker, remaining)

    # Pass 2: fill the rest from the calmest instruments overall.
    for ticker in ranked:
        placed = sum(weights.values())
        if placed >= 1.0 - 1e-12:
            break
        place(ticker, 1.0 - placed)

    # Pass 3: reach min_holdings by shaving the largest positions. A portfolio
    # that satisfies every weight rule but holds too few names still fails
    # compliance, so the witness would be worthless.
    while len(weights) < constraints.min_holdings:
        candidate = next((t for t in ranked if t not in weights), None)
        if candidate is None:
            break
        donor = max(weights, key=lambda t: weights[t])
        share = min(weights[donor] / 2, cap)
        if share <= 1e-9:
            break
        weights[donor] -= share
        weights[candidate] = share

    total = sum(weights.values())
    if total <= 0:
        raise RuntimeError("Could not construct any portfolio from these constraints")
    return {t: w / total for t, w in weights.items() if w > 1e-9}


def defensive_witness(
    constraints: Constraints, universe: Universe | None = None
) -> RiskWitness:
    """Build a defensive portfolio and measure what it achieved.

    Raises if prices are unavailable — callers that must degrade should catch
    and omit the bound rather than reporting a fabricated one.
    """
    from gauntlet.backtest import run_backtest
    from gauntlet.data.prices import price_panel

    universe = universe or load_universe()
    window = universe.backtest_window

    from gauntlet.feasibility import available_instruments

    tickers = [c.ticker for c in available_instruments(constraints, universe)]
    prices = price_panel(tickers, window.start, window.end)

    weights = _defensive_weights(constraints, universe, _instrument_volatility(prices))
    result = run_backtest(
        weights,
        prices[list(weights)],
        rebalance="quarterly",
        risk_free_rate=settings.risk_free_rate,
    )

    return RiskWitness(
        weights=weights,
        # Reported as a positive magnitude to match `max_drawdown_tolerance`,
        # which is stated as "must not fall more than X".
        max_drawdown=abs(result.metrics.max_drawdown),
        annualized_volatility=result.metrics.annualized_volatility,
        holdings=len(weights),
    )


# Constructing a witness costs a panel load and a backtest — a few hundred
# milliseconds. The mandate builder re-checks on every keystroke, so results are
# memoized on the constraint fields that actually change the answer.
_CACHE: dict[tuple, RiskWitness] = {}


def _cache_key(constraints: Constraints) -> tuple:
    return (
        constraints.max_position_weight,
        constraints.max_sector_weight,
        constraints.min_holdings,
        constraints.max_holdings,
        tuple(sorted(constraints.excluded_tickers)),
        tuple(sorted(constraints.excluded_sectors)),
        tuple(
            sorted(
                (bucket, band.min_weight, band.max_weight)
                for bucket, band in constraints.asset_class_bands.items()
            )
        ),
    )


def cached_witness(
    constraints: Constraints, universe: Universe | None = None
) -> RiskWitness | None:
    """Memoized `defensive_witness`, returning None when it cannot be built.

    None means "no bound available", which callers render as an unbounded
    control — never as "unreachable". Failing to construct a witness says
    nothing about whether a target is achievable.
    """
    key = _cache_key(constraints)
    if key in _CACHE:
        return _CACHE[key]
    try:
        witness = defensive_witness(constraints, universe)
    except Exception as exc:  # noqa: BLE001 - any failure means "no bound"
        logger.warning("Could not construct a risk witness: %s", exc)
        return None
    _CACHE[key] = witness
    return witness
