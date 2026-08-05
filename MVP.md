# Gauntlet — MVP & Phase Plan

## Pitch

Give it a mandate — risk profile, constraints, investable universe — and a multi-agent system proposes a portfolio allocation, then actually verifies it: backtests it against real historical data, runs it through a deterministic compliance-rules engine, and screens it for qualitative exclusions via RAG-grounded retrieval. A supervisor agent can reject the proposal and send it back for revision before producing a final investment-committee-style memo.

## Core user flow (target)

1. User submits a mandate: risk tolerance, constraints (e.g., max 10% single position, sector caps, ESG exclusions), and an investable universe (or uses a bundled demo universe).
2. **Allocation Agent** proposes a set of portfolio weights from the mandate.
3. In parallel: **Verification Agent** backtests the proposed weights against historical price data (returns, drawdown, volatility, Sharpe); **Constraint-Compliance Agent** deterministically checks the weights against the mandate's numeric/structural rules; **Screening Agent** checks the proposed holdings against qualitative exclusion criteria using RAG-grounded retrieval over ESG/news documents.
4. **Synthesis Agent** reviews all three outputs. If compliance or screening fails, it sends the mandate + failure reasons back to the Allocation Agent for a revised proposal (bounded number of iterations). If everything passes, it produces a final memo: allocation, backtest performance, compliance status, and reasoning.
5. Frontend streams each agent's status live as the loop runs, including any loop-back iterations, then renders the final memo with equity curve and allocation charts.

## Demo dataset / universe

A fixed set of ~30-50 liquid US equities and ETFs spanning multiple sectors, with cached historical price data — enough to require real portfolio construction (diversification, sector caps) without needing a live brokerage connection.

## Phases

| Phase | Scope | Notes |
|---|---|---|
| 1 — Core agents ✅ | Allocation Agent + Verification Agent (custom backtest engine), hardcoded demo mandate, no persistence, tested via CLI/Postman | Prove the propose → backtest loop works end to end before adding compliance/screening |
| 2 — Compliance + screening ✅ | Constraint-Compliance Agent (deterministic rules engine) + Screening Agent (RAG-backed) + Synthesis Agent with loop-back authority | This is where the "verify, don't trust" premise actually gets built |
| 3 — Eval harness ✅ | Two-part: backtest-math correctness vs. known reference calculations; compliance-checker precision/recall vs. labeled violation cases | The quantifiable artifact — and the regression guard for both the backtester and the rules engine |
| 4 — Persistence ✅ | Postgres (mandates, run history, eval results, ESG corpus, append-only price cache), Redis + RQ job queue for **the agent loop** | App database introduced here, not before. Two plan changes, both measured rather than assumed: the queue runs the whole pipeline (a backtest is 31 ms; the agent loop is 10+ minutes), and Clerk moved to Phase 5 because it is frontend-first — auth with no UI to authenticate against is untestable |
| 5 — Frontend + auth ✅ | React + Vite: constraint builder with server-computed range locking, live SSE agent-progress view, final memo with Recharts (equity curve, allocation breakdown, drawdown). Clerk auth gated at cost — reads public, starting a run requires sign-in. Backtest-derived reachability for post-execution constraints, reported as an existence proof rather than a floor | Auth is disabled entirely when no Clerk key is set; that is a supported mode with its own test, so local dev and a public demo run the same code |
| **6 — Polish** (current) | Langfuse tracing, README with eval numbers + demo GIF, deploy | |

## Explicit non-goals (for now)

- Real brokerage integration or trade execution — out of scope permanently; this product only ever proposes and verifies.
- Multi-user orgs or billing — single-user auth only.
- Live/real-time trading signals — stretch, not MVP.
- Tax-lot optimization or after-tax return modeling — stretch.
- Support for asset classes beyond liquid US-listed equities and ETFs — derivatives and international remain a stretch. Fixed income and commodities are **no longer** excluded: the universe carries seven bond funds and a gold ETF, because asset-class bands and a conservative mandate's bond floor are unenforceable without them.

## Architecture (target, post-Phase 5)

```
React + Vite ── mandate input + live agent-progress view (SSE) + results dashboard (Recharts)
        │
        ├── Clerk (auth, single-user scoped)
        │
FastAPI ─┬─ Allocation Agent (Claude) — proposes weights from mandate
         ├─ Verification Agent — custom backtest engine (pandas/numpy)
         ├─ Screening Agent (Claude + RAG: BM25 + entity linkage) — qualitative exclusions
         ├─ Constraint-Compliance Agent — deterministic rules engine, no LLM
         ├─ Synthesis Agent (Claude, supervisor) — loop-back authority + final memo
         ├─ Redis + RQ — job queue for the whole run (not the backtest; see TECH_STACK.md)
         └─ Eval harness (backtest-math correctness + compliance precision/recall)
                │
        Postgres (mandates, run history, eval results, ESG corpus, append-only price cache)
        yfinance (price data) + news/ESG source (RAG corpus)
```
