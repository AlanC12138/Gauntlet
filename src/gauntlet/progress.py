"""Live progress events for a queued run.

The run executes in the worker process; the browser is attached to the API
process. Nothing in Postgres bridges that gap usefully — writing a row per stage
would mean polling, and polling a 90-second run on a 2-second interval shows a
stale picture of a system whose whole point is the sequence of decisions. So the
worker publishes to Redis and the API streams.

**Replay plus live, not either alone.** A browser that connects 3 seconds into a
run would otherwise miss "Allocation Agent proposing" and open on a blank panel.
Every event is therefore appended to a capped list *and* published to a channel:
a subscriber reads the list for what it missed, then follows the channel. The
subscription is opened *before* the replay is read, and events carry a
monotonic `seq` so the overlap can be discarded — the reverse order would drop
anything published in between.

**Publishing never fails a run.** A run costs real money and several minutes of
API latency. Losing one because a progress notification could not be delivered
would be absurd, so every publish failure is swallowed with a warning. Progress
is a convenience; the RunReport in Postgres is the record.
"""

import datetime as dt
import logging
from collections.abc import AsyncIterator

from pydantic import BaseModel
from redis.exceptions import RedisError

from gauntlet.config import settings

logger = logging.getLogger(__name__)

# Matches RQ's result_ttl, so progress and job result expire together rather than
# leaving a run whose events outlived the report they describe.
EVENT_TTL_SECONDS = 86_400

# Bound on stored events per run. A three-iteration run emits ~40; this is slack
# for a pathological loop, not a tuned figure.
MAX_STORED_EVENTS = 500

# How often to emit an SSE comment when no events are flowing. Without it, an
# idle proxy will close a connection during the Allocation Agent's 60+ seconds
# of thinking, which looks exactly like a crashed run.
HEARTBEAT_SECONDS = 15.0

STAGE_RUN = "run"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"

# The events after which a stream has nothing further to deliver.
TERMINAL_STATUSES = {STATUS_COMPLETED, STATUS_FAILED}


class ProgressEvent(BaseModel):
    run_id: str
    seq: int
    stage: str
    status: str
    detail: str = ""
    iteration: int = 0
    at: dt.datetime


def _events_key(run_id: str) -> str:
    return f"gauntlet:run:{run_id}:events"


def _channel(run_id: str) -> str:
    return f"gauntlet:run:{run_id}:channel"


def is_terminal(event: ProgressEvent) -> bool:
    return event.stage == STAGE_RUN and event.status in TERMINAL_STATUSES


class ProgressPublisher:
    """Worker-side sink. Callable, so the pipeline needs no import from here."""

    def __init__(self, run_id: str, connection=None) -> None:
        self.run_id = run_id
        self._seq = 0
        if connection is None:
            from gauntlet.jobs import get_redis

            connection = get_redis()
        self._redis = connection

    def __call__(
        self, stage: str, status: str, detail: str = "", iteration: int = 0
    ) -> None:
        self._seq += 1
        event = ProgressEvent(
            run_id=self.run_id,
            seq=self._seq,
            stage=stage,
            status=status,
            detail=detail,
            iteration=iteration,
            at=dt.datetime.now(dt.UTC),
        )
        payload = event.model_dump_json()
        try:
            pipe = self._redis.pipeline()
            pipe.rpush(_events_key(self.run_id), payload)
            pipe.ltrim(_events_key(self.run_id), -MAX_STORED_EVENTS, -1)
            pipe.expire(_events_key(self.run_id), EVENT_TTL_SECONDS)
            pipe.publish(_channel(self.run_id), payload)
            pipe.execute()
        except RedisError as exc:
            # Deliberately swallowed. See the module docstring: a progress
            # notification is not worth failing a paid, minutes-long run over.
            logger.warning("Dropped progress event %s/%s: %s", stage, status, exc)


def stored_events(run_id: str, connection=None) -> list[ProgressEvent]:
    """Everything published for this run so far. Empty if Redis is unreachable."""
    if connection is None:
        from gauntlet.jobs import get_redis

        connection = get_redis()
    try:
        raw = connection.lrange(_events_key(run_id), 0, -1)
    except RedisError as exc:
        logger.warning("Cannot read progress for %s: %s", run_id, exc)
        return []
    return [ProgressEvent.model_validate_json(item) for item in raw]


def as_sse(event: ProgressEvent) -> str:
    """One SSE frame. `event:` lets the browser bind per-stage handlers."""
    return f"event: {event.stage}\ndata: {event.model_dump_json()}\n\n"


async def event_stream(run_id: str, connection=None) -> AsyncIterator[str]:
    """Replay what was missed, then follow live events until the run ends.

    Subscribes *before* replaying, so an event published during the replay is
    buffered rather than lost, and drops anything at or below the highest seq
    already sent.
    """
    import asyncio

    import redis.asyncio as aioredis

    if connection is None:
        connection = aioredis.from_url(settings.redis_url)

    pubsub = connection.pubsub(ignore_subscribe_messages=True)
    highest_seq = 0
    try:
        await pubsub.subscribe(_channel(run_id))

        raw = await connection.lrange(_events_key(run_id), 0, -1)
        for item in raw:
            event = ProgressEvent.model_validate_json(item)
            highest_seq = max(highest_seq, event.seq)
            yield as_sse(event)
            if is_terminal(event):
                return

        while True:
            message = await pubsub.get_message(timeout=HEARTBEAT_SECONDS)
            if message is None:
                yield ": keepalive\n\n"
                continue
            event = ProgressEvent.model_validate_json(message["data"])
            if event.seq <= highest_seq:
                continue  # already delivered during replay
            highest_seq = event.seq
            yield as_sse(event)
            if is_terminal(event):
                return
    except (RedisError, asyncio.CancelledError) as exc:
        logger.info("Progress stream for %s ended: %s", run_id, type(exc).__name__)
    finally:
        try:
            await pubsub.aclose()
        except Exception:  # noqa: BLE001 - teardown must not mask the real error
            pass
