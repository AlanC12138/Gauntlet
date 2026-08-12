import { useEffect, useState } from "react";
import { api, type AgentCall } from "../api";

/**
 * The traces for a run: what each agent was asked, and what it emitted.
 *
 * This is the panel that makes a loop-back *showable* rather than merely
 * assertable. The results dashboard says the Synthesis Agent sent a proposal
 * back; only the trace shows the feedback it sent, and the revised prompt the
 * Allocation Agent then received.
 *
 * Collapsed by default and grouped by iteration. Prompts run to several thousand
 * characters — useful to read deliberately, hostile as a wall of text above the
 * charts.
 */

const ms = (n: number | null) => (n === null ? "—" : n >= 1000 ? `${(n / 1000).toFixed(1)}s` : `${n}ms`);

function Call({ call }: { call: AgentCall }) {
  const [open, setOpen] = useState(false);

  return (
    <li className="trace-call">
      <button className="trace-head" onClick={() => setOpen(!open)} aria-expanded={open}>
        <span className="trace-caret">{open ? "▾" : "▸"}</span>
        <span className="trace-agent">{call.agent}</span>
        <span className="trace-meta">
          {call.input_tokens.toLocaleString()} in / {call.output_tokens.toLocaleString()} out
          {" · "}
          {ms(call.latency_ms)}
          {call.cost_usd !== null && ` · $${call.cost_usd.toFixed(4)}`}
          {/* Surfaced rather than buried: a truncated answer is the case where
              reading the raw emission matters most. */}
          {call.stop_reason && call.stop_reason !== "end_turn" && (
            <span className="warn"> · {call.stop_reason}</span>
          )}
        </span>
      </button>
      {open && (
        <div className="trace-body">
          <h5>Prompt</h5>
          <pre>{call.prompt}</pre>
          <h5>Emitted response</h5>
          <pre>{call.response ?? "(no text content)"}</pre>
        </div>
      )}
    </li>
  );
}

export function TracePanel({ runId }: { runId: string }) {
  const [calls, setCalls] = useState<AgentCall[] | null>(null);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    if (!open || calls) return;
    // Fetched only when opened — prompts are kilobytes each and most visits
    // never expand them.
    api.getTraces(runId).then(setCalls).catch(() => setCalls([]));
  }, [open, calls, runId]);

  const iterations = [...new Set((calls ?? []).map((c) => c.iteration))].sort();

  return (
    <section className="panel">
      <button className="trace-toggle" onClick={() => setOpen(!open)} aria-expanded={open}>
        {open ? "▾" : "▸"} Agent traces
      </button>
      <p className="muted">
        The report above says what each agent decided. These say what it was asked
        and what it wrote back — including, on a loop-back, the revision feedback
        the Allocation Agent was handed.
      </p>

      {open && calls === null && <p className="muted">Loading traces…</p>}
      {open && calls?.length === 0 && (
        <p className="muted">
          No traces for this run — it predates per-call tracing, or ran outside the
          worker.
        </p>
      )}

      {open &&
        iterations.map((n) => (
          <div key={n} className="trace-iteration">
            <h4>Iteration {n}</h4>
            <ul>
              {calls!
                .filter((c) => c.iteration === n)
                .map((c) => (
                  <Call key={c.id} call={c} />
                ))}
            </ul>
          </div>
        ))}
    </section>
  );
}
