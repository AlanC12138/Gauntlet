"""Independent reference implementations for differential testing.

Everything here is written to be *obviously* correct rather than fast, and — more
importantly — to arrive at the answer by a different route than the production
code. Agreement between two implementations that share an approach proves very
little; agreement between two that don't is real evidence.

The headline example is `naive_equity_curve`. `backtest/engine.py` computes the
portfolio through return algebra: per-asset cumulative growth within a rebalance
period, weighted and summed, then chained across periods. The version here
simulates *shares* — buy a whole number of dollars' worth at each rebalance, hold
them, mark to market daily — which is how a broker statement is produced. Nothing
about the two shares a line of reasoning, so a vectorization bug in one will not
appear in the other.

No pandas, no numpy: plain Python loops over lists. Slow and transparent on
purpose.
"""

import math

TRADING_DAYS_PER_YEAR = 252
DAYS_PER_YEAR = 365.25

# Matches backtest/metrics.py. A guard on exact zero is unreliable in floating
# point; see the note there. Both implementations must share the convention or
# they disagree on degenerate paths for reasons that say nothing about either.
NEAR_ZERO_STDEV = 1e-12


def naive_periods(dates: list, frequency: str) -> list[int]:
    """Assign each date a rebalance-period id, without pandas' period machinery."""
    if frequency == "none":
        return [0] * len(dates)

    keys = []
    for d in dates:
        if frequency == "monthly":
            keys.append((d.year, d.month))
        elif frequency == "quarterly":
            keys.append((d.year, (d.month - 1) // 3))
        elif frequency == "annual":
            keys.append((d.year,))
        else:
            raise ValueError(f"unknown rebalance frequency {frequency!r}")

    seen: dict = {}
    out = []
    for k in keys:
        if k not in seen:
            seen[k] = len(seen)
        out.append(seen[k])
    return out


def naive_equity_curve(
    weights: dict[str, float],
    prices,
    frequency: str = "quarterly",
    initial_capital: float = 1_000_000.0,
) -> list[float]:
    """Share-based portfolio simulation.

    At each rebalance the whole portfolio is liquidated at that day's prices and
    re-bought at target weights; between rebalances the share counts are fixed
    and the value simply floats with prices. That is the definition of holding a
    portfolio, expressed directly.

    Rebalance timing matches the engine: weights are reset at the close of the
    day *before* the first return of a new period, so the first return of that
    period is earned on target weights.
    """
    tickers = list(weights)
    dates = list(prices.index)
    if len(dates) < 2:
        raise ValueError("need at least two price rows")

    # One period id per *return*, i.e. per date after the first.
    period_ids = naive_periods(dates[1:], frequency)

    # Index (into `dates`) of every day on which we rebalance. Day 0 is the
    # initial purchase; thereafter, the day before a period boundary.
    rebalance_at = {0}
    for i in range(1, len(period_ids)):
        if period_ids[i] != period_ids[i - 1]:
            rebalance_at.add(i)

    values: list[float] = []
    shares: dict[str, float] = {}
    for index, date in enumerate(dates):
        if index in rebalance_at:
            capital = (
                initial_capital
                if index == 0
                else sum(shares[t] * prices.at[date, t] for t in tickers)
            )
            shares = {t: capital * weights[t] / prices.at[date, t] for t in tickers}
        values.append(sum(shares[t] * prices.at[date, t] for t in tickers))
    return values


def naive_returns(equity: list[float]) -> list[float]:
    return [equity[i] / equity[i - 1] - 1.0 for i in range(1, len(equity))]


def naive_max_drawdown(equity: list[float]) -> float:
    """Worst decline from any earlier point to any later one — O(n^2).

    The engine uses a running-cumulative-maximum formulation. This checks every
    ordered pair outright, which is the definition the formulation is derived
    from rather than a restatement of it.
    """
    worst = 0.0
    for i in range(len(equity)):
        for j in range(i, len(equity)):
            worst = min(worst, equity[j] / equity[i] - 1.0)
    return worst


def naive_volatility(returns: list[float]) -> float:
    n = len(returns)
    mean = sum(returns) / n
    variance = sum((r - mean) ** 2 for r in returns) / (n - 1)
    return math.sqrt(variance) * math.sqrt(TRADING_DAYS_PER_YEAR)


def naive_sharpe(returns: list[float], risk_free_rate: float = 0.0) -> float:
    daily_rf = risk_free_rate / TRADING_DAYS_PER_YEAR
    excess = [r - daily_rf for r in returns]
    n = len(excess)
    mean = sum(excess) / n
    variance = sum((e - mean) ** 2 for e in excess) / (n - 1)
    stdev = math.sqrt(variance)
    if stdev <= NEAR_ZERO_STDEV:
        return 0.0
    return mean / stdev * math.sqrt(TRADING_DAYS_PER_YEAR)


def naive_sortino(returns: list[float], risk_free_rate: float = 0.0) -> float:
    daily_rf = risk_free_rate / TRADING_DAYS_PER_YEAR
    excess = [r - daily_rf for r in returns]
    n = len(excess)
    mean = sum(excess) / n
    downside = math.sqrt(sum(min(e, 0.0) ** 2 for e in excess) / n)
    if downside <= NEAR_ZERO_STDEV:
        return 0.0
    return mean * TRADING_DAYS_PER_YEAR / (downside * math.sqrt(TRADING_DAYS_PER_YEAR))


def naive_cagr(equity: list[float], days: int) -> float:
    years = days / DAYS_PER_YEAR
    return (equity[-1] / equity[0]) ** (1.0 / years) - 1.0
