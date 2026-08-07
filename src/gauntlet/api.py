"""FastAPI surface.

Runs are asynchronous as of Phase 4. `POST /api/runs` enqueues and returns a run
id immediately; the client polls `GET /api/runs/{id}`. This is not premature
scaling — a three-iteration run is 10+ minutes of Anthropic API latency, which no
browser or proxy will hold a connection open for. See `gauntlet.jobs` for why the
queued unit is the whole run rather than the backtest.

`GET /api/runs/{id}/events` streams stage events over SSE on top of the same run
records, so a client can watch the governance loop rather than poll a spinner.
"""

import datetime as dt
import logging

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel
from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError

from gauntlet.db.models import RunRecord
from gauntlet.db.runs import STATUS_QUEUED
from gauntlet.auth import CurrentUser
from gauntlet.config import settings
from gauntlet.feasibility import FeasibilityReport, assess
from gauntlet.mandates import DEMO_MANDATES, get_mandate
from gauntlet.models import Constraints, Mandate, RebalanceFrequency, RunReport
from gauntlet.universe import Universe, load_universe

logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Gauntlet",
    version="0.1.0",
    description="Multi-agent portfolio construction with execution-based verification.",
)


# The Vite dev server is a different origin from this API, so every browser call
# is cross-origin. Origins are an explicit allow-list rather than "*" because
# credentialed requests (Clerk's session cookie) are rejected outright against a
# wildcard — a setting that appears to work until auth is added.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(SQLAlchemyError)
@app.exception_handler(RedisError)
def _service_unavailable(request: Request, exc: Exception) -> JSONResponse:
    """Infrastructure outages are 503, not 500.

    A down Postgres or Redis is not a bug in the pipeline, and the status code
    should not imply that it is: 503 tells a client to retry, 500 tells it to
    file a report. Handled app-wide rather than per-endpoint because every route
    that touches a store has the same failure mode, and the one route that
    caught it locally was the only one that did.
    """
    logger.warning("%s %s failed: %s", request.method, request.url.path, exc)
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content={"detail": f"Service unavailable: {type(exc).__name__}"},
    )


class RunRequest(BaseModel):
    """Either name a demo mandate or supply one inline."""

    mandate_name: str | None = "balanced"
    mandate: Mandate | None = None
    rebalance: RebalanceFrequency | None = None


class AuthConfig(BaseModel):
    enabled: bool


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


class MandateOption(BaseModel):
    """A demo mandate together with the key that selects it.

    The key is what `POST /api/runs` takes, and returning the mandates without it
    made this endpoint unusable for building a picker: three of the five demo
    mandates share a `risk_profile`, so a client keying off any other field
    silently starts the wrong run.
    """

    key: str
    mandate: Mandate


@app.get("/api/mandates", response_model=list[MandateOption])
def list_mandates() -> list[MandateOption]:
    return [MandateOption(key=key, mandate=get_mandate(key)) for key in DEMO_MANDATES]


@app.post("/api/constraints/feasibility", response_model=FeasibilityReport)
def check_constraint_feasibility(
    constraints: Constraints, include_risk: bool = False
) -> FeasibilityReport:
    """Whether a constraint set is satisfiable, and the legal range of each field.

    Backs the Phase 5 mandate builder: a client adjusts a control, the widget
    re-posts the *whole* constraint set, and locks the ranges that come back.
    Posting the whole set rather than one field is not incidental — the fields
    are interdependent, so a per-field check would report stale bounds. Cheap
    enough to call on every keystroke: pure arithmetic, no model, no backtest.

    `include_risk` additionally backtests a defensive portfolio to say what
    drawdown and volatility are *demonstrably* reachable. It stays opt-in because
    it costs a backtest — milliseconds become hundreds of them — and because the
    answer it gives is one-directional: a witness proves a target is achievable
    and never proves one impossible. Those two fields therefore still return null
    bounds, with the achieved level carried in the note; publishing it as a floor
    would lock a client out of targets the system actually meets.
    """
    return assess(constraints, include_risk_bounds=include_risk)


@app.get("/api/auth", response_model=AuthConfig)
def auth_config() -> AuthConfig:
    """Whether this deployment requires sign-in to start a run.

    The frontend asks rather than assuming, so a build with no Clerk key
    configured renders no sign-in affordance at all instead of showing a button
    that cannot work.
    """
    return AuthConfig(enabled=settings.auth_enabled)


@app.post("/api/runs", response_model=RunAccepted, status_code=status.HTTP_202_ACCEPTED)
def create_run(request: RunRequest, user: CurrentUser) -> RunAccepted:
    """Queue a run. Returns immediately with an id to poll.

    The one write route, and the only one that requires sign-in — a run spends
    real money on Anthropic API calls, whereas reading the universe, the demo
    mandates, and completed runs is the demo itself.
    """
    from gauntlet.jobs import enqueue_run

    logger.info("Run requested by %s", user.user_id or "anonymous")

    if request.mandate is not None:
        mandate = request.mandate
    else:
        try:
            mandate = get_mandate(request.mandate_name or "balanced")
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from None

    if request.rebalance is not None:
        mandate = mandate.model_copy(update={"rebalance": request.rebalance})

    # RedisError and SQLAlchemyError are handled app-wide as 503; see above.
    return RunAccepted(run_id=enqueue_run(mandate), status=STATUS_QUEUED)


@app.get("/api/runs", response_model=list[RunSummary])
def list_runs(limit: int = 50) -> list[RunSummary]:
    from gauntlet.db import get_session
    from gauntlet.db import runs as repo

    with get_session() as session:
        return [_summarize(r) for r in repo.list_runs(session, limit=limit)]


@app.get("/api/runs/{run_id}/events")
async def stream_run_events(run_id: str) -> StreamingResponse:
    """Server-sent events for a run, replaying anything already emitted.

    Deliberately does not 404 on an unknown run: a client that posts a run and
    connects immediately can arrive before the worker has published anything, and
    an empty stream that later fills is the correct behaviour there. A genuinely
    unknown id simply yields nothing and times out on the client's terms.
    """
    from gauntlet.progress import event_stream

    return StreamingResponse(
        event_stream(run_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            # Tells nginx not to buffer, which would otherwise hold every event
            # until the response completed and defeat the entire mechanism.
            "X-Accel-Buffering": "no",
        },
    )


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
