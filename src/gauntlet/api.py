"""FastAPI surface for Phase 1 — synchronous, no persistence.

Backtests run on the request thread here. The Redis + RQ job queue and SSE
progress stream arrive in Phase 4/5; until then a run is one blocking call.
"""

import logging

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from gauntlet.feasibility import FeasibilityReport, assess
from gauntlet.mandates import DEMO_MANDATES, get_mandate
from gauntlet.models import Constraints, Mandate, RebalanceFrequency, RunReport
from gauntlet.pipeline import run_pipeline
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


@app.post("/api/runs", response_model=RunReport)
def create_run(request: RunRequest) -> RunReport:
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
        return run_pipeline(mandate)
    except ValueError as exc:
        # Validation failures from the Allocation Agent's output.
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
