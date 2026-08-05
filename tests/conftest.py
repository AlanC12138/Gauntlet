import sys
from pathlib import Path

# Allows `pytest` to run without `poetry install` first.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.exc import SQLAlchemyError  # noqa: E402

TEST_DB = "gauntlet_test"


def _admin_url(url: str) -> str:
    """Same server, the default database — needed to CREATE DATABASE."""
    base, _, _ = url.rpartition("/")
    return f"{base}/postgres"


@pytest.fixture(scope="session")
def database():
    """A separate `gauntlet_test` database, created on demand.

    Separate rather than transactional-rollback against the dev database: the
    code under test opens its own sessions through `get_session()`, so there is
    no outer transaction to roll back into without threading a connection
    through every call site. A throwaway database is the honest version.

    Skips rather than fails when Postgres is unreachable, so the suite still runs
    offline — the same posture the eval harness takes with its real-data cases.
    """
    from gauntlet.config import settings
    from gauntlet.db.models import Base
    from gauntlet.db.session import get_engine, reset_engine

    original = settings.database_url
    admin = create_engine(
        _admin_url(original),
        isolation_level="AUTOCOMMIT",
        # Bounded, or "skip when Postgres is unavailable" becomes "hang for four
        # minutes, then skip".
        connect_args={"connect_timeout": settings.db_connect_timeout_seconds},
    )
    try:
        with admin.connect() as conn:
            exists = conn.execute(
                text("select 1 from pg_database where datname = :n"), {"n": TEST_DB}
            ).scalar()
            if not exists:
                conn.execute(text(f'create database "{TEST_DB}"'))
    except SQLAlchemyError as exc:
        pytest.skip(f"Postgres unavailable: {exc}")
    finally:
        admin.dispose()

    settings.database_url = f"{original.rpartition('/')[0]}/{TEST_DB}"
    reset_engine()
    Base.metadata.create_all(get_engine())

    yield get_engine()

    reset_engine()
    settings.database_url = original


def build_report(approved: bool = True):
    """A minimal RunReport with the shape a real run produces.

    Lives here rather than in a test module because both the job-lifecycle and
    API tests need it, and pytest test files are not importable as a package.
    """
    from gauntlet.mandates import get_mandate
    from gauntlet.models import (
        Allocation,
        BacktestResult,
        ComplianceReport,
        PerformanceMetrics,
        Position,
        RunReport,
        ScreeningReport,
        SynthesisVerdict,
    )

    mandate = get_mandate("balanced")
    return RunReport(
        mandate=mandate,
        allocation=Allocation(
            strategy_summary="test",
            positions=[
                Position(ticker="SPY", weight=0.6, rationale="equity"),
                Position(ticker="AGG", weight=0.4, rationale="bonds"),
            ],
        ),
        backtest=BacktestResult(
            start=mandate.start,
            end=mandate.end,
            initial_capital=mandate.initial_capital,
            final_value=1_500_000.0,
            rebalance=mandate.rebalance,
            metrics=PerformanceMetrics(
                total_return=0.5,
                cagr=0.09,
                annualized_volatility=0.1,
                sharpe_ratio=0.9,
                sortino_ratio=1.2,
                max_drawdown=-0.2,
                calmar_ratio=0.45,
                trading_days=3_270,
                years=13.0,
            ),
            calendar_year_returns={},
            equity_curve=[],
            drawdown_curve=[],
        ),
        sector_exposures={},
        compliance=ComplianceReport(
            passed=True, violations=[], checks_run=[], sector_exposures={}
        ),
        screening=ScreeningReport(passed=True, findings=[], tickers_screened=[]),
        verdict=SynthesisVerdict(
            decision="approve" if approved else "revise", reasoning="test"
        ),
        approved=approved,
    )


@pytest.fixture
def sample_report():
    return build_report


@pytest.fixture
def clean_db(database):
    """Empty every table before each test, so ordering cannot matter."""
    from gauntlet.db.models import Base

    with database.begin() as conn:
        tables = ", ".join(f'"{t.name}"' for t in reversed(Base.metadata.sorted_tables))
        conn.execute(text(f"truncate {tables} restart identity cascade"))
    return database
