"""Reading agent traces back.

Ordered by iteration then id, which is the order the calls happened — the point
of reading a trace is following a decision forward, and especially reading a
loop-back's second prompt against the feedback that produced it.
"""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from gauntlet.db.models import AgentCall


def for_run(session: Session, run_id: str) -> list[AgentCall]:
    stmt = (
        select(AgentCall)
        .where(AgentCall.run_id == run_id)
        .order_by(AgentCall.iteration, AgentCall.id)
    )
    return list(session.execute(stmt).scalars())


def summary_for_run(session: Session, run_id: str) -> dict:
    """Aggregates worth showing without shipping kilobytes of prompt text."""
    stmt = (
        select(
            func.count(),
            func.sum(AgentCall.input_tokens),
            func.sum(AgentCall.output_tokens),
            func.sum(AgentCall.cost_usd),
            func.sum(AgentCall.latency_ms),
        )
        .select_from(AgentCall)
        .where(AgentCall.run_id == run_id)
    )
    calls, tokens_in, tokens_out, cost, latency = session.execute(stmt).one()
    return {
        "calls": calls or 0,
        "input_tokens": int(tokens_in or 0),
        "output_tokens": int(tokens_out or 0),
        "cost_usd": float(cost) if cost is not None else None,
        "total_latency_ms": int(latency or 0),
    }
