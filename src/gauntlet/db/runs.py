"""Run and mandate repository.

The columns lifted out of the report — approved, iterations, cost, tokens — are
the ones a list view or a cost dashboard filters and aggregates on. Everything
else stays in the JSONB `report`, because a `RunReport` carries two equity curves
of ~3,270 points each and normalising those into rows would be a great deal of
schema for data that is only ever read back whole.

`status` and `approved` are separate columns, and that separation is the point:
a run can finish cleanly and be unapproved. That is the governance loop doing its
job, not a failure, and collapsing the two would make the two outcomes
indistinguishable in the database.
"""

import datetime as dt

from sqlalchemy import select
from sqlalchemy.orm import Session

from gauntlet.db.models import MandateRecord, RunRecord
from gauntlet.models import Mandate, RunReport

# Job lifecycle, distinct from the verdict.
STATUS_QUEUED = "queued"
STATUS_RUNNING = "running"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"


def save_mandate(session: Session, mandate: Mandate) -> MandateRecord:
    record = MandateRecord(
        name=mandate.name,
        risk_profile=str(mandate.risk_profile),
        payload=mandate.model_dump(mode="json"),
    )
    session.add(record)
    session.flush()  # assigns the autoincrement id without ending the transaction
    return record


def create_run(session: Session, run_id: str, mandate: Mandate) -> RunRecord:
    """Record a queued run. Called before enqueueing, so a client that polls
    immediately finds the row rather than a 404 racing the worker."""
    mandate_record = save_mandate(session, mandate)
    record = RunRecord(id=run_id, mandate_id=mandate_record.id, status=STATUS_QUEUED)
    session.add(record)
    return record


def mark_running(session: Session, run_id: str) -> None:
    session.get(RunRecord, run_id).status = STATUS_RUNNING


def save_result(session: Session, run_id: str, report: RunReport) -> None:
    record = session.get(RunRecord, run_id)
    record.status = STATUS_COMPLETED
    record.approved = report.approved
    record.iterations = len(report.iterations)
    record.cost_usd = report.total_cost_usd
    record.total_tokens = report.total_tokens
    record.report = report.model_dump(mode="json")
    record.finished_at = dt.datetime.now(dt.UTC)


def mark_failed(session: Session, run_id: str, error: str) -> None:
    record = session.get(RunRecord, run_id)
    record.status = STATUS_FAILED
    # Truncated: an exception chain from a failed LLM call can run to kilobytes,
    # and the useful part is at the front.
    record.error = error[:4_000]
    record.finished_at = dt.datetime.now(dt.UTC)


def get_run(session: Session, run_id: str) -> RunRecord | None:
    return session.get(RunRecord, run_id)


def list_runs(session: Session, limit: int = 50) -> list[RunRecord]:
    stmt = select(RunRecord).order_by(RunRecord.created_at.desc()).limit(limit)
    return list(session.execute(stmt).scalars())
