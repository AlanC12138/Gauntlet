"""Eval suite 1 — backtest numeric correctness.

Two kinds of evidence, because they fail differently:

* **Analytic cases** — paths whose answers are derivable by hand (constant
  growth compounds exactly; a 120→90 decline is exactly −25%). These catch a
  wrong *formula*: both implementations could agree and both be wrong.

* **Differential cases** — the engine's output against `reference.py`, which
  reaches the same quantities by share-based simulation rather than return
  algebra. These catch a wrong *implementation*: an off-by-one in period
  assignment, a vectorization slip, a rebalance applied on the wrong day.

Real cached prices are included deliberately. Synthetic four-row fixtures never
exercise the paths that 3,270 rows of real data do — accumulated float error,
period boundaries landing on holidays, a rebalance date that is not a trading day.
"""

import datetime as dt
import logging
from dataclasses import dataclass, field

import pandas as pd

from gauntlet.backtest import run_backtest
from gauntlet.evals import reference
from gauntlet.evals.models import CaseResult, EvalSuiteResult, MetricAgreement

logger = logging.getLogger(__name__)

# Equity values run to millions, so equity is compared in relative terms.
# Ratio metrics are compared absolutely. Both are far tighter than any
# difference that would matter financially — the point is to detect logic
# errors, not to tolerate them.
RELATIVE_TOLERANCE = 1e-9
ABSOLUTE_TOLERANCE = 1e-9

# naive_max_drawdown is O(n^2); on 3,270 rows that is ~5M iterations. Real-data
# cases check it on a slice, while every O(n) metric uses the full history.
QUADRATIC_ROW_LIMIT = 750


@dataclass
class BacktestCase:
    name: str
    prices: pd.DataFrame
    weights: dict[str, float]
    rebalance: str = "none"
    initial_capital: float = 1_000_000.0
    # Hand-derived expectations. Only the keys present are checked.
    analytic: dict[str, float] = field(default_factory=dict)


def _dates(count: int, start: str = "2020-01-31", step_days: int = 31) -> pd.DatetimeIndex:
    begin = dt.date.fromisoformat(start)
    return pd.to_datetime([begin + dt.timedelta(days=step_days * i) for i in range(count)])


def synthetic_cases() -> list[BacktestCase]:
    cases: list[BacktestCase] = []

    # Constant compounding: every daily return is exactly +1%, so volatility and
    # drawdown are exactly zero and total return is exactly 1.01^20 - 1.
    constant = [100.0 * 1.01**i for i in range(21)]
    cases.append(
        BacktestCase(
            name="constant_growth_1pct_daily",
            prices=pd.DataFrame({"A": constant}, index=_dates(21, step_days=1)),
            weights={"A": 1.0},
            analytic={
                "total_return": 1.01**20 - 1.0,
                "annualized_volatility": 0.0,
                "max_drawdown": 0.0,
                "sharpe_ratio": 0.0,
            },
        )
    )

    # A single asset held outright must track its own price exactly.
    cases.append(
        BacktestCase(
            name="single_asset_tracks_price",
            prices=pd.DataFrame({"A": [100.0, 110.0, 121.0, 133.1]}, index=_dates(4)),
            weights={"A": 1.0},
            analytic={"total_return": 0.331, "max_drawdown": 0.0},
        )
    )

    # 50/50 held: V(t) = 0.5*g_A + 0.5*g_B. Weights drift with performance.
    drift = pd.DataFrame(
        {"A": [100.0, 110.0, 121.0, 133.1], "B": [100.0] * 4}, index=_dates(4)
    )
    cases.append(
        BacktestCase(
            name="two_asset_buy_and_hold_drift",
            prices=drift,
            weights={"A": 0.5, "B": 0.5},
            rebalance="none",
            analytic={"total_return": 0.1655},
        )
    )

    # Same path, monthly rebalance: weights reset each step, so the portfolio
    # compounds at a flat 1.05 rather than drifting into the winner.
    cases.append(
        BacktestCase(
            name="two_asset_monthly_rebalance",
            prices=drift,
            weights={"A": 0.5, "B": 0.5},
            rebalance="monthly",
            analytic={"total_return": 1.05**3 - 1.0},
        )
    )

    # Q1 behaves as buy-and-hold; only the April step opens a new period.
    cases.append(
        BacktestCase(
            name="quarterly_groups_within_a_quarter",
            prices=pd.DataFrame(
                {"A": [100.0, 110.0, 121.0, 133.1], "B": [100.0] * 4},
                index=pd.to_datetime(["2020-01-31", "2020-02-29", "2020-03-31", "2020-04-30"]),
            ),
            weights={"A": 0.5, "B": 0.5},
            rebalance="quarterly",
            analytic={"total_return": 0.16025},
        )
    )

    # Peak 120 -> trough 90 is exactly -25%, and the later high does not erase it.
    cases.append(
        BacktestCase(
            name="drawdown_measured_from_running_peak",
            prices=pd.DataFrame({"A": [100.0, 120.0, 90.0, 150.0]}, index=_dates(4)),
            weights={"A": 1.0},
            analytic={"max_drawdown": -0.25, "total_return": 0.5},
        )
    )

    # A portfolio that ends where it started still records its drawdown.
    cases.append(
        BacktestCase(
            name="round_trip_to_flat",
            prices=pd.DataFrame({"A": [100.0, 50.0, 100.0]}, index=_dates(3)),
            weights={"A": 1.0},
            analytic={"total_return": 0.0, "max_drawdown": -0.5},
        )
    )

    # A zero-variance path must not divide by zero in Sharpe or Sortino.
    cases.append(
        BacktestCase(
            name="flat_path_no_division_by_zero",
            prices=pd.DataFrame({"A": [100.0] * 6}, index=_dates(6)),
            weights={"A": 1.0},
            analytic={
                "total_return": 0.0,
                "annualized_volatility": 0.0,
                "sharpe_ratio": 0.0,
                "sortino_ratio": 0.0,
                "max_drawdown": 0.0,
            },
        )
    )

    return cases


def real_data_cases() -> list[BacktestCase]:
    """Cases over the cached universe. Skipped silently when the cache is cold."""
    from gauntlet.data.prices import price_panel
    from gauntlet.universe import load_universe

    universe = load_universe()
    window = universe.backtest_window

    specs = [
        ("real_60_40_quarterly", {"SPY": 0.6, "AGG": 0.4}, "quarterly"),
        ("real_60_40_buy_and_hold", {"SPY": 0.6, "AGG": 0.4}, "none"),
        ("real_defensive_mix_monthly", {"SHY": 0.3, "IEF": 0.2, "SPY": 0.3, "GLD": 0.2}, "monthly"),
        (
            "real_concentrated_equity_annual",
            {"AAPL": 0.2, "MSFT": 0.2, "NVDA": 0.2, "JPM": 0.2, "XOM": 0.2},
            "annual",
        ),
    ]

    cases = []
    for name, weights, rebalance in specs:
        panel = price_panel(list(weights), window.start, window.end)
        cases.append(
            BacktestCase(name=name, prices=panel, weights=weights, rebalance=rebalance)
        )
    return cases


def _compare(
    case: BacktestCase, errors: dict[str, list[float]]
) -> CaseResult:
    result = run_backtest(
        case.weights, case.prices, rebalance=case.rebalance, initial_capital=case.initial_capital
    )
    engine_equity = [v for _, v in result.equity_curve]

    problems: list[str] = []

    # --- differential: engine vs share-based simulation ----------------------
    ref_equity = reference.naive_equity_curve(
        case.weights, case.prices, case.rebalance, case.initial_capital
    )
    worst_relative = max(
        abs(a - b) / max(abs(b), 1e-12) for a, b in zip(engine_equity, ref_equity, strict=True)
    )
    errors["equity_curve"].append(worst_relative)
    if worst_relative > RELATIVE_TOLERANCE:
        problems.append(f"equity curve diverges from share simulation by {worst_relative:.3e}")

    ref_returns = reference.naive_returns(ref_equity)
    metrics = result.metrics
    checks = [
        ("annualized_volatility", metrics.annualized_volatility, reference.naive_volatility(ref_returns)),
        ("sharpe_ratio", metrics.sharpe_ratio, reference.naive_sharpe(ref_returns)),
        ("sortino_ratio", metrics.sortino_ratio, reference.naive_sortino(ref_returns)),
        (
            "cagr",
            metrics.cagr,
            reference.naive_cagr(ref_equity, (case.prices.index[-1] - case.prices.index[0]).days),
        ),
    ]
    if len(ref_equity) <= QUADRATIC_ROW_LIMIT:
        checks.append(
            ("max_drawdown", metrics.max_drawdown, reference.naive_max_drawdown(ref_equity))
        )
    else:
        sliced = ref_equity[:QUADRATIC_ROW_LIMIT]
        engine_sliced = min(
            e / max(sliced[:i + 1]) - 1.0 for i, e in enumerate(sliced)
        )
        checks.append(
            ("max_drawdown", engine_sliced, reference.naive_max_drawdown(sliced))
        )

    for metric, actual, expected in checks:
        error = abs(actual - expected)
        errors[metric].append(error)
        if error > ABSOLUTE_TOLERANCE:
            problems.append(f"{metric}: {actual:.12g} vs reference {expected:.12g} (Δ{error:.3e})")

    # --- analytic: hand-derived expectations ---------------------------------
    for metric, expected in case.analytic.items():
        actual = getattr(metrics, metric)
        error = abs(actual - expected)
        errors[f"analytic:{metric}"].append(error)
        if error > ABSOLUTE_TOLERANCE:
            problems.append(f"{metric}: {actual:.12g} vs hand-derived {expected:.12g}")

    return CaseResult(
        name=case.name,
        passed=not problems,
        detail="; ".join(problems),
    )


def run(include_real_data: bool = True) -> EvalSuiteResult:
    cases = synthetic_cases()
    if include_real_data:
        try:
            cases += real_data_cases()
        except (RuntimeError, OSError, KeyError) as exc:
            # Cold cache, no network, or a ticker missing from the universe.
            # Degrading to synthetic-only keeps the suite runnable offline;
            # tests/test_evals.py asserts the real cases are present, so this
            # cannot silently become the normal path.
            logger.warning("Skipping real-data cases: %s", exc)

    errors: dict[str, list[float]] = {}
    for key in (
        "equity_curve",
        "annualized_volatility",
        "sharpe_ratio",
        "sortino_ratio",
        "cagr",
        "max_drawdown",
    ):
        errors[key] = []
    from collections import defaultdict

    errors = defaultdict(list, errors)

    results = [_compare(case, errors) for case in cases]

    agreements = [
        MetricAgreement(
            metric=metric,
            cases=len(values),
            max_absolute_error=max(values) if values else 0.0,
            tolerance=RELATIVE_TOLERANCE if metric == "equity_curve" else ABSOLUTE_TOLERANCE,
        )
        for metric, values in sorted(errors.items())
        if values
    ]

    passed = all(r.passed for r in results)
    worst = max((a.max_absolute_error for a in agreements), default=0.0)
    return EvalSuiteResult(
        suite="backtest_math",
        passed=passed,
        summary=(
            f"{sum(r.passed for r in results)}/{len(results)} cases exact; "
            f"worst deviation across all metrics {worst:.2e}"
        ),
        cases=results,
        agreements=agreements,
    )
