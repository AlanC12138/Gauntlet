"""The async run surface.

`POST /api/runs` no longer returns a report — it returns an id to poll. These
tests pin the contract a frontend will depend on, and in particular the one
distinction that is easy to collapse by accident: a completed run and an
approved run are different things.
"""

import pytest
from fastapi.testclient import TestClient

from gauntlet.api import app
from gauntlet.db import get_session
from gauntlet.db import runs as repo


@pytest.fixture
def client(clean_db, monkeypatch):
    """Enqueue is stubbed to record the run without dispatching to a worker."""
    from gauntlet import api

    def fake_enqueue(mandate):
        import uuid

        run_id = uuid.uuid4().hex
        with get_session() as session:
            repo.create_run(session, run_id, mandate)
        return run_id

    monkeypatch.setattr("gauntlet.jobs.enqueue_run", fake_enqueue)
    _ = api
    return TestClient(app)


def test_post_accepts_and_returns_an_id(client):
    response = client.post("/api/runs", json={"mandate_name": "balanced"})

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == repo.STATUS_QUEUED
    assert body["run_id"]


def test_unknown_mandate_is_404(client):
    response = client.post("/api/runs", json={"mandate_name": "nonexistent"})
    assert response.status_code == 404


def test_polling_a_queued_run_returns_status_without_a_report(client):
    run_id = client.post("/api/runs", json={"mandate_name": "balanced"}).json()["run_id"]

    body = client.get(f"/api/runs/{run_id}").json()

    assert body["status"] == repo.STATUS_QUEUED
    assert body["report"] is None
    assert body["approved"] is None


def test_unknown_run_is_404(client):
    assert client.get("/api/runs/does-not-exist").status_code == 404


def test_completed_but_unapproved_run_is_not_an_error(client, sample_report):
    """A negative verdict after three iterations is the governance loop working.
    It must surface as a completed run, not a failed one."""
    run_id = client.post("/api/runs", json={"mandate_name": "balanced"}).json()["run_id"]
    with get_session() as session:
        repo.save_result(session, run_id, sample_report(approved=False))

    body = client.get(f"/api/runs/{run_id}").json()

    assert body["status"] == repo.STATUS_COMPLETED
    assert body["approved"] is False
    assert body["error"] is None
    assert body["report"]["backtest"]["final_value"] == 1_500_000.0


def test_failed_run_reports_its_error(client):
    run_id = client.post("/api/runs", json={"mandate_name": "balanced"}).json()["run_id"]
    with get_session() as session:
        repo.mark_failed(session, run_id, "RuntimeError: upstream exploded")

    body = client.get(f"/api/runs/{run_id}").json()

    assert body["status"] == repo.STATUS_FAILED
    assert "upstream exploded" in body["error"]
    assert body["report"] is None


def test_list_returns_summaries_without_report_bulk(client):
    for _ in range(2):
        client.post("/api/runs", json={"mandate_name": "balanced"})

    body = client.get("/api/runs").json()

    assert len(body) == 2
    assert "report" not in body[0]


def test_queue_outage_is_503_not_500(clean_db, monkeypatch):
    """A down Redis is an infrastructure problem, and the status code should say
    so — a 500 would read as a bug in the pipeline."""
    from redis.exceptions import ConnectionError as RedisConnectionError

    def refuse(mandate):
        raise RedisConnectionError("connection refused")

    monkeypatch.setattr("gauntlet.jobs.enqueue_run", refuse)

    response = TestClient(app).post("/api/runs", json={"mandate_name": "balanced"})
    assert response.status_code == 503
