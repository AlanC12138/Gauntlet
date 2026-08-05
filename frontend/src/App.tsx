import { useEffect, useState } from "react";
import { api, ApiError, type MandateOption, type RunSummary } from "./api";
import { AuthControls, useToken, WhenCanRun } from "./auth";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { ProgressTimeline } from "./components/ProgressTimeline";
import { Results } from "./components/Results";
import { useRunEvents } from "./useRunEvents";
import "./App.css";

export default function App() {
  const [mandates, setMandates] = useState<MandateOption[]>([]);
  const [selected, setSelected] = useState<string>("balanced");
  const [runId, setRunId] = useState<string | null>(null);
  const [recent, setRecent] = useState<RunSummary[]>([]);
  const [startError, setStartError] = useState<string | null>(null);

  const { events, result, error } = useRunEvents(runId);
  const { getToken } = useToken();

  useEffect(() => {
    api.listMandates().then(setMandates).catch(() => setMandates([]));
    api.listRuns().then(setRecent).catch(() => setRecent([]));
  }, []);

  // Refresh history once a run finishes, so the list reflects it without a reload.
  useEffect(() => {
    if (result) api.listRuns().then(setRecent).catch(() => undefined);
  }, [result]);

  async function start() {
    setStartError(null);
    try {
      const { run_id } = await api.startRun({ mandate_name: selected }, await getToken());
      setRunId(run_id);
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        setStartError("Your session expired. Sign in again to start a run.");
      } else if (err instanceof ApiError && err.status === 503) {
        setStartError(
          "The job queue or database is unavailable. Is `docker compose up -d` running?",
        );
      } else {
        setStartError(err instanceof Error ? err.message : String(err));
      }
    }
  }

  const running = runId !== null && result === null && error === null;

  return (
    <div className="app">
      <header>
        <div className="header-row">
          <h1>Gauntlet</h1>
          <AuthControls />
        </div>
        <p className="tagline">
          Multi-agent portfolio construction. An allocation is proposed, then verified
          by execution — backtested, checked against deterministic rules, and screened
          before a supervising agent signs off or sends it back.
        </p>
      </header>

      <section className="controls">
        <label htmlFor="mandate">Mandate</label>
        <select
          id="mandate"
          value={selected}
          onChange={(e) => setSelected(e.target.value)}
          disabled={running}
        >
          {/* Keyed by `key`, not `risk_profile` — three demo mandates share a
              risk profile, so keying off it silently starts the wrong run. */}
          {mandates.map((m) => (
            <option key={m.key} value={m.key}>
              {m.mandate.name} ({m.mandate.risk_profile})
            </option>
          ))}
        </select>
        {/* Browsing is open; only starting a run needs an account, because only
            starting a run spends money. */}
        <WhenCanRun
          fallback={
            <span className="muted">
              Sign in to start a run — everything below stays open to browse.
            </span>
          }
        >
          <button onClick={start} disabled={running}>
            {running ? "Running…" : "Run the gauntlet"}
          </button>
          <span className="muted">A run takes 1–3 minutes and costs roughly $0.20.</span>
        </WhenCanRun>
      </section>

      {startError && <p className="error">{startError}</p>}
      {error && <p className="error">Lost the progress stream: {error}</p>}

      {/* Hidden for a historical run with no stored events — progress expires
          after 24h while reports are kept, and "waiting for the worker" beside a
          finished report reads as a stuck run. */}
      {runId && (running || events.length > 0) && (
        <section className="panel">
          <h2>{running ? "Live progress" : "How this run went"}</h2>
          <ProgressTimeline events={events} />
        </section>
      )}

      {result?.report && (
        <ErrorBoundary label="The results dashboard">
          <Results report={result.report} />
        </ErrorBoundary>
      )}

      {result && !result.report && result.status === "failed" && (
        <p className="error">Run failed: {result.error}</p>
      )}

      {recent.length > 0 && !runId && (
        <section className="panel">
          <h2>Recent runs</h2>
          <table>
            <thead>
              <tr>
                <th>Started</th>
                <th>Status</th>
                <th>Verdict</th>
                <th className="num">Iterations</th>
                <th className="num">Cost</th>
              </tr>
            </thead>
            <tbody>
              {recent.map((r) => (
                // Focusable with a key handler, not just an onClick: a row that
                // only responds to the mouse is unreachable by keyboard.
                <tr
                  key={r.run_id}
                  onClick={() => setRunId(r.run_id)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      setRunId(r.run_id);
                    }
                  }}
                  tabIndex={0}
                  role="button"
                  aria-label={`Open run from ${new Date(r.created_at).toLocaleString()}`}
                  className="clickable"
                >
                  <td>{new Date(r.created_at).toLocaleString()}</td>
                  <td>{r.status}</td>
                  {/* Status and verdict are separate on purpose: a completed run
                      can be unapproved, which is the governance loop working. */}
                  <td className={r.approved ? "good" : r.approved === false ? "warn" : ""}>
                    {r.approved === null ? "—" : r.approved ? "approved" : "not approved"}
                  </td>
                  <td className="num">{r.iterations ?? "—"}</td>
                  <td className="num">
                    {r.cost_usd === null ? "—" : `$${r.cost_usd.toFixed(2)}`}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}
    </div>
  );
}
