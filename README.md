# Gauntlet

A multi-agent portfolio construction system. You give it a mandate (risk profile, constraints, investable universe), an agent proposes an allocation, and — unlike most "AI portfolio manager" demos — the proposal is never taken on faith: it's backtested against real historical data, checked against deterministic compliance rules, and screened for qualitative exclusions before a synthesis agent signs off or sends it back for revision.

## Why this exists

Most LLM-driven portfolio tools stop at "the model said allocate 60/40" — plausible-sounding, unverified. This project's whole premise is the opposite: **propose, then verify by execution.** That distinction is the thing worth discussing in an interview, and it demonstrates a different set of engineering muscles than [Law_RAG](https://github.com/AlanC12138/Law_RAG) (retrieval + reasoning) or [SQL Copilot](https://github.com/AlanC12138/sql-copilot) (agentic tool-use + safety sandboxing):

- A genuine propose/verify/govern loop — the Synthesis Agent can reject an allocation and send it back to the Allocation Agent, not just concatenate outputs
- A real backtest engine (not a mocked return series) computing actual historical performance, drawdown, and risk metrics for the proposed allocation
- A deliberate split between **deterministic** compliance checking (numeric/structural rules — concentration limits, sector caps — must be auditable, not LLM-judged) and **RAG-grounded** qualitative screening (ESG/exclusion checks, where retrieval grounding matters because these are judgment calls over real documents, not arithmetic)
- A two-part eval harness: backtest-math correctness against known reference calculations, and compliance-checker precision/recall against labeled violation cases

See [MVP.md](MVP.md) for scope/phases and [TECH_STACK.md](TECH_STACK.md) for stack decisions and rationale.

## Status

[![Watch the demo](https://img.youtube.com/vi/_kiVVxvbi_4/maxresdefault.jpg)](https://youtu.be/_kiVVxvbi_4)

*A walkthrough: building a mandate with server-locked constraint bounds, the five-agent loop running live, and the full verified report.*

**All phases built and verified.** 209 tests, ruff clean, TypeScript clean, and every layer driven end to end in a browser — sign-in, the constraint builder, live progress, the loop-back, and custom constraints. Deployment is documented but deliberately not executed ([DEPLOYING.md](DEPLOYING.md)): every run costs $0.17–$0.40 and nothing rate-limits it yet.

Read the test count correctly: 54 of those 209 need the Postgres that `docker compose up` provides and skip cleanly without it, so a bare `pytest` on a fresh checkout reports **155 passed, 54 skipped** — not a quarter of the suite broken. Start the compose stack to run all 209.

**Verified on 2026-11-20.** The checks above were run against commit `50dde2a`; everything committed since is documentation only.

**Phases 1–2.** The full propose/verify/govern loop runs: the Allocation Agent proposes weights; the Verification Agent backtests them, the Constraint-Compliance Agent checks them deterministically, and the Screening Agent screens them against a RAG corpus; the Synthesis Agent then approves with a memo or sends the proposal back with revision feedback, bounded to three iterations. The demo universe is pinned and verified — 52 instruments across all 11 GICS sectors plus a fixed-income and commodity sleeve, 2013–2025, see [data/README.md](data/README.md).

Two guarantees are enforced in code rather than requested in a prompt:

- **Screening findings must be grounded.** Every finding has to quote a retrieved document verbatim; excerpts that appear in no retrieved note are dropped before the supervisor sees them. This has caught a live hallucination.
- **Compliance is binding, screening is advisory.** If the deterministic rules engine reports a violation the verdict is `revise` regardless of what the Synthesis Agent decided — an LLM that can talk past an arithmetic breach makes the rules engine decorative. Screening findings are LLM judgment and *can* be weighed, with justification.

**Phase 3 — eval harness.** Two suites, measuring genuinely different kinds of correctness. Run with `poetry run gauntlet eval` — no LLM calls, no tokens.

**Backtest math — 12/12 cases exact, worst deviation 2.8e-11.** Every metric is checked two ways: against hand-derived answers (constant growth compounds exactly; a 120→90 decline is exactly −25%), and against an independent reference implementation that simulates *shares* rather than return algebra — the way a broker statement is produced. Two implementations that share an approach agreeing proves little; two that don't is evidence. Cases run over both synthetic fixtures and 3,270 rows of real cached prices.

**Compliance conformance — 26/26 cases labeled exactly, 0 false positives, 0 false negatives across all 9 rules.** Labels are derived by hand from each mandate's meaning, never read off the engine, so the suite is an independent statement of the specification rather than a tautology. Read the number correctly: the engine is deterministic, so **100% is the pass mark, not an achievement** — anything lower is a defect. The cases that earn their keep are the boundaries (sitting exactly on a limit must *not* fire) and the tricky negatives (60% in broad-market funds against a 25% sector cap; gold against an equity floor), because an engine that flags everything scores perfect recall and only those catch it.

The harness paid for itself on its first run by finding a real bug: **Sharpe ratio exploded to ~1e15 on constant-return paths**, because the zero-variance guard tested `std > 0` and floating point returns ~1e-16 rather than 0. A portfolio held entirely in a stable short-duration bond fund would have reported an astronomical Sharpe in its memo.

**Phase 4 — persistence and job queue.** Postgres holds mandates, run history, eval results, the ESG corpus, and the append-only price cache; Redis + RQ run the pipeline off the request thread. `POST /api/runs` now returns a run id to poll rather than blocking.

The queue's original justification did not survive being measured. README and TECH_STACK both said it existed because backtests are compute-heavy; in fact loading the 52-ticker panel takes ~113 ms and backtesting 52 positions over 3,270 days takes ~31 ms. The real long pole is the **agent loop** — 10+ minutes of API latency for a three-iteration run — so the queued unit is the whole run, not the backtest inside it. Both documents are corrected.

The price cache moved from parquet to Postgres for a reason that only appears once the queue exists: the worker is a separate process, so a file on local disk stops being a shared store. Append-only survived the move and is now better enforced — `ON CONFLICT DO NOTHING` against the `(ticker, date)` primary key is atomic, where the parquet path had to read the whole cache, diff it in Python, and rewrite. The swap is verified lossless: the eval harness reports the same numbers to the last decimal, and both read paths are tested for parity.

**Phase 5 — frontend, live progress, auth, and constraint feasibility.** React + Vite with a results dashboard (Recharts), a live view of the governance loop over server-sent events, a constraint builder with server-computed range locking, and Clerk auth in a deliberately partial configuration.

**The constraint builder never decides what is legal.** Every bound comes from the API, computed by the same arithmetic the compliance engine uses — because a widget that reimplemented it would drift, which is exactly the failure that shipped an impossible 30% bond floor against an 8% position cap and burned three live iterations before anyone noticed. The whole constraint set is posted on each edit rather than one field, since the fields are interdependent: dropping the position cap from 10% to 5% moves the max-holdings floor from 12 to 20 and halves the reachable asset-class capacity. Controls with no freedom left are disabled, infeasible combinations are named in plain language, and the run button is disabled so no money is spent on an unsatisfiable mandate.

**The two post-execution constraints get evidence, not arithmetic.** Whether a 12% drawdown is reachable can't be decided by inequality — it would mean minimizing realized drawdown over every portfolio satisfying the weight rules. So `reachability.py` builds one defensive portfolio, backtests it, and reports what it achieved. A target at or above that is *demonstrably* reachable; a tighter one is **unproven, not impossible**. That asymmetry is load-bearing and the numbers show why: the heuristic reaches 15.7% on the conservative mandate, while a live agent run achieved 14.02%. Publishing the witness as a hard floor would have locked a client out of the 15% target the system actually meets — so it travels as a note, and those fields keep null bounds.

**Auth is drawn at cost, not at sensitivity.** Browsing the universe, the demo mandates, and every completed run — charts, memo, compliance and screening panels — needs no account, because that *is* the demo and a sign-up wall in front of it would defeat the point. Only `POST /api/runs` requires sign-in, because only starting a run spends money on Anthropic API calls. Setting no `CLERK_SECRET_KEY` disables auth entirely and makes everything public; that is a supported mode with its own test, not a fallback, so local development and a public deployment run the same code.

The live view is the part worth explaining. The run executes in the worker process while the browser is attached to the API process, so nothing bridges them by default. The worker publishes a stage event per agent to Redis; the API streams them. Two details make it hold up: events are appended to a capped list *as well as* published, so a browser that connects mid-run replays what it missed rather than opening on a blank panel; and publishing failures are swallowed, because losing a paid, minutes-long run over an undeliverable progress notification would be absurd. The report in Postgres is the record — progress is a convenience.

## Running it

```bash
poetry install
cp .env.example .env   # add your ANTHROPIC_API_KEY
```

Optionally add `CLERK_SECRET_KEY` to `.env` and `VITE_CLERK_PUBLISHABLE_KEY` to `frontend/.env.local` to require sign-in before starting a run. Leave both unset to run fully public.

On Windows, write those files with an editor or `Set-Content -Encoding utf8` — PowerShell's `>>` defaults to UTF-16, which the env parser reads as binary and silently ignores.

Start Postgres and Redis (non-default ports 5433/6380, so they can't collide with anything already running):

```bash
docker compose up -d
```

Create the schema:

```bash
poetry run alembic upgrade head
```

Warm the price cache once — 52 tickers × 3,270 trading days, fetched from yfinance and stored append-only — and load the ESG corpus:

```bash
poetry run gauntlet fetch
```

```bash
poetry run gauntlet import-corpus
```

Already have the Phase 1–3 parquet cache? Import it instead of re-downloading. It's insert-if-absent, so re-running writes nothing:

```bash
poetry run gauntlet import-prices
```

Propose an allocation and backtest it:

```bash
poetry run gauntlet run balanced
```

Backtest explicit weights without calling the LLM — useful for exercising the engine without spending tokens:

```bash
poetry run gauntlet backtest '{"SPY": 0.6, "AGG": 0.4}'
```

Tidy the stored run history — useful before recording a demo:

```bash
poetry run gauntlet runs list
```

```bash
poetry run gauntlet runs archive 9c3b8fb5
```

`archive` is a soft delete: the run drops out of listings and out of the frontend's history, but stays readable at `GET /api/runs/{id}`, so an existing link keeps working — and `runs unarchive` puts it back. Ids can be given as a unique prefix, like a short git SHA; an ambiguous one is refused rather than guessed at.

`runs purge <id>` deletes permanently and prompts first. It removes the run, its traced calls, and its mandate — in that order, because `agent_calls.run_id` references the run and `runs.mandate_id` references the mandate. A mandate shared with another run is kept. Prefer `archive`: a run cost real money and several minutes, and destroying one to tidy a list is a poor trade.

Run the eval harness (no LLM calls, no tokens spent). Results are recorded per suite, so the numbers read as a trend rather than a snapshot — the point of a regression guard is noticing the run where a number moved. Pass `--no-save` to skip, and note persistence is best-effort: a guard that refuses to report because a database is down has made itself the outage.

```bash
poetry run gauntlet eval
```

Or serve the whole thing. **Three terminals** — each of these blocks, so they each need their own:


```bash
poetry run uvicorn gauntlet.api:app --reload
```

Queued runs need a worker alongside it:

```bash
poetry run gauntlet worker
```

And the frontend:

```bash
npm install --prefix frontend && npm run dev --prefix frontend
```

`POST /api/runs` returns `202` with a run id; `GET /api/runs/{id}/events` streams stage events over SSE until the run ends; `GET /api/runs/{id}` returns status and, once complete, the full report. `GET /api/runs` lists recent runs as summaries. Also available: `GET /health`, `GET /api/universe`, `GET /api/mandates`, `GET /api/auth` (whether sign-in is required), and `POST /api/constraints/feasibility` (add `?include_risk=true` for the backtested reachability bound).

Infrastructure outages return `503`, not `500` — a down Postgres or Redis is not a bug in the pipeline, and the distinction tells a client to retry rather than file a report. Routes that need no store (`/health`, `/api/universe`, `/api/mandates`) keep serving during one.

Note that `status` and `approved` are separate fields, deliberately. A run that completes with `approved: false` is the governance loop refusing an allocation after three iterations — a successful run with a negative verdict, not a failure.

## Architecture

Everything below is built, except where marked.

```
React + Vite ── constraint builder (server-computed range locking)
        │       + live agent-progress view (SSE) + results dashboard (Recharts)
        │
        ├── Clerk — required only to START a run; all reads are public
        │
FastAPI ─┬─ Allocation Agent (Claude) — proposes allocation from mandate + portfolio theory
         ├─ Verification Agent — executes backtest against historical price data (custom engine)
         ├─ Screening Agent (Claude + RAG) — qualitative exclusion checks (ESG/controversy),
         │                                    grounded in retrieved news/ESG documents
         ├─ Constraint-Compliance Agent — deterministic, non-LLM numeric/structural rule checks
         │                                 (concentration limits, sector caps)
         ├─ Synthesis Agent (Claude, supervisor) — combines all outputs, has loop-back authority
         │                                          to Allocation Agent on failure
         ├─ Feasibility engine — pre-execution bounds by arithmetic; post-execution
         │                        bounds by backtested witness (existence proof only)
         ├─ Job queue (Redis + RQ) — the whole run executes off the request thread
         │                            (it's 10+ min of LLM latency; the backtest is 31 ms)
         ├─ Progress bus (Redis pub/sub + capped replay list) → SSE
         └─ Eval harness (backtest-math correctness + compliance precision/recall)
                │
        Postgres (mandates, run history, eval results, ESG corpus, append-only price cache)
        yfinance (historical price data) + hand-authored ESG corpus (RAG for Screening Agent)

        Not built: per-user data isolation, deployment.
```

**Phase 6 — traces, licence, code-splitting, and the deployment guide.** Every LLM call is now recorded: prompt, emitted response, model, tokens, cost, latency, and stop reason, keyed to run and iteration. `GET /api/runs/{id}/traces` returns them in the order they happened, and the results dashboard has an **Agent traces** panel that shows them grouped by iteration.

The run report already said *what* each agent decided and the stage events said *when*. Neither said **why** — and that gap was widest at the interesting moment. A loop-back is only explicable if you can read the revision feedback the Allocation Agent was handed, which is now recoverable verbatim:

> *"Compliance FAIL is binding: realized peak-to-trough drawdown of -17.20% breaches the mandate's 15.00% max drawdown tolerance… the drawdown is the only defect, so do not rebuild from scratch."*

This replaces the planned Langfuse integration. At this size a table won: four columns of substance, no vendor account, no SDK, and it keeps the offline posture the rest of the system has — every other component degrades gracefully without network, and tracing shouldn't be the exception. Recording is best-effort and can never fail a run, the same rule the progress bus follows.

It also produced the first per-agent latency data, which finally justifies the queue with numbers rather than assertion. From a real 3-iteration run:

| Agent | Latency per call |
|---|---|
| Allocation | 35s / 103s / 63s |
| Screening | ~2–3s |
| Synthesis | ~19–21s |

**~269 seconds of LLM latency against a 31 ms backtest.** The proposer is the long pole, and revisions cost more than first attempts — it is being asked to hit a drawdown target it has no tool to measure.

## Known limitations

Stated plainly, because the premise of this project is not taking claims on faith.

- **The Allocation Agent aims at a target it cannot measure.** It is told a drawdown tolerance but has no tool to compute one, so it optimizes by proxy and the governance loop catches the miss. The fix is a train/test split — a backtest tool over an in-sample window, with compliance verifying on a holdout — which would turn a single structured-output call into a tool-use loop. Established as a gap, not intent.
- **The model has look-ahead knowledge of the backtest window.** Training data covers 2013–2025, so it knows how these assets performed. There is no clean fix. The defensible framing, used throughout, is that the backtest is a **risk-constraint measurement, not a performance claim** — and the Synthesis prompt treats backtest results as informational only.
- **Reachability for drawdown and volatility is one-directional.** A backtested witness proves a target is achievable; nothing here proves one is impossible.
- **No per-user data isolation.** `runs` has no owner column — every signed-in user sees every run.
- **Survivorship bias in the universe.** Constituents were chosen knowing which companies still exist in 2026; sector labels are current, not point-in-time; there is no ETF look-through. See [data/README.md](data/README.md).
- **Not deployed, and a public URL would be an open wallet** — every run costs $0.17–$0.40 and nothing rate-limits it. See [DEPLOYING.md](DEPLOYING.md) for what to add first.

## Non-goals (v1)

- No real brokerage integration or trade execution — this proposes and verifies, it never places an order
- No multi-user orgs or billing — single-user auth only, and no per-user data isolation (see limitations above)
- No live/real-time trading signals — backtests run over historical data on demand
- No tax-lot optimization or after-tax return modeling
