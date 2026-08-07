"""Per-call agent traces.

The property that matters most is the one that is easy to get wrong: recording a
trace must never be able to fail a run. A run costs real money and several
minutes, and observability is not worth losing one over.
"""

import pytest

from gauntlet.db import get_session
from gauntlet.db import traces as repo
from gauntlet.tracing import record_call, set_iteration, trace_run
from gauntlet.usage import LLMUsage


class FakeResponse:
    """Shaped like the SDK's response: content blocks with `.text`."""

    def __init__(self, text: str = "some reasoning", stop_reason: str = "end_turn"):
        self.content = [type("Block", (), {"text": text})()]
        self.stop_reason = stop_reason


def _usage(agent: str = "Allocation Agent") -> LLMUsage:
    return LLMUsage(
        agent=agent, model="claude-opus-5", input_tokens=100, output_tokens=50, cost_usd=0.01
    )


def _record(run_id: str, agent: str = "Allocation Agent", iteration: int = 1, **kw):
    with trace_run(run_id):
        set_iteration(iteration)
        record_call(
            agent,
            "claude-opus-5",
            kw.get("system", "SYSTEM"),
            kw.get("prompt", "the prompt"),
            kw.get("response", FakeResponse()),
            _usage(agent),
            kw.get("latency_ms", 1234),
        )


@pytest.fixture
def run_row(clean_db):
    """A real run, since traces carry a foreign key to one."""
    from gauntlet.db import runs as run_repo
    from gauntlet.mandates import get_mandate

    with get_session() as session:
        run_repo.create_run(session, "run-1", get_mandate("balanced"))
    return "run-1"


def test_a_call_is_recorded_whole(run_row):
    _record(run_row, prompt="what the agent saw", response=FakeResponse("what it wrote"))

    with get_session() as session:
        calls = repo.for_run(session, run_row)

    assert len(calls) == 1
    call = calls[0]
    assert call.prompt == "what the agent saw"
    assert call.response == "what it wrote"
    assert call.agent == "Allocation Agent"
    assert (call.input_tokens, call.output_tokens) == (100, 50)
    assert call.latency_ms == 1234


def test_tracing_is_off_unless_a_run_scope_is_open():
    """Importing the package must never imply a database. The CLI's offline
    paths call agents too."""
    record_call(
        "Allocation Agent", "claude-opus-5", "SYSTEM", "prompt", FakeResponse(), _usage()
    )  # no trace_run — must be a silent no-op, not an error


def test_calls_are_ordered_by_iteration_then_time(run_row):
    """Reading a trace means following a decision forward — and a loop-back's
    second prompt only makes sense after the feedback that produced it."""
    _record(run_row, agent="Allocation Agent", iteration=1)
    _record(run_row, agent="Synthesis Agent", iteration=1)
    _record(run_row, agent="Allocation Agent", iteration=2)

    with get_session() as session:
        calls = repo.for_run(session, run_row)

    assert [(c.iteration, c.agent) for c in calls] == [
        (1, "Allocation Agent"),
        (1, "Synthesis Agent"),
        (2, "Allocation Agent"),
    ]


def test_a_truncated_response_still_records_its_stop_reason(run_row):
    """A truncation is exactly when the partial response is worth reading."""
    _record(run_row, response=FakeResponse("half an answ", stop_reason="max_tokens"))

    with get_session() as session:
        assert repo.for_run(session, run_row)[0].stop_reason == "max_tokens"


def test_a_dead_store_does_not_fail_the_run(monkeypatch):
    """The guarantee that matters. Same rule the progress bus follows."""
    from sqlalchemy.exc import OperationalError

    def refuse():
        raise OperationalError("select 1", {}, Exception("connection refused"))

    monkeypatch.setattr("gauntlet.db.get_session", refuse)

    with trace_run("run-1"):
        record_call(
            "Allocation Agent", "claude-opus-5", "SYS", "prompt", FakeResponse(), _usage()
        )  # must not raise


def test_a_malformed_response_object_does_not_fail_the_run(run_row):
    """Defensive on purpose: the trace reads attributes off an SDK object it does
    not own, and an SDK shape change must not take a run down with it."""

    class Weird:
        pass

    _record(run_row, response=Weird())

    with get_session() as session:
        assert repo.for_run(session, run_row)[0].response is None


def test_summary_aggregates_without_shipping_prompts(run_row):
    _record(run_row, agent="Allocation Agent")
    _record(run_row, agent="Screening Agent")

    with get_session() as session:
        summary = repo.summary_for_run(session, run_row)

    assert summary["calls"] == 2
    assert summary["input_tokens"] == 200
    assert summary["output_tokens"] == 100
    assert summary["total_latency_ms"] == 2468
    assert "prompt" not in summary


def test_endpoint_returns_calls_in_order(run_row):
    from fastapi.testclient import TestClient

    from gauntlet.api import app

    _record(run_row, agent="Allocation Agent", iteration=1)
    _record(run_row, agent="Synthesis Agent", iteration=1)

    body = TestClient(app).get(f"/api/runs/{run_row}/traces").json()

    assert [c["agent"] for c in body] == ["Allocation Agent", "Synthesis Agent"]
    assert body[0]["prompt"] == "the prompt"


def test_endpoint_is_empty_not_404_for_an_untraced_run(clean_db):
    """A run from before tracing existed has no calls; that is not an error."""
    from fastapi.testclient import TestClient

    from gauntlet.api import app

    response = TestClient(app).get("/api/runs/nonexistent/traces")

    assert response.status_code == 200
    assert response.json() == []
