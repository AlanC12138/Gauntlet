# Recording the demo

Phase 7. This is the one part of the project that cannot be automated — it needs
a screen recorder and a person. What follows is the shot list, worked out from
what the system actually does rather than from what would be nice to show.

## The problem to design around

**A run takes one to three minutes, and the interesting one takes five.** Nobody
watches that. So the recording cannot be "press go and wait" — the wait has to be
cut, and the payoff has to be a stored run you can open instantly.

That gives two options, and the second is better:

1. **Live run, edited.** Start a run, cut the dead time, land on the result.
   Honest and kinetic, but you are editing around a five-minute gap.
2. **Live start, then cut to a stored run.** Show the loop *beginning* live —
   which proves it is real — then cut to `9c3b8fb5`, already complete with full
   traces, and explore it at speed. **Recommended.**

## What actually makes this project interesting

Lead with the thing no other "AI portfolio manager" demo can show: **the
supervisor rejecting a proposal and sending it back.** Not the charts. Charts are
table stakes; a governance loop that fires is not.

## Shot list (target: 90 seconds)

| # | Shot | Say |
|---|---|---|
| 1 | Landing page, run history | "Five agents. An allocation is proposed, then verified by execution — nothing is taken on faith." |
| 2 | Drag **Max position weight** 10% → 5% | "Every bound here is computed server-side by the same engine that enforces it. Watch the other controls move." |
| 3 | Keep dragging to ~1.5% — max-holdings greys out, problems list, Run disables | "Unachievable combinations lock rather than failing at run time. It refuses to spend money on an impossible mandate." |
| 4 | Reset, pick **Conservative Income**, hit Run | "This one has a 15% drawdown tolerance — a constraint you cannot check until after you have backtested." |
| 5 | Live timeline streaming | "Server-sent events. Allocation proposes, verification backtests, compliance checks, screening reads the ESG corpus, the supervisor decides." |
| 6 | **Cut** to stored run `9c3b8fb5` | — |
| 7 | Timeline showing **"Iteration 2 ← sent back for revision"** | "Drawdown came in at 17.8% against a 15% tolerance. Compliance is deterministic and binding — the supervisor cannot approve over it." |
| 8 | Verdict: **Not approved**, 3 iterations | "Three attempts, still outside tolerance, so it returns unapproved. That is the loop working, not failing." |
| 9 | Expand **Agent traces** → iteration 2, Allocation Agent | "And this is what the proposer was actually told." |
| 10 | Scroll the prompt to *Revision required* | Read it aloud — it is the strongest 10 seconds in the video. |
| 11 | Trace header row: 35s → 103s → 63s | "The revision costs three times the first attempt, because the agent is aiming at a target it has no tool to measure. That is a known gap, and it shows up in the latency." |

## Setup

```
docker compose up -d
poetry run uvicorn gauntlet.api:app --port 8000
poetry run gauntlet worker
npm run dev --prefix frontend
```

- Sign in **before** recording; the sign-in flow is not part of the story.
- Widen the browser to ~1280px so the charts and the constraint grid lay out.
- Check the run history contains the three runs below before you start.

## Runs to record against

| Run | Why |
|---|---|
| `9c3b8fb5` | **The one.** 3 iterations, **not approved**, $0.63, full traces attached |
| `fde782c9` | 2 iterations, approved — the loop firing *and* resolving |
| `645d1211` | Edited constraints: 5% cap / 30 holdings → 30 holdings, largest exactly 5.00% |

If those have been cleared, regenerate the first with `Conservative Income`
unedited — it is the mandate whose drawdown tolerance the proposer reliably
misses on the first attempt.

## Where the file goes

An animated GIF in the README under **Status**, or an MP4 linked from it. Keep it
under ~10 MB or GitHub will be slow to render it; 12–15 fps is plenty for UI.

## What not to do

- **Do not narrate the architecture over a still frame.** The README already does
  that better than a voiceover can.
- **Do not show a passing run first.** A run approved on iteration 1 looks
  identical to a system with no governance at all.
- **Do not speed up the trace section.** It is the only part that cannot be
  inferred from a screenshot.
