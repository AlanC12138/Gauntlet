"""Tidying the run history.

Archiving is the default because a run cost real money and several minutes;
destroying one to clean up a listing is a poor trade. Purging exists, but it has
to delete rows in an order the foreign keys dictate, which is the part worth
testing.
"""

import pytest

from gauntlet.db import get_session
from gauntlet.db import runs as repo
from gauntlet.mandates import get_mandate


@pytest.fixture
def two_runs(clean_db):
    with get_session() as session:
        repo.create_run(session, "run-a", get_mandate("balanced"))
        repo.create_run(session, "run-b", get_mandate("conservative"))
    return ["run-a", "run-b"]


def test_archiving_hides_from_listings_but_keeps_the_run(two_runs):
    with get_session() as session:
        repo.archive(session, "run-a")

    with get_session() as session:
        assert [r.id for r in repo.list_runs(session)] == ["run-b"]
        # Still fetchable by id, so an existing link keeps working.
        assert repo.get_run(session, "run-a") is not None


def test_archiving_is_reversible(two_runs):
    with get_session() as session:
        repo.archive(session, "run-a")
    with get_session() as session:
        repo.unarchive(session, "run-a")
    with get_session() as session:
        assert {r.id for r in repo.list_runs(session)} == {"run-a", "run-b"}


def test_archived_runs_are_listable_on_request(two_runs):
    with get_session() as session:
        repo.archive(session, "run-a")
    with get_session() as session:
        assert len(repo.list_runs(session, include_archived=True)) == 2


def test_purge_removes_the_run_and_its_traces(two_runs):
    """`agent_calls.run_id` references the run, so traces must go first or the
    delete is rejected outright."""
    from gauntlet.db import traces as trace_repo
    from gauntlet.db.models import AgentCall

    with get_session() as session:
        session.add(
            AgentCall(
                run_id="run-a", iteration=1, agent="Allocation Agent",
                model="claude-opus-5", prompt="p", input_tokens=1, output_tokens=1,
            )
        )

    with get_session() as session:
        assert repo.purge(session, "run-a") is True

    with get_session() as session:
        assert repo.get_run(session, "run-a") is None
        assert trace_repo.for_run(session, "run-a") == []
        assert [r.id for r in repo.list_runs(session)] == ["run-b"]


def test_purge_removes_the_orphaned_mandate(two_runs):
    """`runs.mandate_id` points at the mandate, so it can only go after the run —
    and only if nothing else references it."""
    from sqlalchemy import func, select

    from gauntlet.db.models import MandateRecord

    with get_session() as session:
        before = session.execute(select(func.count()).select_from(MandateRecord)).scalar_one()
        repo.purge(session, "run-a")
    with get_session() as session:
        after = session.execute(select(func.count()).select_from(MandateRecord)).scalar_one()

    assert (before, after) == (2, 1)


def test_purge_keeps_a_mandate_another_run_still_uses(clean_db):
    """A mandate row is created per submission by convention, not guarantee —
    so a shared one must survive."""
    from sqlalchemy import func, select

    from gauntlet.db.models import MandateRecord, RunRecord

    with get_session() as session:
        record = repo.create_run(session, "run-a", get_mandate("balanced"))
        session.flush()
        session.add(RunRecord(id="run-b", mandate_id=record.mandate_id, status="queued"))

    with get_session() as session:
        repo.purge(session, "run-a")
    with get_session() as session:
        assert session.execute(
            select(func.count()).select_from(MandateRecord)
        ).scalar_one() == 1


def test_operations_on_an_unknown_run_report_failure_rather_than_raising(clean_db):
    with get_session() as session:
        assert repo.archive(session, "nope") is False
        assert repo.unarchive(session, "nope") is False
        assert repo.purge(session, "nope") is False


def test_api_hides_archived_runs(two_runs):
    from fastapi.testclient import TestClient

    from gauntlet.api import app

    with get_session() as session:
        repo.archive(session, "run-a")

    client = TestClient(app)
    assert [r["run_id"] for r in client.get("/api/runs").json()] == ["run-b"]
    assert len(client.get("/api/runs?include_archived=true").json()) == 2
    # An archived run stays readable, so a saved link does not break.
    assert client.get("/api/runs/run-a").status_code == 200
