"""Risk and return metrics.

Every formula is spelled out below because the Phase 3 eval harness checks these
against independently hand-computed reference values. A change here is a change
to the contract, not an implementation detail — if a definition moves, the
reference values move with it.

Given an equity curve E (portfolio value per trading day) and daily returns
r_t = E_t / E_{t-1} - 1, with a constant daily risk-free rate f = R_f / 252:

    total_return  = E_N / E_0 - 1
    years         = (date_N - date_0) in days / 365.25        [calendar, not trading, days]
    CAGR          = (E_N / E_0) ^ (1 / years) - 1
    ann_vol       = stdev(r, ddof=1) * sqrt(252)
    Sharpe        = mean(r - f) / stdev(r - f, ddof=1) * sqrt(252)
    downside_dev  = sqrt( mean( min(r - f, 0)^2 ) ) * sqrt(252)   [population mean, all obs]
    Sortino       = (mean(r - f) * 252) / downside_dev
    max_drawdown  = min( E_t / cummax(E)_t - 1 )                   [negative or zero]
    Calmar        = CAGR / |max_drawdown|

Notes on the choices that have more than one defensible convention:
  * 252 trading days per year for annualization; 365.25 calendar days for CAGR.
  * Sample standard deviation (ddof=1) for volatility and Sharpe.
  * Downside deviation divides by the total observation count, not just the
    count of negative observations — the standard Sortino definition.
  * Sharpe uses stdev of *excess* returns. Since f is constant this equals
    stdev(r), but the excess form is what the definition states.
"""

import numpy as np
import pandas as pd

from gauntlet.models import PerformanceMetrics

TRADING_DAYS_PER_YEAR = 252
DAYS_PER_YEAR = 365.25

# Below this, a standard deviation is numerically zero and the ratio it would
# divide is undefined. An exact `> 0` guard is not enough: a constant-return path
# gives a std of ~1e-16 rather than 0, which passes the guard and produces a
# Sharpe around 1e15. Found by the eval harness, not by inspection.
#
# Daily return standard deviations are of order 1e-3; 1e-12 is twelve orders
# below anything real, so nothing meaningful is suppressed.
#
# Reporting 0.0 for these is a *convention* for a degenerate case, not a
# measurement — a positive return at genuinely zero risk has an unbounded ratio,
# not a zero one. No such portfolio exists, but the number should be read as
# "undefined" rather than "bad".
NEAR_ZERO_STDEV = 1e-12


def drawdown_series(equity: pd.Series) -> pd.Series:
    """Fractional drawdown from the running peak. Zero at each new high, else negative."""
    return equity / equity.cummax() - 1.0


def compute_metrics(equity: pd.Series, risk_free_rate: float = 0.0) -> PerformanceMetrics:
    if len(equity) < 2:
        raise ValueError("equity curve needs at least two points")

    daily = equity.pct_change().dropna()
    span_days = (equity.index[-1] - equity.index[0]).days
    years = span_days / DAYS_PER_YEAR
    if years <= 0:
        raise ValueError("equity curve spans zero days")

    growth = equity.iloc[-1] / equity.iloc[0]
    total_return = growth - 1.0
    cagr = growth ** (1.0 / years) - 1.0

    ann_vol = float(daily.std(ddof=1) * np.sqrt(TRADING_DAYS_PER_YEAR))

    daily_rf = risk_free_rate / TRADING_DAYS_PER_YEAR
    excess = daily - daily_rf
    excess_std = float(excess.std(ddof=1))
    sharpe = (
        float(excess.mean() / excess_std * np.sqrt(TRADING_DAYS_PER_YEAR))
        if excess_std > NEAR_ZERO_STDEV
        else 0.0
    )

    downside = np.minimum(excess, 0.0)
    downside_dev = float(np.sqrt((downside**2).mean()) * np.sqrt(TRADING_DAYS_PER_YEAR))
    sortino = (
        float(excess.mean() * TRADING_DAYS_PER_YEAR / downside_dev)
        if downside_dev > NEAR_ZERO_STDEV
        else 0.0
    )

    max_dd = float(drawdown_series(equity).min())
    calmar = float(cagr / abs(max_dd)) if max_dd < 0 else 0.0

    return PerformanceMetrics(
        total_return=float(total_return),
        cagr=float(cagr),
        annualized_volatility=ann_vol,
        sharpe_ratio=sharpe,
        sortino_ratio=sortino,
        max_drawdown=max_dd,
        calmar_ratio=calmar,
        trading_days=len(equity),
        years=float(years),
    )


def calendar_year_returns(equity: pd.Series) -> dict[str, float]:
    """Return per calendar year. The first year is measured from the curve's
    starting value, so a mid-year start reports a partial-year return."""
    year_end = equity.resample("YE").last()
    previous = year_end.shift(1)
    previous.iloc[0] = equity.iloc[0]
    yearly = year_end / previous - 1.0
    return {str(ts.year): float(v) for ts, v in yearly.items()}
