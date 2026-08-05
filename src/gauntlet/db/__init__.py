"""Persistence layer — Postgres via SQLAlchemy 2.0.

Introduced in Phase 4, deliberately not before: the agent loop was worth proving
against files and a parquet cache before a schema was committed to.
"""

from gauntlet.db.session import get_session, get_engine

__all__ = ["get_engine", "get_session"]
