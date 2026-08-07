import { useEffect, useRef, useState } from "react";
import { api, type ProgressEvent, type RunStatus } from "./api";

/**
 * Follows a run's stage events over SSE, then fetches the finished report.
 *
 * The server replays events a late subscriber missed, so this does not need to
 * connect before the run starts — mounting mid-run still renders the full
 * history. Events carry a monotonic `seq`; deduping on it means a reconnect
 * cannot double-render a stage.
 *
 * EventSource is deliberately not used. It cannot send headers, which would make
 * attaching a Clerk bearer token impossible without moving the token into a
 * query string — where it would land in access logs. `fetch` with a streaming
 * body reader costs a little parsing and keeps auth in a header.
 */
export function useRunEvents(runId: string | null) {
  const [events, setEvents] = useState<ProgressEvent[]>([]);
  const [result, setResult] = useState<RunStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const seen = useRef<Set<number>>(new Set());

  useEffect(() => {
    // Cleared before the early return, not after: navigating back to the
    // builder sets runId to null, and leaving the previous run's report in
    // state would render a stale result beside a fresh constraint set.
    setEvents([]);
    setResult(null);
    setError(null);
    seen.current = new Set();

    if (!runId) return;

    const controller = new AbortController();

    (async () => {
      try {
        // Check first whether the run has already finished. Progress events
        // expire after 24 hours while the report is kept forever, so opening a
        // stream for an old run would wait on a terminal event that no longer
        // exists — the run would render as permanently "in progress".
        const existing = await api.getRun(runId);
        if (existing.status === "completed" || existing.status === "failed") {
          setResult(existing);
          return;
        }

        const response = await fetch(`${api.base}/api/runs/${runId}/events`, {
          signal: controller.signal,
          headers: { Accept: "text/event-stream" },
        });
        if (!response.body) throw new Error("No response body");

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });

          // SSE frames are separated by a blank line. Anything after the last
          // separator is a partial frame and stays buffered.
          const frames = buffer.split("\n\n");
          buffer = frames.pop() ?? "";

          for (const frame of frames) {
            const data = frame
              .split("\n")
              .find((line) => line.startsWith("data:"));
            if (!data) continue; // heartbeat comment
            const event = JSON.parse(data.slice(5)) as ProgressEvent;
            if (seen.current.has(event.seq)) continue;
            seen.current.add(event.seq);
            setEvents((prev) => [...prev, event]);
          }
        }

        // The stream closes on the terminal event, which the worker publishes
        // only after the report is committed — so this read cannot race the write.
        setResult(await api.getRun(runId));
      } catch (err) {
        if (controller.signal.aborted) return;
        setError(err instanceof Error ? err.message : String(err));
      }
    })();

    return () => controller.abort();
  }, [runId]);

  return { events, result, error };
}
