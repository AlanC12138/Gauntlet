"""Historical price data: yfinance fetch behind an append-only Postgres cache.

Storage is long format — one row per (ticker, date) with the timestamp it was
fetched:

    price_bars(ticker text, date date, adj_close double, fetched_at timestamptz,
               primary key (ticker, date))

Phase 1 wrote exactly this shape into a parquet file, deliberately, so that
moving to Postgres in Phase 4 would be mechanical rather than a rewrite. The
public functions here are unchanged across that move; only the store beneath
them differs. See `gauntlet.db.prices` for why the file had to go — briefly, the
RQ worker is a separate process, so a local file is no longer a shared store.

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
from sqlalchemy.exc import SQLAlchemyError

from gauntlet.config import settings
from gauntlet.db import get_session
from gauntlet.db import prices as repo

logger = logging.getLogger(__name__)

CACHE_COLUMNS = ["ticker", "date", "adj_close", "fetched_at"]

# A ticker's cached span is bounded by *trading* days, but callers ask for
# calendar dates — a window starting Jan 1 can only ever cache from Jan 2. Without
# slack on both ends, every run would look under-covered and re-hit yfinance.
# Five days covers the longest run of consecutive market closures.
COVERAGE_SLACK = pd.Timedelta(days=5)


class PriceFetchDisabled(RuntimeError):
    """Data is missing and `GAUNTLET_OFFLINE` forbids fetching it.

    A sibling of `PriceStoreUnavailable` rather than the same class, because the
    causes differ and the message should say which: one means the database is
    down, this means the database is fine and the data simply is not there.
    Both are RuntimeError, so callers that already degrade on a cold cache --
    the eval harness, the reachability witness -- handle this unchanged.
    """


class PriceStoreUnavailable(RuntimeError):
    """The price store could not be reached.

    A RuntimeError subclass on purpose: callers that already degrade gracefully
    on a cold cache — the eval harness drops its real-data cases and runs
    synthetic-only — keep working without knowing a database is involved.
    """


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
    """Fetch any tickers whose stored span doesn't cover [start, end]. Returns rows added."""
    want_start, want_end = pd.Timestamp(start), pd.Timestamp(end)

    try:
        with get_session() as session:
            spans = repo.coverage(session, tickers)
    except SQLAlchemyError as exc:
        raise PriceStoreUnavailable(f"Cannot reach the price store: {exc}") from exc

    missing = [
        t
        for t in tickers
        if t not in spans
        or spans[t][0] > want_start + COVERAGE_SLACK
        or spans[t][1] < want_end - COVERAGE_SLACK
    ]
    if not missing:
        return 0

    if settings.offline:
        raise PriceFetchDisabled(
            f"{len(missing)} ticker(s) are not cached and GAUNTLET_OFFLINE is set: "
            f"{', '.join(missing[:5])}{'...' if len(missing) > 5 else ''}. "
            "Run `gauntlet fetch` or `gauntlet import-prices` to populate the cache."
        )

    logger.info("Fetching %d ticker(s) from yfinance: %s", len(missing), ", ".join(missing))
    close = _download(missing, start, end)

    long = (
        close.reset_index(names="date")
        .melt(id_vars="date", var_name="ticker", value_name="adj_close")
        .dropna(subset=["adj_close"])
    )
    long["ticker"] = long["ticker"].astype("string")
    long["fetched_at"] = pd.Timestamp.now()

    with get_session() as session:
        written = repo.insert_bars(session, long[CACHE_COLUMNS])
    logger.info("Cached %d new price bars", written)
    return written


def price_panel(tickers: list[str], start: dt.date, end: dt.date) -> pd.DataFrame:
    """Wide panel of adjusted closes (rows: trading days, columns: tickers).

    Fetches anything not already cached. Rows where any requested ticker is
    missing are dropped, so every column is complete over the returned index —
    the backtest engine relies on that.
    """
    ensure_cached(tickers, start, end)

    try:
        with get_session() as session:
            window = repo.read_window(session, tickers, start, end)
    except SQLAlchemyError as exc:
        raise PriceStoreUnavailable(f"Cannot reach the price store: {exc}") from exc

    if window.empty:
        raise RuntimeError(f"No cached prices for {sorted(tickers)} in {start}..{end}")

    panel = window.pivot(index="date", columns="ticker", values="adj_close")

    absent = sorted(set(tickers) - set(panel.columns))
    if absent:
        raise RuntimeError(f"No cached prices for {absent} in {start}..{end}")

    panel = panel[tickers].dropna(how="any").sort_index()
    if panel.empty:
        raise RuntimeError(f"No overlapping trading days for {tickers} in {start}..{end}")

    panel.columns.name = None
    return panel
