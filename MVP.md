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
| **1 — Core agents** (current) | Allocation Agent + Verification Agent (custom backtest engine), hardcoded demo mandate, no persistence, tested via CLI/Postman | Prove the propose → backtest loop works end to end before adding compliance/screening |
| 2 — Compliance + screening | Constraint-Compliance Agent (deterministic rules engine) + Screening Agent (RAG-backed) + Synthesis Agent with loop-back authority | This is where the "verify, don't trust" premise actually gets built |
| 3 — Eval harness | Two-part: backtest-math correctness vs. known reference calculations; compliance-checker precision/recall vs. labeled violation cases | The quantifiable artifact — and the regression guard for both the backtester and the rules engine |
| 4 — Persistence + auth | Postgres (mandates, allocation history, eval results, append-only price cache), Redis + RQ async job queue for backtests, Clerk auth (single-user scoped) | App database introduced here, not before |
| 5 — Frontend | React + Vite: mandate input, live SSE agent-progress view, final memo with Recharts (equity curve, allocation breakdown, drawdown) | |
| 6 — Polish | Langfuse tracing wired into the full agent loop (including loop-back iterations), README with eval numbers + demo GIF, deploy | |

## Explicit non-goals (for now)

- Real brokerage integration or trade execution — out of scope permanently; this product only ever proposes and verifies.
- Multi-user orgs or billing — single-user auth only.
- Live/real-time trading signals — stretch, not MVP.
- Tax-lot optimization or after-tax return modeling — stretch.
- Support for asset classes beyond liquid US equities/ETFs (fixed income, derivatives, international) — stretch.

## Architecture (target, post-Phase 5)

```
React + Vite ── mandate input + live agent-progress view (SSE) + results dashboard (Recharts)
        │
        ├── Clerk (auth, single-user scoped)
        │
FastAPI ─┬─ Allocation Agent (Claude) — proposes weights from mandate
         ├─ Verification Agent — custom backtest engine (pandas/numpy)
         ├─ Screening Agent (Claude + RAG/pgvector) — qualitative exclusion checks
         ├─ Constraint-Compliance Agent — deterministic rules engine, no LLM
         ├─ Synthesis Agent (Claude, supervisor) — loop-back authority + final memo
         ├─ Redis + RQ — async backtest job queue
         └─ Eval harness (backtest-math correctness + compliance precision/recall)
                │
        Postgres (mandates, allocation history, eval results, append-only price cache)
        yfinance (price data) + news/ESG source (RAG corpus)
```
