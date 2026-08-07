"""Eval-result repository.

The point of a regression guard is noticing the run where a number moved, and a
number you only ever see on stdout cannot move visibly. One row per suite per
execution turns the harness from a snapshot into a trend: "compliance has been
26/26 for eleven runs" is a different claim from "compliance is 26/26 today".

Storing per *suite* rather than per report is deliberate. The two suites measure
different things — numeric agreement versus classification accuracy — and a
combined row would force one pass/fail over both, losing which one moved.

Persistence is best-effort. A regression guard that refuses to report because a
database is unreachable has made itself the outage, so `gauntlet eval` still
prints and still exits non-zero on failure with no database at all.
"""

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from gauntlet.db.models import EvalRunRecord
from gauntlet.evals.models import EvalReport

logger = logging.getLogger(__name__)


def save_report(session: Session, report: EvalReport) -> int:
    """Persist one row per suite. Returns rows written."""
    for suite in report.suites:
        session.add(
            EvalRunRecord(
                suite=suite.suite,
                passed=suite.passed,
                summary=suite.summary,
                payload=suite.model_dump(mode="json"),
            )
        )
    return len(report.suites)


def recent(session: Session, suite: str, limit: int = 10) -> list[EvalRunRecord]:
    stmt = (
        select(EvalRunRecord)
        .where(EvalRunRecord.suite == suite)
        .order_by(EvalRunRecord.created_at.desc())
        .limit(limit)
    )
    return list(session.execute(stmt).scalars())


def save_report_best_effort(report: EvalReport) -> int:
    """Persist if a database is reachable; log and carry on if not.

    Returns rows written, or 0 when persistence was skipped.
    """
    from sqlalchemy.exc import SQLAlchemyError

    from gauntlet.db import get_session

    try:
        with get_session() as session:
            return save_report(session, report)
    except SQLAlchemyError as exc:
        logger.warning("Eval results not persisted (store unreachable): %s", exc)
        return 0
