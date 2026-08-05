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


@pytest.fixture(autouse=True)
def _auth_disabled(monkeypatch):
    """This module tests the run surface, not the gate in front of it.

    Auth is disabled throughout so these assertions describe request/response
    shapes rather than doubling as authentication tests — `test_auth.py` owns
    that, and splitting them keeps a 401 from masking a broken 404.
    """
    from gauntlet.config import settings

    monkeypatch.setattr(settings, "clerk_secret_key", None)


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


def test_an_inline_mandate_carries_edited_constraints_through(clean_db, monkeypatch):
    """What the mandate builder actually posts.

    A preset is sent by key so the server stays its source of truth, but an
    edited constraint set is sent as a full inline mandate — so the edit has to
    survive validation and reach the pipeline verbatim, not be silently replaced
    by the preset it started from.
    """
    import uuid

    from gauntlet.db import get_session
    from gauntlet.db import runs as repo
    from gauntlet.mandates import get_mandate

    captured = {}

    def fake_enqueue(mandate):
        captured["mandate"] = mandate
        run_id = uuid.uuid4().hex
        with get_session() as session:
            repo.create_run(session, run_id, mandate)
        return run_id

    monkeypatch.setattr("gauntlet.jobs.enqueue_run", fake_enqueue)

    base = get_mandate("balanced")
    edited = base.model_dump(mode="json")
    edited["constraints"]["max_position_weight"] = 0.05
    edited["constraints"]["max_holdings"] = 30
    edited["constraints"]["max_drawdown_tolerance"] = 0.12

    response = TestClient(app).post("/api/runs", json={"mandate": edited})

    assert response.status_code == 202
    sent = captured["mandate"].constraints
    assert sent.max_position_weight == 0.05
    assert sent.max_holdings == 30
    assert sent.max_drawdown_tolerance == 0.12
    # And it is persisted, so the run record reflects what was actually run
    # rather than the preset's defaults.
    with get_session() as session:
        record = repo.get_run(session, response.json()["run_id"])
        stored = record.mandate.payload["constraints"]
    assert stored["max_position_weight"] == 0.05


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


def test_queue_outage_is_503_not_500(monkeypatch):
    """A down Redis is an infrastructure problem, and the status code should say
    so — a 500 would read as a bug in the pipeline. 503 tells a client to retry;
    500 tells it to file a report.

    Deliberately does not take `clean_db`: an outage test that needs the very
    infrastructure it is simulating the loss of would be skipped exactly when it
    matters most.
    """
    from redis.exceptions import ConnectionError as RedisConnectionError

    def refuse(mandate):
        raise RedisConnectionError("connection refused")

    monkeypatch.setattr("gauntlet.jobs.enqueue_run", refuse)

    response = TestClient(app).post("/api/runs", json={"mandate_name": "balanced"})
    assert response.status_code == 503


def test_database_outage_is_also_503(monkeypatch):
    """The same reasoning as Redis, and the case that was actually broken: a down
    Postgres surfaced as an unhandled 500 until the handler was made app-wide."""
    from sqlalchemy.exc import OperationalError

    def refuse(mandate):
        raise OperationalError("select 1", {}, Exception("connection refused"))

    monkeypatch.setattr("gauntlet.jobs.enqueue_run", refuse)

    response = TestClient(app).post("/api/runs", json={"mandate_name": "balanced"})
    assert response.status_code == 503


def test_reads_survive_an_outage_as_503(monkeypatch):
    """Polling a run during an outage must not look like a missing run — a 404
    would tell a client the run never existed."""
    from sqlalchemy.exc import OperationalError

    def refuse(*args, **kwargs):
        raise OperationalError("select 1", {}, Exception("connection refused"))

    monkeypatch.setattr("gauntlet.db.runs.get_run", refuse)
    monkeypatch.setattr("gauntlet.db.runs.list_runs", refuse)

    client = TestClient(app)
    assert client.get("/api/runs/anything").status_code == 503
    assert client.get("/api/runs").status_code == 503


def test_routes_that_need_no_store_still_serve_during_an_outage(monkeypatch):
    """Health checks and static reference data must not go down with Postgres,
    or a load balancer will pull a node that is merely degraded."""
    from sqlalchemy.exc import OperationalError

    def refuse(*args, **kwargs):
        raise OperationalError("select 1", {}, Exception("connection refused"))

    monkeypatch.setattr("gauntlet.db.runs.list_runs", refuse)

    client = TestClient(app)
    assert client.get("/health").status_code == 200
    assert client.get("/api/universe").status_code == 200
    assert client.get("/api/mandates").status_code == 200
