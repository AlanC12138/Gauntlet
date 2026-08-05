"""Queued-run lifecycle.

No LLM calls: `run_pipeline` is replaced throughout, because what is under test
is the persistence and failure handling around the pipeline, not the pipeline.
"""

import pytest

from gauntlet.db import get_session
from gauntlet.db import runs as repo
from gauntlet.mandates import get_mandate


@pytest.fixture
def fake_queue(monkeypatch):
    """RQ against fakeredis, so tests need no Redis of their own."""
    import fakeredis
    from rq import Queue

    from gauntlet import jobs

    connection = fakeredis.FakeStrictRedis()
    queue = Queue("test", connection=connection, is_async=False)
    monkeypatch.setattr(jobs, "get_queue", lambda: queue)
    return queue


def test_enqueue_writes_the_row_before_the_job_runs(clean_db, fake_queue, monkeypatch):
    """A client that polls immediately must find `queued`, not a 404."""
    from gauntlet import jobs

    seen: dict[str, str] = {}

    def capture(func, run_id, payload, **kwargs):
        with get_session() as session:
            seen["status"] = repo.get_run(session, run_id).status
        return None

    monkeypatch.setattr(fake_queue, "enqueue", capture)
    run_id = jobs.enqueue_run(get_mandate("balanced"))

    assert seen["status"] == repo.STATUS_QUEUED
    with get_session() as session:
        assert repo.get_run(session, run_id) is not None


def test_completed_run_persists_the_report_and_its_summary(
    clean_db, fake_queue, monkeypatch, sample_report
):
    from gauntlet import jobs, pipeline

    report = sample_report(approved=True)
    monkeypatch.setattr(pipeline, "run_pipeline", lambda mandate, **kw: report)

    run_id = jobs.enqueue_run(get_mandate("balanced"))

    with get_session() as session:
        record = repo.get_run(session, run_id)
        assert record.status == repo.STATUS_COMPLETED
        assert record.approved is True
        assert record.finished_at is not None
        assert record.report["backtest"]["final_value"] == 1_500_000.0


def test_unapproved_run_still_completes(clean_db, fake_queue, monkeypatch, sample_report):
    """The distinction the schema exists to preserve: a run that finishes cleanly
    with a negative verdict is a success of the governance loop, not a failure."""
    from gauntlet import jobs, pipeline

    monkeypatch.setattr(
        pipeline, "run_pipeline", lambda mandate, **kw: sample_report(approved=False)
    )
    run_id = jobs.enqueue_run(get_mandate("balanced"))

    with get_session() as session:
        record = repo.get_run(session, run_id)

    assert record.status == repo.STATUS_COMPLETED
    assert record.approved is False


def test_failure_is_recorded_and_reraised(clean_db, fake_queue, monkeypatch):
    """Recording gives the API something to show; re-raising lets RQ file the job
    as failed, so a dead worker and a failed run stay distinguishable."""
    from gauntlet import jobs, pipeline

    def boom(mandate, **kw):
        raise RuntimeError("upstream exploded")

    monkeypatch.setattr(pipeline, "run_pipeline", boom)

    run_id = jobs.enqueue_run(get_mandate("balanced"))
    job = fake_queue.fetch_job(run_id)
    assert job.is_failed

    with get_session() as session:
        record = repo.get_run(session, run_id)

    assert record.status == repo.STATUS_FAILED
    assert "upstream exploded" in record.error
    assert record.report is None


def test_error_text_is_truncated(clean_db):
    from gauntlet.db.models import RunRecord

    with get_session() as session:
        session.add(RunRecord(id="x", status=repo.STATUS_QUEUED))
    with get_session() as session:
        repo.mark_failed(session, "x", "e" * 10_000)
    with get_session() as session:
        assert len(repo.get_run(session, "x").error) == 4_000


def test_list_runs_is_newest_first(clean_db, fake_queue, monkeypatch, sample_report):
    from gauntlet import jobs, pipeline

    monkeypatch.setattr(pipeline, "run_pipeline", lambda mandate, **kw: sample_report())
    ids = [jobs.enqueue_run(get_mandate("balanced")) for _ in range(3)]

    with get_session() as session:
        listed = [r.id for r in repo.list_runs(session)]

    assert set(listed) == set(ids)
    assert len(listed) == 3
