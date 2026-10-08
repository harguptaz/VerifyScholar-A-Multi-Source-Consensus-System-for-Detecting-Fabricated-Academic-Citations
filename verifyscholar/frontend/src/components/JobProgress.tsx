import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import type { JobStatus } from "../types";

interface Props {
  jobId: string;
  onFinished: (job: JobStatus) => void;
}

/** Live progress for a running job: Server-Sent Events, falling back to polling if the stream breaks. */
export function JobProgress({ jobId, onFinished }: Props) {
  const [state, setState] = useState({ stage: "Starting…", completed: 0, total: 0 });
  const callback = useRef(onFinished);
  callback.current = onFinished;

  useEffect(() => {
    let finished = false;
    let timer: number | undefined;
    let source: EventSource | null = null;

    const finish = async () => {
      if (finished) return;
      finished = true;
      source?.close();
      window.clearInterval(timer);
      try {
        callback.current(await api.getJob(jobId));
      } catch (e) {
        callback.current({ id: jobId, mode: "", status: "error", stage: "", total: 0, completed: 0, error: e instanceof Error ? e.message : "Could not read the job result." });
      }
    };

    const startPolling = () => {
      if (timer !== undefined || finished) return;
      timer = window.setInterval(async () => {
        try {
          const j = await api.getJob(jobId);
          setState({ stage: j.stage, completed: j.completed, total: j.total });
          if (j.status === "done" || j.status === "error") void finish();
        } catch {
          /* keep trying */
        }
      }, 1500);
    };

    try {
      source = new EventSource(api.streamUrl(jobId));
      source.addEventListener("progress", (e) => {
        const d = JSON.parse((e as MessageEvent<string>).data) as { stage: string; completed: number; total: number };
        setState({ stage: d.stage, completed: d.completed, total: d.total });
      });
      source.addEventListener("done", () => void finish());
      source.addEventListener("failed", () => void finish());
      source.onerror = () => {
        source?.close();
        source = null;
        startPolling();
      };
    } catch {
      startPolling();
    }

    return () => {
      finished = true;
      source?.close();
      window.clearInterval(timer);
    };
  }, [jobId]);

  const pct = state.total > 0 ? Math.round((state.completed / state.total) * 100) : 5;
  return (
    <div className="card progress-card" role="status" aria-live="polite">
      <div className="progress-text">{state.stage}</div>
      <div className="progress-track">
        <div className="progress-fill" style={{ width: `${Math.max(5, pct)}%` }} />
      </div>
      {state.total > 0 && (
        <div className="muted small">
          {state.completed} of {state.total} citations checked
        </div>
      )}
    </div>
  );
}
