# Gauntlet — MVP & Phase Plan

## Pitch

Give it a mandate — risk profile, constraints, investable universe — and a multi-agent system proposes a portfolio allocation, then actually verifies it: backtests it against real historical data, runs it through a deterministic compliance-rules engine, and screens it for qualitative exclusions via RAG-grounded retrieval. A supervisor agent can reject the proposal and send it back for revision before producing a final investment-committee-style memo.

## Core user flow (built)

1. User picks a demo mandate and edits its constraints in a builder whose bounds are computed server-side, so unachievable combinations are locked rather than left to fail at run time.
2. **Allocation Agent** proposes a set of portfolio weights from the mandate.
3. Three independent assessments — none reads another's output, which is what makes their agreement evidence rather than an echo. They run **sequentially, in a fixed order**, not in parallel: **Verification Agent** backtests first (returns, drawdown, volatility, Sharpe), because the **Constraint-Compliance Agent** needs those results to check post-execution rules like drawdown tolerance; the **Screening Agent** then checks holdings against qualitative exclusions via RAG-grounded retrieval. That ordering is load-bearing, not incidental — a constraint measured after execution is the one a proposer handed the rules cannot pre-satisfy.
4. **Synthesis Agent** reviews all three outputs. If compliance or screening fails, it sends the mandate + failure reasons back to the Allocation Agent for a revised proposal (bounded number of iterations). If everything passes, it produces a final memo: allocation, backtest performance, compliance status, and reasoning.
5. Frontend streams each agent's status live as the loop runs, including any loop-back iterations, then renders the final memo with equity curve and allocation charts.

## Demo dataset / universe

52 instruments — 39 US large-cap equities plus 13 ETFs (5 broad/sector equity, 7 fixed income, 1 commodity) — spanning all 11 GICS sectors, over a fixed 2013-01-01 → 2025-12-31 window of 3,270 trading days. Enough to require real portfolio construction (diversification, sector caps, asset-class bands) without a live brokerage connection. Prices are cached append-only in Postgres: 170,040 bars. See [data/README.md](data/README.md).

## Phases

| Phase | Scope | Notes |
|---|---|---|
| 1 — Core agents ✅ | Allocation Agent + Verification Agent (custom backtest engine), hardcoded demo mandate, no persistence, tested via CLI/Postman | Prove the propose → backtest loop works end to end before adding compliance/screening |
| 2 — Compliance + screening ✅ | Constraint-Compliance Agent (deterministic rules engine) + Screening Agent (RAG-backed) + Synthesis Agent with loop-back authority | This is where the "verify, don't trust" premise actually gets built |
| 3 — Eval harness ✅ | Two-part: backtest-math correctness vs. known reference calculations; compliance-checker precision/recall vs. labeled violation cases | The quantifiable artifact — and the regression guard for both the backtester and the rules engine |
| 4 — Persistence ✅ | Postgres (mandates, run history, eval results, ESG corpus, append-only price cache), Redis + RQ job queue for **the agent loop** | App database introduced here, not before. Two plan changes, both measured rather than assumed: the queue runs the whole pipeline (a backtest is 31 ms; the agent loop is 10+ minutes), and Clerk moved to Phase 5 because it is frontend-first — auth with no UI to authenticate against is untestable |
| 5 — Frontend + auth ✅ | React + Vite: constraint builder with server-computed range locking, live SSE agent-progress view, final memo with Recharts (equity curve, allocation breakdown, drawdown). Clerk auth gated at cost — reads public, starting a run requires sign-in. Backtest-derived reachability for post-execution constraints, reported as an existence proof rather than a floor | Auth is disabled entirely when no Clerk key is set; that is a supported mode with its own test, so local dev and a public demo run the same code |
| 6 — Polish ✅ | Per-call agent traces in Postgres, MIT licence, bundle code-splitting, deployment guide | Langfuse was dropped for a table — see TECH_STACK. Deployment is documented but deliberately not executed: every run costs $0.17–$0.40 and nothing rate-limits it yet |
| **7 — Demo** (current) | Recorded walkthrough of a loop-back run; optional deploy once run spend is capped | Shot list in [DEMO.md](DEMO.md). Traces are now surfaced in the UI so the revision feedback is showable rather than curl-only — that panel is the payoff shot |

## Explicit non-goals (for now)

- Real brokerage integration or trade execution — out of scope permanently; this product only ever proposes and verifies.
- Multi-user orgs or billing — single-user auth only.
- Live/real-time trading signals — stretch, not MVP.
- Tax-lot optimization or after-tax return modeling — stretch.
- Support for asset classes beyond liquid US-listed equities and ETFs — derivatives and international remain a stretch. Fixed income and commodities are **no longer** excluded: the universe carries seven bond funds and a gold ETF, because asset-class bands and a conservative mandate's bond floor are unenforceable without them.

## Architecture (built, as of Phase 6)

```
React + Vite ── constraint builder (server-locked ranges) + live agent-progress
        │       view (SSE) + results dashboard (Recharts)
        │
        ├── Clerk — required only to START a run; all reads public. Off entirely
        │            when no key is configured
        │
FastAPI ─┬─ Allocation Agent (Claude) — proposes weights from mandate
         ├─ Verification Agent — custom backtest engine (pandas/numpy)
         ├─ Screening Agent (Claude + RAG: BM25 + entity linkage) — qualitative exclusions
         ├─ Constraint-Compliance Agent — deterministic rules engine, no LLM
         ├─ Synthesis Agent (Claude, supervisor) — loop-back authority + final memo
         ├─ Redis + RQ — job queue for the whole run (not the backtest; see TECH_STACK.md)
         ├─ Progress bus (Redis pub/sub + capped replay) → SSE
         ├─ Per-call agent traces (prompt, response, tokens, cost, latency)
         └─ Eval harness (backtest-math correctness + compliance precision/recall)
                │
        Postgres (mandates, run history, agent traces, eval results, ESG corpus,
                  append-only price cache)
        yfinance (price data) + hand-authored ESG corpus (RAG)
```
