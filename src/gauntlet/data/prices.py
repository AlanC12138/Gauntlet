"""Historical price data: yfinance fetch behind an append-only local cache.

The cache is stored in long format — one row per (ticker, date) with the
timestamp it was fetched — deliberately mirroring the Postgres table this
becomes in Phase 4:

    price_bars(ticker text, date date, adj_close double, fetched_at timestamptz,
               primary key (ticker, date))

Append-only is the point, not an optimization: a bar that has been observed is
never overwritten, so a backtest run months apart over the same window produces
the same numbers even if yfinance later revises its history.

Prices are dividend- and split-adjusted (`auto_adjust=True`). Over a 13-year
window that choice is large and uneven across this universe — see data/README.md.
"""

import datetime as dt
import logging

import pandas as pd
import yfinance as yf

from gauntlet.config import settings

logger = logging.getLogger(__name__)

CACHE_COLUMNS = ["ticker", "date", "adj_close", "fetched_at"]

# A ticker's cached span is bounded by *trading* days, but callers ask for
# calendar dates — a window starting Jan 1 can only ever cache from Jan 2. Without
# slack on both ends, every run would look under-covered and re-hit yfinance.
# Five days covers the longest run of consecutive market closures.
COVERAGE_SLACK = pd.Timedelta(days=5)


def _empty_cache() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ticker": pd.Series(dtype="string"),
            "date": pd.Series(dtype="datetime64[ns]"),
            "adj_close": pd.Series(dtype="float64"),
            "fetched_at": pd.Series(dtype="datetime64[ns]"),
        }
    )


def read_cache() -> pd.DataFrame:
    path = settings.price_cache_path
    if not path.exists():
        return _empty_cache()
    return pd.read_parquet(path)


def _append_cache(new_rows: pd.DataFrame) -> int:
    """Append rows whose (ticker, date) is not already cached. Returns rows written."""
    existing = read_cache()
    if not existing.empty:
        seen = set(zip(existing["ticker"], existing["date"], strict=True))
        mask = [
            (t, d) not in seen
            for t, d in zip(new_rows["ticker"], new_rows["date"], strict=True)
        ]
        new_rows = new_rows[mask]

    if new_rows.empty:
        return 0

    combined = pd.concat([existing, new_rows], ignore_index=True)
    combined = combined.sort_values(["ticker", "date"], ignore_index=True)
    settings.price_cache_path.parent.mkdir(parents=True, exist_ok=True)
    combined.to_parquet(settings.price_cache_path, index=False)
    return len(new_rows)


def _cached_coverage() -> dict[str, tuple[pd.Timestamp, pd.Timestamp]]:
    cache = read_cache()
    if cache.empty:
        return {}
    spans = cache.groupby("ticker")["date"].agg(["min", "max"])
    return {t: (row["min"], row["max"]) for t, row in spans.iterrows()}


def _download(tickers: list[str], start: dt.date, end: dt.date) -> pd.DataFrame:
    """Fetch adjusted closes from yfinance as a wide DataFrame (dates x tickers)."""
    # yfinance treats `end` as exclusive.
    raw = yf.download(
        tickers,
        start=start.isoformat(),
        end=(end + dt.timedelta(days=1)).isoformat(),
        auto_adjust=True,
        progress=False,
        group_by="column",
        threads=True,
    )
    if raw.empty:
        raise RuntimeError(f"yfinance returned no data for {tickers}")

    close = raw["Close"] if isinstance(raw.columns, pd.MultiIndex) else raw[["Close"]]
    if len(tickers) == 1 and list(close.columns) == ["Close"]:
        close.columns = tickers
    close.index = pd.to_datetime(close.index).tz_localize(None)
    return close


def ensure_cached(tickers: list[str], start: dt.date, end: dt.date) -> int:
    """Fetch any tickers whose cached span doesn't cover [start, end]. Returns rows added."""
    coverage = _cached_coverage()
    want_start, want_end = pd.Timestamp(start), pd.Timestamp(end)

    missing = [
        t
        for t in tickers
        if t not in coverage
        or coverage[t][0] > want_start + COVERAGE_SLACK
        or coverage[t][1] < want_end - COVERAGE_SLACK
    ]
    if not missing:
        return 0

    logger.info("Fetching %d ticker(s) from yfinance: %s", len(missing), ", ".join(missing))
    close = _download(missing, start, end)

    long = (
        close.reset_index(names="date")
        .melt(id_vars="date", var_name="ticker", value_name="adj_close")
        .dropna(subset=["adj_close"])
    )
    long["ticker"] = long["ticker"].astype("string")
    long["fetched_at"] = pd.Timestamp.now()
    written = _append_cache(long[CACHE_COLUMNS])
    logger.info("Cached %d new price bars", written)
    return written


def price_panel(tickers: list[str], start: dt.date, end: dt.date) -> pd.DataFrame:
    """Wide panel of adjusted closes (rows: trading days, columns: tickers).

    Fetches anything not already cached. Rows where any requested ticker is
    missing are dropped, so every column is complete over the returned index —
    the backtest engine relies on that.
    """
    ensure_cached(tickers, start, end)
    cache = read_cache()

    window = cache[
        cache["ticker"].isin(tickers)
        & (cache["date"] >= pd.Timestamp(start))
        & (cache["date"] <= pd.Timestamp(end))
    ]
    panel = window.pivot(index="date", columns="ticker", values="adj_close")

    absent = sorted(set(tickers) - set(panel.columns))
    if absent:
        raise RuntimeError(f"No cached prices for {absent} in {start}..{end}")

    panel = panel[tickers].dropna(how="any").sort_index()
    if panel.empty:
        raise RuntimeError(f"No overlapping trading days for {tickers} in {start}..{end}")

    panel.columns.name = None
    return panel
