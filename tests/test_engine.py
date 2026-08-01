"""Backtest engine correctness.

Expected values here are computed by hand from the definitions in
backtest/metrics.py, not by calling the code under test. This is the seed of the
Phase 3 backtest-math eval harness.
"""

import math

import pandas as pd
import pytest

from gauntlet.backtest.engine import equity_curve, run_backtest
from gauntlet.backtest.metrics import compute_metrics, drawdown_series

# Month-end dates so that "monthly" puts every return in its own period.
MONTH_ENDS = pd.to_datetime(["2020-01-31", "2020-02-29", "2020-03-31", "2020-04-30"])

# A compounds at exactly +10% per step; B is flat. Makes drift arithmetic exact.
PRICES = pd.DataFrame(
    {"A": [100.0, 110.0, 121.0, 133.1], "B": [100.0, 100.0, 100.0, 100.0]},
    index=MONTH_ENDS,
)


def test_single_asset_tracks_its_price():
    curve = equity_curve({"A": 1.0}, PRICES, rebalance="none", initial_capital=1_000.0)
    assert curve.tolist() == pytest.approx([1000.0, 1100.0, 1210.0, 1331.0])


def test_buy_and_hold_lets_weights_drift():
    # 50/50 held: V(t) = 0.5*g_A(t) + 0.5*g_B(t)
    #   t1: 0.5*1.100 + 0.5*1 = 1.0500
    #   t2: 0.5*1.210 + 0.5*1 = 1.1050
    #   t3: 0.5*1.331 + 0.5*1 = 1.1655
    curve = equity_curve(
        {"A": 0.5, "B": 0.5}, PRICES, rebalance="none", initial_capital=1_000.0
    )
    assert curve.tolist() == pytest.approx([1000.0, 1050.0, 1105.0, 1165.5])


def test_rebalancing_resets_to_target_weights():
    # Each step is its own month, so weights reset to 50/50 every step and the
    # portfolio compounds at a flat 1.05: 1.05, 1.05^2, 1.05^3.
    curve = equity_curve(
        {"A": 0.5, "B": 0.5}, PRICES, rebalance="monthly", initial_capital=1_000.0
    )
    assert curve.tolist() == pytest.approx([1000.0, 1050.0, 1102.5, 1157.625])


def test_rebalancing_differs_from_buy_and_hold():
    """Guards against the classic bug of renormalizing weights every day, which
    would make the two schedules identical."""
    held = equity_curve({"A": 0.5, "B": 0.5}, PRICES, rebalance="none")
    rebalanced = equity_curve({"A": 0.5, "B": 0.5}, PRICES, rebalance="monthly")
    assert held.iloc[-1] != pytest.approx(rebalanced.iloc[-1])


def test_quarterly_periods_group_within_a_quarter():
    # Jan/Feb/Mar/Apr: the first three fall in Q1, so only the Apr step opens a
    # new period. Q1 behaves as buy-and-hold: 1.05, 1.105.
    # Q2 restarts at target weights from 1.105: 1.105 * 1.05 = 1.16025.
    curve = equity_curve(
        {"A": 0.5, "B": 0.5}, PRICES, rebalance="quarterly", initial_capital=1_000.0
    )
    assert curve.tolist() == pytest.approx([1000.0, 1050.0, 1105.0, 1160.25])


def test_weights_must_have_prices():
    with pytest.raises(ValueError, match="no price data"):
        equity_curve({"A": 0.5, "ZZZZ": 0.5}, PRICES)


def test_unknown_rebalance_frequency_rejected():
    with pytest.raises(ValueError, match="unknown rebalance frequency"):
        equity_curve({"A": 1.0}, PRICES, rebalance="weekly")


def test_drawdown_measures_from_running_peak():
    equity = pd.Series(
        [100.0, 120.0, 90.0, 150.0],
        index=pd.to_datetime(["2021-01-01", "2021-01-02", "2021-01-03", "2021-01-04"]),
    )
    dd = drawdown_series(equity)
    # Peak 120 -> trough 90 is -25%; new highs sit at 0.
    assert dd.tolist() == pytest.approx([0.0, 0.0, -0.25, 0.0])
    assert compute_metrics(equity).max_drawdown == pytest.approx(-0.25)


def test_metrics_match_hand_computed_values():
    equity = pd.Series(
        [100.0, 110.0, 99.0, 118.8],
        index=pd.to_datetime(["2020-01-01", "2020-01-02", "2020-01-03", "2020-01-04"]),
    )
    m = compute_metrics(equity, risk_free_rate=0.0)

    daily = [0.10, -0.10, 0.20]
    mean = sum(daily) / 3
    variance = sum((r - mean) ** 2 for r in daily) / 2  # ddof=1
    stdev = math.sqrt(variance)
    years = 3 / 365.25  # 2020-01-01 -> 2020-01-04

    assert m.total_return == pytest.approx(0.188)
    assert m.cagr == pytest.approx(1.188 ** (1 / years) - 1)
    assert m.annualized_volatility == pytest.approx(stdev * math.sqrt(252))
    assert m.sharpe_ratio == pytest.approx(mean / stdev * math.sqrt(252))
    assert m.max_drawdown == pytest.approx(-0.10)
    assert m.trading_days == 4

    # Sortino: downside deviation uses the population mean over ALL observations.
    downside = math.sqrt(sum(min(r, 0.0) ** 2 for r in daily) / 3) * math.sqrt(252)
    assert m.sortino_ratio == pytest.approx(mean * 252 / downside)


def test_risk_free_rate_lowers_sharpe():
    equity = pd.Series(
        [100.0, 110.0, 99.0, 118.8],
        index=pd.to_datetime(["2020-01-01", "2020-01-02", "2020-01-03", "2020-01-04"]),
    )
    assert (
        compute_metrics(equity, risk_free_rate=0.05).sharpe_ratio
        < compute_metrics(equity, risk_free_rate=0.0).sharpe_ratio
    )


def test_calendar_year_returns_measure_from_curve_start():
    index = pd.to_datetime(["2020-06-30", "2020-12-31", "2021-12-31"])
    equity = pd.Series([100.0, 110.0, 132.0], index=index)
    result = run_backtest({"A": 1.0}, pd.DataFrame({"A": equity}), rebalance="none",
                          initial_capital=100.0)
    # 2020 is measured from the curve's opening value (a partial year), 2021 year-over-year.
    assert result.calendar_year_returns == pytest.approx({"2020": 0.10, "2021": 0.20})


def test_run_backtest_reports_consistent_endpoints():
    result = run_backtest(
        {"A": 0.5, "B": 0.5}, PRICES, rebalance="none", initial_capital=1_000.0
    )
    assert result.final_value == pytest.approx(1165.5)
    assert result.metrics.total_return == pytest.approx(0.1655)
    assert result.start == MONTH_ENDS[0].date()
    assert result.end == MONTH_ENDS[-1].date()
    assert len(result.equity_curve) == len(result.drawdown_curve) == 4
