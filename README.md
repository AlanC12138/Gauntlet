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

**Phases 1–2 built.** The full propose/verify/govern loop runs: the Allocation Agent proposes weights; the Verification Agent backtests them, the Constraint-Compliance Agent checks them deterministically, and the Screening Agent screens them against a RAG corpus; the Synthesis Agent then approves with a memo or sends the proposal back with revision feedback, bounded to three iterations. The demo universe is pinned and verified — 52 instruments across all 11 GICS sectors plus a fixed-income and commodity sleeve, 2013–2025, see [data/README.md](data/README.md).

Two guarantees are enforced in code rather than requested in a prompt:

- **Screening findings must be grounded.** Every finding has to quote a retrieved document verbatim; excerpts that appear in no retrieved note are dropped before the supervisor sees them. This has caught a live hallucination.
- **Compliance is binding, screening is advisory.** If the deterministic rules engine reports a violation the verdict is `revise` regardless of what the Synthesis Agent decided — an LLM that can talk past an arithmetic breach makes the rules engine decorative. Screening findings are LLM judgment and *can* be weighed, with justification.

**Phase 3 (eval harness) built.** Two suites, measuring genuinely different kinds of correctness. Run with `poetry run gauntlet eval` — no LLM calls, no tokens.

**Backtest math — 12/12 cases exact, worst deviation 2.8e-11.** Every metric is checked two ways: against hand-derived answers (constant growth compounds exactly; a 120→90 decline is exactly −25%), and against an independent reference implementation that simulates *shares* rather than return algebra — the way a broker statement is produced. Two implementations that share an approach agreeing proves little; two that don't is evidence. Cases run over both synthetic fixtures and 3,270 rows of real cached prices.

**Compliance conformance — 26/26 cases labeled exactly, 0 false positives, 0 false negatives across all 9 rules.** Labels are derived by hand from each mandate's meaning, never read off the engine, so the suite is an independent statement of the specification rather than a tautology. Read the number correctly: the engine is deterministic, so **100% is the pass mark, not an achievement** — anything lower is a defect. The cases that earn their keep are the boundaries (sitting exactly on a limit must *not* fire) and the tricky negatives (60% in broad-market funds against a 25% sector cap; gold against an equity floor), because an engine that flags everything scores perfect recall and only those catch it.

The harness paid for itself on its first run by finding a real bug: **Sharpe ratio exploded to ~1e15 on constant-return paths**, because the zero-variance guard tested `std > 0` and floating point returns ~1e-16 rather than 0. A portfolio held entirely in a stable short-duration bond fund would have reported an astronomical Sharpe in its memo.

**Phase 4 (persistence + job queue) built.** Postgres holds mandates, run history, eval results, the ESG corpus, and the append-only price cache; Redis + RQ run the pipeline off the request thread. `POST /api/runs` now returns a run id to poll rather than blocking.

The queue's original justification did not survive being measured. README and TECH_STACK both said it existed because backtests are compute-heavy; in fact loading the 52-ticker panel takes ~113 ms and backtesting 52 positions over 3,270 days takes ~31 ms. The real long pole is the **agent loop** — 10+ minutes of API latency for a three-iteration run — so the queued unit is the whole run, not the backtest inside it. Both documents are corrected.

The price cache moved from parquet to Postgres for a reason that only appears once the queue exists: the worker is a separate process, so a file on local disk stops being a shared store. Append-only survived the move and is now better enforced — `ON CONFLICT DO NOTHING` against the `(ticker, date)` primary key is atomic, where the parquet path had to read the whole cache, diff it in Python, and rewrite. The swap is verified lossless: the eval harness reports the same numbers to the last decimal, and both read paths are tested for parity.

Phase 5 (frontend + Clerk auth) is next.

## Running it

```bash
poetry install
cp .env.example .env   # add your ANTHROPIC_API_KEY
```

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

Run the eval harness (no LLM calls, no tokens spent):

```bash
poetry run gauntlet eval
```

Or serve the API:

```bash
poetry run uvicorn gauntlet.api:app --reload
```

Queued runs need a worker alongside it:

```bash
poetry run gauntlet worker
```

`POST /api/runs` returns `202` with a run id; poll `GET /api/runs/{id}` for status and, once complete, the full report. `GET /api/runs` lists recent runs as summaries. Also available: `GET /api/universe`, `GET /api/mandates`, and `POST /api/constraints/feasibility`.

Note that `status` and `approved` are separate fields, deliberately. A run that completes with `approved: false` is the governance loop refusing an allocation after three iterations — a successful run with a negative verdict, not a failure.

## Architecture (target)

```
React + Vite ── mandate input form + live agent-progress view (SSE) + results dashboard (Recharts)
        │
        ├── Clerk (auth, single-user scoped — no orgs/billing)
        │
FastAPI ─┬─ Allocation Agent (Claude) — proposes allocation from mandate + portfolio theory
         ├─ Verification Agent — executes backtest against historical price data (custom engine)
         ├─ Screening Agent (Claude + RAG) — qualitative exclusion checks (ESG/controversy),
         │                                    grounded in retrieved news/ESG documents
         ├─ Constraint-Compliance Agent — deterministic, non-LLM numeric/structural rule checks
         │                                 (concentration limits, sector caps)
         ├─ Synthesis Agent (Claude, supervisor) — combines all outputs, has loop-back authority
         │                                          to Allocation Agent on failure
         ├─ Job queue (Redis + RQ) — the whole run executes off the request thread
         │                            (it's 10+ min of LLM latency; the backtest is 31 ms)
         └─ Eval harness (backtest-math correctness + compliance precision/recall)
                │
        Postgres (mandates, run history, eval results, ESG corpus, append-only price cache)
        yfinance (historical price data) + news/ESG source (RAG corpus for Screening Agent)
```

## Non-goals (v1)

- No real brokerage integration or trade execution — this proposes and verifies, it never places an order
- No multi-user orgs or billing — single-user auth only
- No live/real-time trading signals — backtests run over historical data on demand
- No tax-lot optimization or after-tax return modeling
