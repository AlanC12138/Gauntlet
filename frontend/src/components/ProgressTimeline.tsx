import type { ProgressEvent } from "../api";

/**
 * The live view of the governance loop.
 *
 * Grouped by iteration rather than shown as one flat log, because a second
 * iteration is the single most interesting thing this system does — it means the
 * supervisor rejected a proposal and sent it back. A flat list would bury that
 * as just more lines scrolling past.
 */

const STAGE_LABELS: Record<string, string> = {
  run: "Run",
  iteration: "Iteration",
  allocation: "Allocation Agent",
  verification: "Verification Agent",
  compliance: "Compliance Agent",
  screening: "Screening Agent",
  synthesis: "Synthesis Agent",
};

const TERMINAL_STATUSES = new Set([
  "finished",
  "approved",
  "completed",
  "violated",
  "flagged",
  "revise",
  "rejected",
  "failed",
  "truncated",
]);

function statusClass(status: string): string {
  if (status === "started") return "running";
  if (["approved", "finished", "completed"].includes(status)) return "good";
  if (["violated", "revise", "flagged"].includes(status)) return "warn";
  if (["failed", "rejected", "truncated"].includes(status)) return "bad";
  return "";
}

export function ProgressTimeline({ events }: { events: ProgressEvent[] }) {
  if (events.length === 0) {
    return <p className="muted">Waiting for the worker to pick up the run…</p>;
  }

  // A stage is "in flight" until a terminal status for it arrives.
  const iterations = new Map<number, ProgressEvent[]>();
  for (const event of events) {
    if (event.stage === "run") continue;
    const list = iterations.get(event.iteration) ?? [];
    list.push(event);
    iterations.set(event.iteration, list);
  }

  const runEvents = events.filter((e) => e.stage === "run");
  const finished = runEvents.find((e) => TERMINAL_STATUSES.has(e.status));

  return (
    <div className="timeline">
      {[...iterations.entries()].map(([index, iterationEvents]) => (
        <section key={index} className="iteration">
          <h3>
            Iteration {index}
            {index > 1 && (
              <span className="loopback" title="The Synthesis Agent rejected the previous proposal">
                ← sent back for revision
              </span>
            )}
          </h3>
          <ol>
            {iterationEvents
              .filter((e) => e.stage !== "iteration")
              .map((event) => (
                <li key={event.seq} className={statusClass(event.status)}>
                  <span className="stage">{STAGE_LABELS[event.stage] ?? event.stage}</span>
                  <span className="status">{event.status}</span>
                  <span className="detail">{event.detail}</span>
                </li>
              ))}
          </ol>
        </section>
      ))}
      {finished && (
        <p className={`run-final ${statusClass(finished.status)}`}>{finished.detail}</p>
      )}
    </div>
  );
}
