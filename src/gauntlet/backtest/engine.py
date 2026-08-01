"""Vectorized backtest engine.

Given target weights, a price panel, and a rebalance schedule, computes the
portfolio's historical equity curve. This is deliberately not backtrader or
zipline: the Verification Agent's need is narrow, and a transparent engine is
what lets the eval harness check it against hand-computed reference values.

## How weight drift is handled

Between rebalance dates the portfolio is *held*, so weights drift with relative
performance — a position that doubles while the rest is flat ends up carrying
more of the portfolio than its target. Modeling that correctly is the difference
between a real backtest and one that silently rebalances every day.

Within a rebalance period, each asset's growth since the period's start is
g_i(t) = prod(1 + r_i) over the period so far. With targets w_i set at the
period's start, the portfolio's value relative to that start is:

    V(t) = sum_i w_i * g_i(t)

which is exactly buy-and-hold drift — no daily renormalization. Periods are then
chained by the running product of their end values, which is what applies the
rebalance: each new period restarts from the target weights.

`rebalance="none"` makes the whole window a single period, i.e. pure buy-and-hold.

## Simplifications, stated plainly

  * No transaction costs, spreads, or slippage. Rebalancing is free here, so
    higher-frequency schedules look better than they would in practice.
  * No fractional-share or lot-size constraints — positions are continuous.
  * Rebalances execute at the close of the period's last trading day at that
    day's adjusted price.
  * Dividends are reinvested implicitly via `auto_adjust=True` price data.
"""

import datetime as dt

import numpy as np
import pandas as pd

from gauntlet.backtest.metrics import calendar_year_returns, compute_metrics, drawdown_series
from gauntlet.models import BacktestResult, RebalanceFrequency

_PERIOD_FREQ = {"monthly": "M", "quarterly": "Q", "annual": "Y"}


def _rebalance_periods(index: pd.DatetimeIndex, frequency: RebalanceFrequency) -> pd.Series:
    """Map each trading day to a contiguous integer period id."""
    if frequency == "none":
        codes = np.zeros(len(index), dtype=int)
    else:
        try:
            freq = _PERIOD_FREQ[frequency]
        except KeyError:
            raise ValueError(f"unknown rebalance frequency {frequency!r}") from None
        codes = pd.factorize(index.to_period(freq))[0]
    return pd.Series(codes, index=index, name="period")


def equity_curve(
    weights: dict[str, float],
    prices: pd.DataFrame,
    rebalance: RebalanceFrequency = "quarterly",
    initial_capital: float = 1_000_000.0,
) -> pd.Series:
    """Portfolio value per trading day, starting at `initial_capital`."""
    if prices.empty:
        raise ValueError("price panel is empty")

    missing = sorted(set(weights) - set(prices.columns))
    if missing:
        raise ValueError(f"no price data for {missing}")

    tickers = list(weights)
    target = pd.Series(weights, dtype="float64")[tickers]
    panel = prices[tickers]

    returns = panel.pct_change().iloc[1:]
    if returns.empty:
        raise ValueError("price panel has fewer than two trading days")

    period = _rebalance_periods(returns.index, rebalance)

    # Per-asset growth since the start of the current rebalance period.
    growth = (1.0 + returns).groupby(period).cumprod()

    # Portfolio value relative to the start of its period — this is where drift lives.
    relative = growth.mul(target, axis=1).sum(axis=1)

    # Chain periods: the value entering period k is the product of all prior period ends.
    period_end = relative.groupby(period).last()
    carry = period_end.shift(1).fillna(1.0).cumprod()

    curve = relative * period.map(carry)

    # Prepend the opening value — the first return is already day two.
    opening = pd.Series([1.0], index=[panel.index[0]])
    return pd.concat([opening, curve]) * initial_capital


def run_backtest(
    weights: dict[str, float],
    prices: pd.DataFrame,
    rebalance: RebalanceFrequency = "quarterly",
    initial_capital: float = 1_000_000.0,
    risk_free_rate: float = 0.0,
) -> BacktestResult:
    curve = equity_curve(weights, prices, rebalance, initial_capital)
    drawdowns = drawdown_series(curve)

    def _as_date(ts: pd.Timestamp) -> dt.date:
        return ts.to_pydatetime().date()

    return BacktestResult(
        start=_as_date(curve.index[0]),
        end=_as_date(curve.index[-1]),
        initial_capital=initial_capital,
        final_value=float(curve.iloc[-1]),
        rebalance=rebalance,
        metrics=compute_metrics(curve, risk_free_rate=risk_free_rate),
        calendar_year_returns=calendar_year_returns(curve),
        equity_curve=[(_as_date(ts), float(v)) for ts, v in curve.items()],
        drawdown_curve=[(_as_date(ts), float(v)) for ts, v in drawdowns.items()],
    )
