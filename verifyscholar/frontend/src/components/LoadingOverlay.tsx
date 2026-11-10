import { useEffect, useState } from "react";

export interface Pending {
  /** What is happening, e.g. "Opening paper…". */
  title: string;
  /** What it is happening to, e.g. the paper's title or the search terms. */
  detail?: string;
}

interface Props {
  pending: Pending;
  onCancel: () => void;
  /** Requests that finish faster than this never show the overlay, so quick answers don't flicker. */
  delayMs?: number;
}

/** Blurs the page below the navbar and shows a spinner while the Paper Finder waits for an answer. */
export function LoadingOverlay({ pending, onCancel, delayMs = 200 }: Props) {
  const [visible, setVisible] = useState(delayMs <= 0);

  useEffect(() => {
    if (delayMs <= 0) return;
    const t = window.setTimeout(() => setVisible(true), delayMs);
    return () => window.clearTimeout(t);
  }, [delayMs]);

  // Escape cancels, like the Cancel link
  useEffect(() => {
    if (!visible) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !document.querySelector(".modal-card")) {
        e.preventDefault();
        onCancel();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [visible, onCancel]);

  if (!visible) return null;
  return (
    <div className="loading-overlay">
      <div className="loading-card" role="status" aria-live="polite">
        <span className="spinner" aria-hidden="true" />
        <div className="loading-text">
          <div className="loading-title">{pending.title}</div>
          {pending.detail && <div className="loading-detail">“{pending.detail}”</div>}
          <button type="button" className="link loading-cancel" onClick={onCancel}>Cancel</button>
        </div>
      </div>
    </div>
  );
}
