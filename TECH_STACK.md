# Gauntlet — Tech Stack

> Each choice includes why it fits *this* project specifically — not copied from other repos in this workspace.

---

## Backend

| Concern | Choice | Notes |
|---|---|---|
| Language | Python 3.13+ | Numeric/backtest work (pandas, numpy) and agent orchestration both live comfortably here |
| Web framework | FastAPI | Async-native — needed for streaming agent progress over SSE and for kicking off async backtest jobs without blocking |
| Dependency management | Poetry | Lock file, clean packaging |
| ORM | SQLAlchemy 2.0 | Mandates, allocation history, eval results — a fixed, known app schema, unlike SQL Copilot's arbitrary-target-DB reflection use case |
| Migrations | Alembic | App DB schema versioning from Phase 4 onward |

## AI / Agent

| Concern | Choice | Notes |
|---|---|---|
| LLM | Claude (Anthropic SDK) | Used by the Allocation, Screening, and Synthesis agents. The Constraint-Compliance Agent deliberately does *not* use an LLM (see below) |
| Agent orchestration | Hand-rolled loop with Pydantic structured I/O between agents (no LangGraph) | The Synthesis Agent's loop-back-to-Allocation control flow is a small, specific state machine — a thin custom loop keeps that logic explicit and explainable rather than hidden inside a framework's graph abstraction |
| Constraint-Compliance Agent | Deterministic Python rules engine, no LLM in the loop | Numeric/structural checks (concentration limits, sector caps) need to be exactly reproducible and auditable — an LLM judgment call here would undermine the "verify by execution" premise of the whole project |
| Screening Agent | Claude + light RAG (BM25 + entity linkage) over ESG/news documents | Qualitative exclusion checks (e.g., "any company with a major ESG controversy") are genuinely judgment calls over unstructured text, unlike the numeric compliance checks — this is where retrieval grounding earns its keep, and *only* here |
| Backtest engine | Custom, vectorized (pandas/numpy) | Not a full framework (backtrader/zipline) — the Verification Agent's needs are narrow (given weights + rebalance schedule, compute historical returns/drawdown/risk metrics) and a custom engine keeps the computation transparent and directly explainable, matching the eval harness's need for known-reference-value correctness checks |

## Data

| Concern | Choice | Notes |
|---|---|---|
| Market data | yfinance | Free, no key, sufficient historical price coverage for backtesting |
| Price cache | Postgres, append-only table | Avoids re-fetching yfinance on every backtest; append-only keeps historical runs reproducible even if a later fetch would return revised data |
| ESG/news corpus (RAG) | Hand-authored notes in `data/esg_corpus/`, stored in Postgres | Co-located in Postgres, but **not** vectorized — see below |

### Why the corpus is not in pgvector

This originally targeted pgvector. Building it out, that turned into cargo-culting. At eight short notes, dense embeddings add an embedding-provider dependency and a similarity index to search a body of text that fits in a single prompt — and they make retrieval non-deterministic across provider model updates, which is disqualifying when Phase 3 scores screening reproducibility.

What actually drives quality at this size is **entity linkage**, not semantic similarity: the real question is "which notes bear on the tickers actually held," which the corpus answers exactly via front-matter. BM25 supplements it for concept matches ("fossil fuel", "weapons") that no ticker names.

`retrieval.Retriever` is a Protocol, so a pgvector implementation can be swapped in without the Screening Agent noticing. Past a few dozen documents, that is the moment to reconsider — not before.

## Data Storage

| Concern | Choice | Notes |
|---|---|---|
| App database | PostgreSQL | Mandates, run history, eval results, and the append-only price cache — introduced Phase 4 |
| Job queue | Redis + RQ | Queues the **whole pipeline run**, not the backtest — see below |

### Correcting the queue rationale

This table originally said the queue existed because *backtests are compute-heavy*. That turned out to be measurably false on this universe:

| Operation | Time |
|---|---|
| Load the 52-ticker price panel | ~113 ms |
| Backtest 52 positions × 3,270 trading days | ~31 ms |

A backtest is 31 ms. Queuing it would be pure ceremony.

The actual long pole is the **agent loop**: a three-iteration run is 10+ minutes of wall clock, almost entirely Anthropic API latency, and it twice exceeded a ten-minute command timeout during development. No browser, proxy, or load balancer will hold a request open that long. So the queued unit is the entire propose/verify/govern run, and `POST /api/runs` returns a job id rather than a report.

Worth noting the shape of the mistake: the original rationale was a plausible guess about where time goes in a system that backtests things, written before anything had been measured. It survived two phases because nothing forced a number.

## Auth (Phase 4)

| Concern | Choice | Notes |
|---|---|---|
| Auth | Clerk, scoped to single-user auth (no orgs, no billing) | This is a portfolio piece, not a monetized SaaS — per-user data isolation (each user's mandates/history are their own) is the actual requirement; buying auth still beats hand-rolling it, just without the org/billing surface SQL Copilot needed |

## Frontend (Phase 5)

| Concern | Choice | Notes |
|---|---|---|
| Framework | React + Vite + TypeScript + Tailwind | No SSR marketing/pricing pages needed here (unlike SQL Copilot), so a plain SPA is simpler than pulling in Next.js for a feature it wouldn't use |
| Charting | Recharts | Equity curves, allocation breakdowns, drawdown charts |
| Streaming | SSE | Live view of each agent's status as the propose/verify/govern loop runs, including loop-back iterations |

## Observability & Eval

| Concern | Choice | Notes |
|---|---|---|
| LLM/agent tracing | Langfuse (Cloud, free tier) | Same reasoning as SQL Copilot — purpose-built for tool-call/agent traces, and the Synthesis Agent's loop-back decisions are exactly the kind of control flow worth having full traces of |
| Eval harness | Two-part: (1) backtest-math correctness vs. known reference calculations, (2) compliance-checker precision/recall vs. labeled violation cases | These are genuinely different correctness problems — the backtester needs numeric correctness, the compliance/screening layer needs classification accuracy — so one eval harness with one number wouldn't be honest about either |

## Infra

| Concern | Choice | Notes |
|---|---|---|
| Local dev | Docker Compose | Postgres + Redis |
| Deploy | Undecided, deferred | Same as SQL Copilot — not worth committing to a host until this needs a real public URL |
