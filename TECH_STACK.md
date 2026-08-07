# Gauntlet — Tech Stack

> Each choice includes why it fits *this* project specifically — not copied from other repos in this workspace.

---

## Backend

| Concern | Choice | Notes |
|---|---|---|
| Language | Python 3.13+ | Numeric/backtest work (pandas, numpy) and agent orchestration both live comfortably here |
| Web framework | FastAPI | Async-native — needed for streaming agent progress over SSE and for enqueuing pipeline runs without blocking the request thread (the run, not the backtest — see below) |
| Dependency management | Poetry | Lock file, clean packaging |
| ORM | SQLAlchemy 2.0 | Mandates, run history, eval results, the ESG corpus, and the append-only price cache — a fixed, known app schema, unlike SQL Copilot's arbitrary-target-DB reflection use case |
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
| App database | PostgreSQL | Mandates, run history, agent traces, eval results, the ESG corpus, and the append-only price cache — introduced Phase 4 |
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

## Auth (Phase 5)

| Concern | Choice | Notes |
|---|---|---|
| Auth | Clerk (`@clerk/react` + `clerk-backend-api`), single-user — no orgs, no billing | Buying auth beats hand-rolling it, without the org/billing surface SQL Copilot needed |

Moved here from Phase 4 during the build: auth is frontend-first, and standing it up with no UI to authenticate against would have meant shipping something untestable.

### The gate is drawn at cost, not at sensitivity

Reads are public — the universe, the demo mandates, and every completed run with its charts, memo, and compliance panels. That *is* the demo, and a sign-up wall in front of it would defeat the point of having one. Only `POST /api/runs` requires sign-in, because only starting a run spends money on Anthropic API calls.

Unsetting `CLERK_SECRET_KEY` disables auth entirely and makes everything public. That is a supported mode with its own test, not a fallback, so local development and a public deployment run the same code path.

**Not built: per-user data isolation.** An earlier version of this table claimed each user's mandates and history are their own. They are not — `runs` has no owner column, and every signed-in user sees every run. That is defensible for a single-user portfolio demo where the run history *is* the exhibit, but it is a real limitation rather than a design subtlety, and it would have to change before this held anyone else's data.

## Frontend (Phase 5)

| Concern | Choice | Notes |
|---|---|---|
| Framework | React + Vite + TypeScript | No SSR marketing/pricing pages needed here (unlike SQL Copilot), so a plain SPA is simpler than pulling in Next.js for a feature it wouldn't use |
| Styling | Hand-written CSS with custom properties | Originally specified as Tailwind. At roughly a dozen components, a utility framework would have added a build-time dependency and a class vocabulary to serve one page; CSS variables give the light/dark theming that was actually needed |
| Charting | Recharts | Equity curves, drawdown, sector exposure. It is roughly two thirds of the bundle, so the results dashboard is lazy-loaded: 330 kB initial + 392 kB on demand (98 kB gzipped first load) |
| Streaming | SSE over `fetch`, not `EventSource` | `EventSource` cannot send headers, which would force a Clerk bearer token into a query string where it lands in access logs. A streaming `fetch` costs a little manual frame parsing and keeps auth in a header |

## Observability & Eval

| Concern | Choice | Notes |
|---|---|---|
| LLM/agent tracing | An `agent_calls` table in Postgres, **not** Langfuse | Originally planned as Langfuse. At this size a table won: four columns of substance against a vendor account, an SDK and an egress dependency to read your own prompts — and it keeps the offline posture every other component has. Records prompt, emitted response, model, tokens, cost, latency and stop reason per call, keyed to run and iteration. Best-effort: a trace can never fail a paid run |
| Eval harness | Two-part: (1) backtest-math correctness vs. known reference calculations, (2) compliance-checker precision/recall vs. labeled violation cases | These are genuinely different correctness problems — the backtester needs numeric correctness, the compliance/screening layer needs classification accuracy — so one eval harness with one number wouldn't be honest about either |

## Infra

| Concern | Choice | Notes |
|---|---|---|
| Local dev | Docker Compose | Postgres + Redis |
| Deploy | Undecided, deferred | Same as SQL Copilot — not worth committing to a host until this needs a real public URL |
