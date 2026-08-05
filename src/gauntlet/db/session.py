"""Engine and session factory.

The engine is built lazily and cached. Building it at import time would mean
every `import gauntlet.db` needs a reachable database — which would break the
CLI's offline paths (`gauntlet eval`, `gauntlet backtest`) for no reason, since
those touch no tables. Nothing connects until a session is actually opened.

`pool_pre_ping` is on because the long pole here is an LLM call, not a query: a
worker can hold a connection across ten minutes of agent latency, by which time
Postgres or a container restart may have dropped it. Pre-ping costs a trivial
round trip and turns a stale-connection crash into a transparent reconnect.
"""

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from gauntlet.config import settings

_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def get_engine() -> Engine:
    """The process-wide engine, created on first use."""
    global _engine
    if _engine is None:
        _engine = create_engine(
            settings.database_url,
            pool_pre_ping=True,
            future=True,
        )
    return _engine


def _factory() -> sessionmaker[Session]:
    global _session_factory
    if _session_factory is None:
        # expire_on_commit=False so a record read inside `get_session` is still
        # usable after the block exits. Without it, touching any attribute of a
        # returned ORM object raises DetachedInstanceError.
        _session_factory = sessionmaker(bind=get_engine(), expire_on_commit=False)
    return _session_factory


@contextmanager
def get_session() -> Iterator[Session]:
    """Transactional scope: commits on success, rolls back on any exception."""
    session = _factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def reset_engine() -> None:
    """Drop the cached engine and factory.

    For tests that point `database_url` at a different database after the engine
    has already been built — otherwise the cache above would keep handing back a
    connection to the previous one.
    """
    global _engine, _session_factory
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _session_factory = None
