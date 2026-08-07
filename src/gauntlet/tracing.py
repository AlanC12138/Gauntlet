"""Per-call agent traces.

## What this answers that nothing else did

The stored `RunReport` records what each agent *decided*. The stage events record
*when*. Neither records **why** — the prompt an agent saw and the text it
produced. That gap matters most at exactly the interesting moment: a loop-back is
only explicable if you can read the revision feedback the Allocation Agent was
handed on its second attempt, and compare its two proposals against it.

## Why not Langfuse

TECH_STACK originally targeted it. Building this out, a table won: the traces are
already going to a database this project runs, the schema is four columns of
substance, and adding a vendor account plus an SDK plus an egress dependency to
read your own prompts is a lot of surface for a UI. It also keeps the offline
posture the rest of the system has — every other component degrades gracefully
without network, and tracing should not be the exception.

## Context, not plumbing

Agents do not take a `run_id`. Threading one through three call signatures purely
for observability would put a logging concern into the domain code, so the run and
iteration travel in context variables that the worker and pipeline set. An agent
records a call; it does not know what it belongs to.

Recording is **best-effort and never raises**. A trace that fails to persist must
not fail a paid, minutes-long run — the same rule the progress bus follows.
"""

import contextvars
import logging
from collections.abc import Iterator
from contextlib import contextmanager

from gauntlet.models import LLMUsage

logger = logging.getLogger(__name__)

_RUN_ID: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "gauntlet_run_id", default=None
)
_ITERATION: contextvars.ContextVar[int] = contextvars.ContextVar(
    "gauntlet_iteration", default=0
)
# Off by default so importing the package never implies a database.
_ENABLED: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "gauntlet_tracing_enabled", default=False
)


@contextmanager
def trace_run(run_id: str | None, enabled: bool = True) -> Iterator[None]:
    """Attribute every agent call inside this block to `run_id`."""
    run_token = _RUN_ID.set(run_id)
    enabled_token = _ENABLED.set(enabled)
    try:
        yield
    finally:
        _RUN_ID.reset(run_token)
        _ENABLED.reset(enabled_token)


def set_iteration(index: int) -> None:
    """Stamp subsequent calls with this iteration number.

    Not a context manager: the pipeline's loop is sequential and each iteration
    simply supersedes the last, so scoping would add unwinding for nothing.
    `trace_run` bounds the whole thing.
    """
    _ITERATION.set(index)


def _first_text(response) -> str | None:
    """The response's text blocks, concatenated.

    With structured output these hold the serialized JSON the model actually
    emitted — `{"findings":[]}` from a clean screen, the full proposal from the
    Allocation Agent. That is deliberately kept rather than dismissed as a
    duplicate of `parsed_output`: it is the raw emission, so a truncated or
    malformed answer is visible here exactly when the parsed form is unavailable,
    which is when a trace is worth most.
    """
    try:
        parts = []
        for block in getattr(response, "content", []) or []:
            text = getattr(block, "text", None)
            if text:
                parts.append(text)
        return "\n".join(parts) or None
    except Exception:  # noqa: BLE001 - a trace must never break the caller
        return None


def record_call(
    agent: str,
    model: str,
    system: str | None,
    prompt: str,
    response,
    usage: LLMUsage,
    latency_ms: int | None = None,
) -> None:
    """Persist one call. Silent no-op when tracing is off or the store is down."""
    if not _ENABLED.get():
        return

    from sqlalchemy.exc import SQLAlchemyError

    from gauntlet.db import get_session
    from gauntlet.db.models import AgentCall

    try:
        with get_session() as session:
            session.add(
                AgentCall(
                    run_id=_RUN_ID.get(),
                    iteration=_ITERATION.get(),
                    agent=agent,
                    model=model,
                    system_prompt=system,
                    prompt=prompt,
                    response=_first_text(response),
                    input_tokens=usage.input_tokens,
                    output_tokens=usage.output_tokens,
                    cost_usd=usage.cost_usd,
                    latency_ms=latency_ms,
                    stop_reason=getattr(response, "stop_reason", None),
                )
            )
    except SQLAlchemyError as exc:
        # Deliberately swallowed. A run costs real money and several minutes;
        # losing one because a trace could not be written would be absurd.
        logger.warning("Trace not recorded for %s: %s", agent, exc)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Trace not recorded for %s: %s", agent, exc)
