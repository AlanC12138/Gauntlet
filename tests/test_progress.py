"""Stage events: publishing, replay, and the guarantee that they cannot break a run.

Uses fakeredis, so none of this needs a live Redis.
"""

import pytest

from gauntlet.progress import (
    STAGE_RUN,
    STATUS_COMPLETED,
    ProgressPublisher,
    is_terminal,
    stored_events,
)


@pytest.fixture
def redis():
    import fakeredis

    return fakeredis.FakeStrictRedis()


def test_events_are_stored_in_order_with_monotonic_seq(redis):
    """Ordering is load-bearing: `seq` is how a late subscriber discards the
    overlap between replayed and live events."""
    emit = ProgressPublisher("r1", connection=redis)
    emit("allocation", "started")
    emit("allocation", "finished", "20 positions", iteration=1)
    emit("compliance", "started")

    events = stored_events("r1", connection=redis)

    assert [e.seq for e in events] == [1, 2, 3]
    assert [e.stage for e in events] == ["allocation", "allocation", "compliance"]
    assert events[1].detail == "20 positions"
    assert events[1].iteration == 1


def test_runs_are_isolated_from_each_other(redis):
    ProgressPublisher("r1", connection=redis)("allocation", "started")
    ProgressPublisher("r2", connection=redis)("screening", "started")

    assert [e.stage for e in stored_events("r1", connection=redis)] == ["allocation"]
    assert [e.stage for e in stored_events("r2", connection=redis)] == ["screening"]


def test_events_expire_so_they_do_not_outlive_the_report(redis):
    emit = ProgressPublisher("r1", connection=redis)
    emit("allocation", "started")

    assert redis.ttl("gauntlet:run:r1:events") > 0


def test_a_broken_redis_does_not_fail_the_run(monkeypatch):
    """The guarantee that matters most here. A run costs real money and several
    minutes; losing one because a progress notification failed would be absurd."""
    from redis.exceptions import ConnectionError as RedisConnectionError

    class DeadRedis:
        def pipeline(self):
            raise RedisConnectionError("connection refused")

    emit = ProgressPublisher("r1", connection=DeadRedis())
    emit("allocation", "started")  # must not raise


def test_reading_progress_from_a_broken_redis_returns_empty_not_an_error():
    from redis.exceptions import ConnectionError as RedisConnectionError

    class DeadRedis:
        def lrange(self, *args):
            raise RedisConnectionError("connection refused")

    assert stored_events("r1", connection=DeadRedis()) == []


def test_terminal_event_is_recognised(redis):
    emit = ProgressPublisher("r1", connection=redis)
    emit("synthesis", "approved")
    emit(STAGE_RUN, STATUS_COMPLETED, "Approved after 1 iteration(s)")

    events = stored_events("r1", connection=redis)

    assert not is_terminal(events[0])
    assert is_terminal(events[1])


def test_stored_events_are_capped(redis):
    from gauntlet.progress import MAX_STORED_EVENTS

    emit = ProgressPublisher("r1", connection=redis)
    for _ in range(MAX_STORED_EVENTS + 25):
        emit("allocation", "started")

    events = stored_events("r1", connection=redis)

    assert len(events) == MAX_STORED_EVENTS
    # The cap must drop the *oldest*, keeping the newest — a client watching a
    # pathological loop cares about where it is now, not where it began.
    assert events[-1].seq == MAX_STORED_EVENTS + 25


def test_pipeline_emits_the_expected_stage_sequence(monkeypatch, sample_report):
    """The loop must emit stages in the order it executes them, since that
    ordering is the thing the live view exists to show."""
    from gauntlet import pipeline

    seen: list[tuple[str, str]] = []

    def record(stage, status, detail="", iteration=0):
        seen.append((stage, status))

    report = sample_report()
    monkeypatch.setattr(pipeline, "load_universe", lambda: None)
    monkeypatch.setattr(pipeline, "BM25Retriever", lambda *a, **k: object())
    monkeypatch.setattr(pipeline, "build_client", lambda: object())
    monkeypatch.setattr(
        pipeline,
        "propose_allocation",
        lambda *a, **k: (report.allocation, report.usage[0] if report.usage else _usage()),
    )
    monkeypatch.setattr(pipeline, "verify_allocation", lambda *a, **k: report.backtest)
    monkeypatch.setattr(pipeline, "check_compliance", lambda *a, **k: report.compliance)
    monkeypatch.setattr(
        pipeline, "screen_allocation", lambda *a, **k: (report.screening, _usage())
    )
    monkeypatch.setattr(pipeline, "synthesize", lambda *a, **k: (report.verdict, _usage()))

    pipeline.run_pipeline(report.mandate, on_event=record)

    assert seen == [
        ("iteration", "started"),
        ("allocation", "started"),
        ("allocation", "finished"),
        ("verification", "started"),
        ("verification", "finished"),
        ("compliance", "started"),
        ("compliance", "finished"),
        ("screening", "started"),
        ("screening", "finished"),
        ("synthesis", "started"),
        ("synthesis", "approved"),
    ]


def test_pipeline_runs_unchanged_without_an_event_sink(monkeypatch, sample_report):
    """Progress reporting is optional; omitting it must change nothing."""
    from gauntlet import pipeline

    report = sample_report()
    monkeypatch.setattr(pipeline, "load_universe", lambda: None)
    monkeypatch.setattr(pipeline, "BM25Retriever", lambda *a, **k: object())
    monkeypatch.setattr(pipeline, "build_client", lambda: object())
    monkeypatch.setattr(
        pipeline, "propose_allocation", lambda *a, **k: (report.allocation, _usage())
    )
    monkeypatch.setattr(pipeline, "verify_allocation", lambda *a, **k: report.backtest)
    monkeypatch.setattr(pipeline, "check_compliance", lambda *a, **k: report.compliance)
    monkeypatch.setattr(
        pipeline, "screen_allocation", lambda *a, **k: (report.screening, _usage())
    )
    monkeypatch.setattr(pipeline, "synthesize", lambda *a, **k: (report.verdict, _usage()))

    result = pipeline.run_pipeline(report.mandate)

    assert result.approved is True


def _usage():
    from gauntlet.models import LLMUsage

    return LLMUsage(
        agent="test", model="test", input_tokens=1, output_tokens=1
    )
