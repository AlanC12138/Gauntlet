"""FastAPI surface.

Runs are asynchronous as of Phase 4. `POST /api/runs` enqueues and returns a run
id immediately; the client polls `GET /api/runs/{id}`. This is not premature
scaling — a three-iteration run is 10+ minutes of Anthropic API latency, which no
browser or proxy will hold a connection open for. See `gauntlet.jobs` for why the
queued unit is the whole run rather than the backtest.

The SSE progress stream arrives in Phase 5, on top of the same run records.
"""

import datetime as dt
import logging

from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel
from redis.exceptions import RedisError

from gauntlet.db.models import RunRecord
from gauntlet.db.runs import STATUS_QUEUED
from gauntlet.feasibility import FeasibilityReport, assess
from gauntlet.mandates import DEMO_MANDATES, get_mandate
from gauntlet.models import Constraints, Mandate, RebalanceFrequency, RunReport
from gauntlet.universe import Universe, load_universe

logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(name)s: %(message)s")

app = FastAPI(
    title="Gauntlet",
    version="0.1.0",
    description="Multi-agent portfolio construction with execution-based verification.",
)


class RunRequest(BaseModel):
    """Either name a demo mandate or supply one inline."""

    mandate_name: str | None = "balanced"
    mandate: Mandate | None = None
    rebalance: RebalanceFrequency | None = None


class RunAccepted(BaseModel):
    run_id: str
    status: str


class RunSummary(BaseModel):
    """List-view shape: the columns lifted out of the report, none of its bulk."""

    run_id: str
    status: str
    approved: bool | None = None
    iterations: int | None = None
    cost_usd: float | None = None
    total_tokens: int | None = None
    created_at: dt.datetime
    finished_at: dt.datetime | None = None


class RunStatus(RunSummary):
    error: str | None = None
    report: RunReport | None = None


def _summarize(record: RunRecord) -> RunSummary:
    return RunSummary(
        run_id=record.id,
        status=record.status,
        approved=record.approved,
        iterations=record.iterations,
        cost_usd=record.cost_usd,
        total_tokens=record.total_tokens,
        created_at=record.created_at,
        finished_at=record.finished_at,
    )


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/universe", response_model=Universe)
def get_universe() -> Universe:
    return load_universe()


@app.get("/api/mandates")
def list_mandates() -> list[Mandate]:
    return [get_mandate(key) for key in DEMO_MANDATES]


@app.post("/api/constraints/feasibility", response_model=FeasibilityReport)
def check_constraint_feasibility(constraints: Constraints) -> FeasibilityReport:
    """Whether a constraint set is satisfiable, and the legal range of each field.

    Backs the Phase 5 mandate builder: a client adjusts a control, the widget
    re-posts the *whole* constraint set, and locks the ranges that come back.
    Posting the whole set rather than one field is not incidental — the fields
    are interdependent, so a per-field check would report stale bounds. Cheap
    enough to call on every keystroke: pure arithmetic, no model, no backtest.

    `max_drawdown_tolerance` and `max_volatility` come back with null bounds.
    They are measured after execution, so deciding reachability means backtesting
    a candidate portfolio rather than evaluating an inequality — Phase 5.
    """
    return assess(constraints)


@app.post("/api/runs", response_model=RunAccepted, status_code=status.HTTP_202_ACCEPTED)
def create_run(request: RunRequest) -> RunAccepted:
    """Queue a run. Returns immediately with an id to poll."""
    from gauntlet.jobs import enqueue_run

    if request.mandate is not None:
        mandate = request.mandate
    else:
        try:
            mandate = get_mandate(request.mandate_name or "balanced")
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from None

    if request.rebalance is not None:
        mandate = mandate.model_copy(update={"rebalance": request.rebalance})

    try:
        run_id = enqueue_run(mandate)
    except RedisError as exc:
        raise HTTPException(
            status_code=503, detail=f"Job queue unavailable: {exc}"
        ) from exc

    return RunAccepted(run_id=run_id, status=STATUS_QUEUED)


@app.get("/api/runs", response_model=list[RunSummary])
def list_runs(limit: int = 50) -> list[RunSummary]:
    from gauntlet.db import get_session
    from gauntlet.db import runs as repo

    with get_session() as session:
        return [_summarize(r) for r in repo.list_runs(session, limit=limit)]


@app.get("/api/runs/{run_id}", response_model=RunStatus)
def get_run(run_id: str) -> RunStatus:
    """Status, and the full report once the run has completed.

    `status` and `approved` are deliberately distinct. A completed run with
    `approved=false` is the governance loop refusing an allocation after three
    iterations — a successful run with a negative verdict, not a failure.
    """
    from gauntlet.db import get_session
    from gauntlet.db import runs as repo

    with get_session() as session:
        record = repo.get_run(session, run_id)
        if record is None:
            raise HTTPException(status_code=404, detail=f"No run {run_id!r}")

        report = RunReport.model_validate(record.report) if record.report else None
        return RunStatus(**_summarize(record).model_dump(), error=record.error, report=report)
