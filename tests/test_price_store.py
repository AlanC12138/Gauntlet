"""Append-only semantics of the price store.

The guarantee under test is the one the whole backtest rests on: a bar that has
been observed is never overwritten, so re-running a backtest months later over
the same window produces the same numbers even if the vendor revised its history.
"""

import datetime as dt

import pandas as pd
import pytest

from gauntlet.db import get_session
from gauntlet.db import prices as repo


def _bars(rows: list[tuple[str, str, float]]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ticker": [r[0] for r in rows],
            "date": [pd.Timestamp(r[1]) for r in rows],
            "adj_close": [r[2] for r in rows],
            "fetched_at": [pd.Timestamp("2026-01-01")] * len(rows),
        }
    )


def test_insert_is_idempotent(clean_db):
    frame = _bars([("SPY", "2020-01-02", 100.0), ("SPY", "2020-01-03", 101.0)])

    with get_session() as session:
        first = repo.insert_bars(session, frame)
    with get_session() as session:
        second = repo.insert_bars(session, frame)
        total = repo.count_bars(session)

    assert (first, second, total) == (2, 0, 2)


def test_existing_bar_is_never_overwritten(clean_db):
    """A revised price for an already-stored (ticker, date) is dropped, not applied."""
    with get_session() as session:
        repo.insert_bars(session, _bars([("SPY", "2020-01-02", 100.0)]))

    with get_session() as session:
        written = repo.insert_bars(session, _bars([("SPY", "2020-01-02", 999.0)]))

    with get_session() as session:
        stored = repo.read_window(
            session, ["SPY"], dt.date(2020, 1, 1), dt.date(2020, 12, 31)
        )

    assert written == 0
    assert stored["adj_close"].tolist() == [100.0]


def test_partial_overlap_inserts_only_the_new_rows(clean_db):
    with get_session() as session:
        repo.insert_bars(session, _bars([("SPY", "2020-01-02", 100.0)]))
        written = repo.insert_bars(
            session,
            _bars([("SPY", "2020-01-02", 100.0), ("SPY", "2020-01-03", 101.0)]),
        )
        assert written == 1
        assert repo.count_bars(session) == 2


def test_read_window_is_inclusive_at_both_ends(clean_db):
    with get_session() as session:
        repo.insert_bars(
            session,
            _bars(
                [
                    ("SPY", "2020-01-01", 1.0),
                    ("SPY", "2020-01-02", 2.0),
                    ("SPY", "2020-01-03", 3.0),
                    ("SPY", "2020-01-04", 4.0),
                ]
            ),
        )
        window = repo.read_window(
            session, ["SPY"], dt.date(2020, 1, 2), dt.date(2020, 1, 3)
        )

    assert sorted(window["adj_close"].tolist()) == [2.0, 3.0]


def test_coverage_reports_stored_span(clean_db):
    with get_session() as session:
        repo.insert_bars(
            session,
            _bars(
                [
                    ("SPY", "2020-01-02", 1.0),
                    ("SPY", "2020-06-30", 2.0),
                    ("AGG", "2020-03-02", 3.0),
                ]
            ),
        )
        spans = repo.coverage(session)

    assert spans["SPY"] == (pd.Timestamp("2020-01-02"), pd.Timestamp("2020-06-30"))
    assert spans["AGG"] == (pd.Timestamp("2020-03-02"), pd.Timestamp("2020-03-02"))


def test_coverage_filters_to_requested_tickers(clean_db):
    with get_session() as session:
        repo.insert_bars(
            session, _bars([("SPY", "2020-01-02", 1.0), ("AGG", "2020-01-02", 2.0)])
        )
        assert set(repo.coverage(session, ["SPY"])) == {"SPY"}


def test_panel_never_calls_yfinance_when_coverage_is_satisfied(clean_db, monkeypatch):
    """The cache must actually prevent refetching — this regressed once already,
    when cached *trading* days were compared against requested *calendar* dates."""
    from gauntlet.data import prices

    def explode(*args, **kwargs):
        raise AssertionError("yfinance was called despite full coverage")

    monkeypatch.setattr(prices, "_download", explode)

    with get_session() as session:
        repo.insert_bars(
            session,
            _bars([("SPY", "2020-01-02", 100.0), ("SPY", "2020-01-03", 101.0)]),
        )

    panel = prices.price_panel(["SPY"], dt.date(2020, 1, 1), dt.date(2020, 1, 3))
    assert list(panel.columns) == ["SPY"]
    assert len(panel) == 2


def test_missing_ticker_raises_rather_than_returning_a_short_panel(clean_db, monkeypatch):
    from gauntlet.data import prices

    monkeypatch.setattr(prices, "ensure_cached", lambda *a, **k: 0)

    with get_session() as session:
        repo.insert_bars(session, _bars([("SPY", "2020-01-02", 100.0)]))

    with pytest.raises(RuntimeError, match="AGG"):
        prices.price_panel(["SPY", "AGG"], dt.date(2020, 1, 1), dt.date(2020, 1, 3))


def test_offline_mode_refuses_to_fetch(clean_db, monkeypatch):
    """CI relies on this: an empty cache must fail fast rather than pulling
    170k bars from a third party on every run."""
    import datetime as dt

    from gauntlet.config import settings
    from gauntlet.data import prices
    from gauntlet.data.prices import PriceFetchDisabled

    monkeypatch.setattr(settings, "offline", True)
    monkeypatch.setattr(
        prices, "_download", lambda *a, **k: pytest.fail("yfinance was called in offline mode")
    )

    with pytest.raises(PriceFetchDisabled, match="GAUNTLET_OFFLINE"):
        prices.ensure_cached(["SPY"], dt.date(2020, 1, 1), dt.date(2020, 1, 3))


def test_offline_mode_still_serves_cached_data(clean_db, monkeypatch):
    """Offline forbids *fetching*, not reading — otherwise it would be useless."""
    import datetime as dt

    from gauntlet.config import settings
    from gauntlet.data import prices

    monkeypatch.setattr(settings, "offline", True)
    with get_session() as session:
        repo.insert_bars(
            session, _bars([("SPY", "2020-01-02", 100.0), ("SPY", "2020-01-03", 101.0)])
        )

    panel = prices.price_panel(["SPY"], dt.date(2020, 1, 1), dt.date(2020, 1, 3))
    assert len(panel) == 2


def test_offline_errors_are_catchable_as_store_unavailable():
    """The eval harness and the reachability witness already degrade on
    RuntimeError; offline must not need them changed."""
    from gauntlet.data.prices import PriceFetchDisabled

    assert issubclass(PriceFetchDisabled, RuntimeError)
