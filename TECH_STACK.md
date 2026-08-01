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
| Screening Agent | Claude + light RAG (pgvector) over ESG/news documents | Qualitative exclusion checks (e.g., "any company with a major ESG controversy") are genuinely judgment calls over unstructured text, unlike the numeric compliance checks — this is where retrieval grounding earns its keep, and *only* here |
| Backtest engine | Custom, vectorized (pandas/numpy) | Not a full framework (backtrader/zipline) — the Verification Agent's needs are narrow (given weights + rebalance schedule, compute historical returns/drawdown/risk metrics) and a custom engine keeps the computation transparent and directly explainable, matching the eval harness's need for known-reference-value correctness checks |

## Data

| Concern | Choice | Notes |
|---|---|---|
| Market data | yfinance | Free, no key, sufficient historical price coverage for backtesting |
| Price cache | Postgres, append-only table | Avoids re-fetching yfinance on every backtest; append-only keeps historical runs reproducible even if a later fetch would return revised data |
| ESG/news corpus (RAG) | News API or web search tool, chunked into pgvector | Small corpus, co-located in Postgres — same reasoning as SQL Copilot's schema RAG: not enough volume to justify a dedicated vector service |

## Data Storage

| Concern | Choice | Notes |
|---|---|---|
| App database | PostgreSQL | Mandates, allocation history, eval results — introduced Phase 4 |
| Job queue | Redis + RQ | Backtests are compute-heavy relative to a request/response cycle; queuing them keeps the API responsive and gives the frontend a job-status hook to poll/stream from |

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
