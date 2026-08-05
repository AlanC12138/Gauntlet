"""Price-bar repository.

The cache moved out of a parquet file for a reason that only appears once the
queue exists: with RQ the worker is a *separate process*, potentially a separate
container, so a file on local disk stops being a shared store. Two processes
appending to the same parquet also has no concurrency story — the write is
read-modify-write over the whole file.

Append-only is preserved, and Postgres enforces it better than the file version
did. `ON CONFLICT DO NOTHING` against the `(ticker, date)` primary key is a
single atomic statement: a bar that has been observed is never overwritten, even
under concurrent writers. The parquet path had to read the whole cache, diff it
in Python, and rewrite — correct single-threaded, racy otherwise.

Why never upsert: a backtest re-run months later must produce the same numbers
even if the vendor has since revised its history. That is a reproducibility
guarantee, not a performance choice.
"""

import datetime as dt

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from gauntlet.db.models import PriceBar

# Rows per INSERT. The initial parquet import is ~170k bars; sending that as one
# statement builds a multi-megabyte query, so it goes in chunks.
INSERT_CHUNK = 5_000


def insert_bars(session: Session, rows: pd.DataFrame) -> int:
    """Insert bars whose (ticker, date) is not already stored. Returns rows written.

    Expects the long format `data/prices.py` has written since Phase 1:
    columns `ticker, date, adj_close, fetched_at`.
    """
    if rows.empty:
        return 0

    records = [
        {
            "ticker": str(r.ticker),
            "date": pd.Timestamp(r.date).date(),
            "adj_close": float(r.adj_close),
            "fetched_at": pd.Timestamp(r.fetched_at).to_pydatetime(),
        }
        for r in rows.itertuples(index=False)
    ]

    written = 0
    for start in range(0, len(records), INSERT_CHUNK):
        chunk = records[start : start + INSERT_CHUNK]
        stmt = (
            insert(PriceBar)
            .values(chunk)
            .on_conflict_do_nothing(index_elements=["ticker", "date"])
            # RETURNING rather than `.rowcount`: with ON CONFLICT DO NOTHING the
            # driver reports -1 for a multi-VALUES insert, so counting the rows
            # actually returned is the only honest measure of what was new.
            .returning(PriceBar.ticker)
        )
        written += len(session.execute(stmt).all())
    return written


def coverage(session: Session, tickers: list[str] | None = None) -> dict[str, tuple[pd.Timestamp, pd.Timestamp]]:
    """Stored span per ticker, as (first bar, last bar)."""
    stmt = select(
        PriceBar.ticker, func.min(PriceBar.date), func.max(PriceBar.date)
    ).group_by(PriceBar.ticker)
    if tickers is not None:
        stmt = stmt.where(PriceBar.ticker.in_(tickers))

    return {
        ticker: (pd.Timestamp(lo), pd.Timestamp(hi))
        for ticker, lo, hi in session.execute(stmt)
    }


def read_window(
    session: Session, tickers: list[str], start: dt.date, end: dt.date
) -> pd.DataFrame:
    """Bars for these tickers over [start, end], long format.

    Filtering happens in SQL rather than in pandas — the parquet version had to
    load all 170k rows to answer a question about 52 tickers over one window.
    """
    stmt = (
        select(PriceBar.ticker, PriceBar.date, PriceBar.adj_close)
        .where(PriceBar.ticker.in_(tickers))
        .where(PriceBar.date >= start)
        .where(PriceBar.date <= end)
    )
    frame = pd.DataFrame(
        session.execute(stmt).all(), columns=["ticker", "date", "adj_close"]
    )
    if not frame.empty:
        frame["date"] = pd.to_datetime(frame["date"])
    return frame


def count_bars(session: Session) -> int:
    return session.execute(select(func.count()).select_from(PriceBar)).scalar_one()
