"""Background execution of a pipeline run.

**What is queued, and why.** README and TECH_STACK originally justified the queue
by saying backtests are compute-heavy. Measured on this universe, that is simply
wrong: loading the 52-ticker panel takes ~113ms and backtesting 52 positions over
3,270 days takes ~31ms. Neither needs a worker.

The agent loop is the long pole. A three-iteration run is 10+ minutes of wall
clock, almost entirely Anthropic API latency, and has twice exceeded a ten-minute
command timeout during development. That does not belong on an HTTP request
thread — a browser, a proxy, and most load balancers will all give up first. So
the queued unit is the *whole pipeline run*, not the backtest inside it.

The job function takes a serialized mandate rather than a `Mandate` object.
Anything enqueued is pickled and handed to another process, so keeping the
payload to plain JSON means the queue does not silently depend on Pydantic class
identity matching between the API and the worker.
"""

import logging
import uuid

from redis import Redis
from rq import Queue

from gauntlet.config import settings
from gauntlet.models import Mandate

logger = logging.getLogger(__name__)


def get_redis() -> Redis:
    return Redis.from_url(settings.redis_url)


def get_queue() -> Queue:
    return Queue(
        settings.queue_name,
        connection=get_redis(),
        default_timeout=settings.job_timeout_seconds,
    )


def execute_run(run_id: str, mandate_payload: dict) -> str:
    """Worker entry point. Runs the pipeline and persists the result.

    Returns the run id rather than the report: the report is large, and RQ would
    otherwise keep a full copy in Redis alongside the copy already in Postgres.
    Callers read the result from the database.

    Failures are recorded and then re-raised. Recording gives the API something
    to show; re-raising lets RQ move the job to its failed registry instead of
    marking it finished, so a dead worker and a failed run stay distinguishable.
    """
    from gauntlet.db import get_session
    from gauntlet.db import runs as repo
    from gauntlet.pipeline import run_pipeline
    from gauntlet.progress import (
        STAGE_RUN,
        STATUS_COMPLETED,
        STATUS_FAILED,
        ProgressPublisher,
    )
    from gauntlet.tracing import trace_run

    mandate = Mandate.model_validate(mandate_payload)
    emit = ProgressPublisher(run_id)

    with get_session() as session:
        repo.mark_running(session, run_id)
    emit(STAGE_RUN, "started", mandate.name)

    try:
        # Tracing is enabled only for queued runs: the worker has a run row to
        # attribute calls to, and a database it already depends on.
        with trace_run(run_id):
            report = run_pipeline(mandate, on_event=emit)
    except Exception as exc:
        logger.exception("Run %s failed", run_id)
        with get_session() as session:
            repo.mark_failed(session, run_id, f"{type(exc).__name__}: {exc}")
        emit(STAGE_RUN, STATUS_FAILED, f"{type(exc).__name__}: {exc}")
        raise

    with get_session() as session:
        repo.save_result(session, run_id, report)
    # Terminal event: this is what closes the browser's stream. It is emitted
    # after the report is persisted, so a client that reacts by fetching the run
    # cannot beat the write.
    emit(
        STAGE_RUN,
        STATUS_COMPLETED,
        f"{'Approved' if report.approved else 'Not approved'} after "
        f"{len(report.iterations)} iteration(s)",
    )

    logger.info(
        "Run %s complete: approved=%s iterations=%d",
        run_id,
        report.approved,
        len(report.iterations),
    )
    return run_id


def enqueue_run(mandate: Mandate) -> str:
    """Persist a queued run, enqueue it, and return its id.

    The row is written before the job is enqueued so that a client polling
    immediately gets `queued` rather than a 404 racing the worker.
    """
    from gauntlet.db import get_session
    from gauntlet.db import runs as repo

    run_id = uuid.uuid4().hex

    with get_session() as session:
        repo.create_run(session, run_id, mandate)

    get_queue().enqueue(
        execute_run,
        run_id,
        mandate.model_dump(mode="json"),
        job_id=run_id,
        result_ttl=86_400,
    )
    logger.info("Enqueued run %s", run_id)
    return run_id
