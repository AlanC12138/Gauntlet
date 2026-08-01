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

**Phases 1–2 built.** The full propose/verify/govern loop runs: the Allocation Agent proposes weights; the Verification Agent backtests them, the Constraint-Compliance Agent checks them deterministically, and the Screening Agent screens them against a RAG corpus; the Synthesis Agent then approves with a memo or sends the proposal back with revision feedback, bounded to three iterations. The demo universe is pinned and verified — 47 instruments across all 11 GICS sectors, 2013–2025, see [data/README.md](data/README.md).

Two guarantees are enforced in code rather than requested in a prompt:

- **Screening findings must be grounded.** Every finding has to quote a retrieved document verbatim; excerpts that appear in no retrieved note are dropped before the supervisor sees them. This has caught a live hallucination.
- **Compliance is binding, screening is advisory.** If the deterministic rules engine reports a violation the verdict is `revise` regardless of what the Synthesis Agent decided — an LLM that can talk past an arithmetic breach makes the rules engine decorative. Screening findings are LLM judgment and *can* be weighed, with justification.

Phase 3 (the two-part eval harness) is next.

## Running it

```bash
poetry install
cp .env.example .env   # add your ANTHROPIC_API_KEY
```

Warm the price cache once — 47 tickers × 3,270 trading days, fetched from yfinance and stored append-only:

```bash
poetry run gauntlet fetch
```

Propose an allocation and backtest it:

```bash
poetry run gauntlet run balanced
```

Backtest explicit weights without calling the LLM — useful for exercising the engine without spending tokens:

```bash
poetry run gauntlet backtest '{"SPY": 0.6, "AGG": 0.4}'
```

Or serve the API (`POST /api/runs`, `GET /api/universe`, `GET /api/mandates`):

```bash
poetry run uvicorn gauntlet.api:app --reload
```

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
         ├─ Job queue (Redis + RQ) — backtests run async, off the request thread
         └─ Eval harness (backtest-math correctness + compliance precision/recall)
                │
        Postgres (mandates, allocation history, eval results, append-only price cache)
        yfinance (historical price data) + news/ESG source (RAG corpus for Screening Agent)
```

## Non-goals (v1)

- No real brokerage integration or trade execution — this proposes and verifies, it never places an order
- No multi-user orgs or billing — single-user auth only
- No live/real-time trading signals — backtests run over historical data on demand
- No tax-lot optimization or after-tax return modeling
