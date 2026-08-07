# Deploying Gauntlet

Not deployed. This is the guide for when that changes, written while the
architecture was fresh rather than reconstructed later.

## Read this first: a public URL is an open wallet

Every click of **Run the gauntlet** costs roughly **$0.17–$0.40** in Anthropic API
calls and takes one to three minutes. That is the single fact that should shape
any deployment decision here, and it is why this is not already live.

Clerk already gates `POST /api/runs`, so a visitor cannot start a run without
signing in — but signing up is free and unlimited, so auth alone is a speed bump
rather than a budget. Before exposing this publicly, add **at least one** of:

- A per-user run quota (a `count(*) from runs where user_id = ?` check — note this
  needs the per-user column the schema does not yet have, see Limitations below).
- An allow-list of user IDs permitted to start runs.
- A global daily spend ceiling, checked against `sum(cost_usd) from runs` for the
  day, refusing new runs past it. The data is already recorded.

The cheapest safe option is to **deploy read-only**: leave `CLERK_SECRET_KEY` set
and remove nobody's access to browse, but disable run creation entirely, letting
visitors explore the stored runs. The dashboards, the loop-back, the traces and
the constraint builder are all still there — the demo does not depend on a
visitor being able to spend your money.

## What has to run

Five pieces, and the worker is the one people forget:

| Piece | Notes |
|---|---|
| Postgres | Holds everything. The price cache alone is 170,040 rows |
| Redis | Job queue and the progress bus |
| API (`uvicorn`) | Stateless; scale freely |
| **Worker** (`gauntlet worker`) | Without it, runs sit at `queued` forever |
| Frontend | Static files after `npm run build` — any static host |

The API and worker must share both Postgres and Redis. The worker holds a
connection across 10+ minutes of Anthropic latency, which is why the engine sets
`pool_pre_ping`.

## Before the first deploy

1. **Use the forking worker.** `gauntlet worker` selects `SimpleWorker` on
   Windows because Windows has no `fork`. On Linux it uses RQ's default forking
   worker, which gives per-job timeouts and isolates a crash to one job. Deploy on
   Linux and this happens automatically.
2. **Run migrations**: `alembic upgrade head`.
3. **Seed the data**: `gauntlet fetch` (or `gauntlet import-prices` from an
   existing parquet) and `gauntlet import-corpus`. Without the corpus the
   Screening Agent falls back to reading files from the repo, which a container
   may not have.
4. **Set `GAUNTLET_CORS_ORIGINS`** to the deployed frontend origin. The default
   allow-list is localhost only, and it is an explicit list rather than `*`
   because credentialed requests are rejected against a wildcard.
5. **Point `VITE_API_BASE`** at the deployed API before building the frontend —
   it is compiled in, not read at runtime.
6. **Move Clerk to a production instance.** Development keys carry strict usage
   limits and show a console warning. Production keys need a real domain.

## Configuration

Everything is environment-driven; see `.env.example` and `frontend/.env.example`.

| Variable | Purpose |
|---|---|
| `ANTHROPIC_API_KEY` | Required. The only unavoidable cost |
| `GAUNTLET_DATABASE_URL` | `postgresql+psycopg://…` |
| `GAUNTLET_REDIS_URL` | `redis://…` |
| `GAUNTLET_CORS_ORIGINS` | JSON list of allowed browser origins |
| `CLERK_SECRET_KEY` | Unset ⇒ **auth disabled and every route public**. Never leave unset on a public deployment |
| `VITE_CLERK_PUBLISHABLE_KEY` | Frontend, compiled in |
| `GAUNTLET_EFFORT` | `medium`. `high` has been observed truncating past 32k output tokens |

## Sizing

- **API**: trivial. It enqueues and reads; it never calls Anthropic.
- **Worker**: one is enough. Concurrency is bounded by your Anthropic rate limit,
  not by CPU — a run is ~99% waiting on the API. Memory is dominated by the price
  panel (~52 × 3,270 floats), which is small.
- **Postgres**: the price cache is the bulk and it is ~170k narrow rows. Traces
  grow fastest — roughly 8 calls per run at a few KB each.
- **Redis**: progress events only, capped at 500 per run with a 24h TTL.

## Health and observability

- `GET /health` needs no database, so it stays green during a store outage and is
  the right liveness probe. For readiness, prefer `GET /api/runs`, which does
  touch Postgres and returns 503 when it is down.
- Store outages return **503, not 500**, so a load balancer can distinguish
  "retry" from "this is broken".
- `GET /api/runs/{id}/traces` returns every LLM call for a run — prompts,
  responses, tokens, latency.
- `gauntlet eval` records to `eval_runs` on each execution, so a regression shows
  as a change in the series rather than a number nobody compared.

## Limitations that matter more once deployed

- **No per-user data isolation.** `runs` has no owner column, so every signed-in
  user sees every run — including their prompts and costs. Fine for a single-user
  demo, not fine for anyone else's data. This is the first thing to fix if this
  ever becomes multi-user, and it is also what a per-user quota would need.
- **No rate limiting.** See the warning at the top.
- The frontend build is ~330 kB initial + 392 kB of charts loaded on demand.
