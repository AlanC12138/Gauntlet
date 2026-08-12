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

from sqlalchemy import delete, func, select
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


def list_runs(
    session: Session, limit: int = 50, include_archived: bool = False
) -> list[RunRecord]:
    """Recent runs, newest first. Archived ones are hidden unless asked for."""
    stmt = select(RunRecord)
    if not include_archived:
        stmt = stmt.where(RunRecord.archived_at.is_(None))
    stmt = stmt.order_by(RunRecord.created_at.desc()).limit(limit)
    return list(session.execute(stmt).scalars())


def archive(session: Session, run_id: str) -> bool:
    """Hide a run from listings without destroying it.

    The default way to tidy the demo history: a run cost real money and several
    minutes, and it stays fetchable by id — only the listing changes.
    """
    record = session.get(RunRecord, run_id)
    if record is None:
        return False
    record.archived_at = dt.datetime.now(dt.UTC)
    return True


def unarchive(session: Session, run_id: str) -> bool:
    record = session.get(RunRecord, run_id)
    if record is None:
        return False
    record.archived_at = None
    return True


def purge(session: Session, run_id: str) -> bool:
    """Delete a run permanently, with the rows that only exist because of it.

    Order matters and is not obvious. `agent_calls.run_id` references the run, so
    the traces go first or the delete is rejected. The mandate goes *after*,
    because `runs.mandate_id` points at it — and only if no other run shares it,
    since a mandate row is created per submission but that is a convention rather
    than a guarantee.

    Prices, the ESG corpus and eval results are untouched: none of them belong to
    a run.
    """
    from gauntlet.db.models import AgentCall, MandateRecord

    record = session.get(RunRecord, run_id)
    if record is None:
        return False

    mandate_id = record.mandate_id
    session.execute(delete(AgentCall).where(AgentCall.run_id == run_id))
    session.delete(record)
    session.flush()

    if mandate_id is not None:
        others = session.execute(
            select(func.count())
            .select_from(RunRecord)
            .where(RunRecord.mandate_id == mandate_id)
        ).scalar_one()
        if others == 0:
            mandate = session.get(MandateRecord, mandate_id)
            if mandate is not None:
                session.delete(mandate)
    return True
