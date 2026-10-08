import { useState } from "react";
import { api } from "../api";
import type { SourcesHealthResponse } from "../types";

const NAMES: Record<string, string> = { crossref: "CrossRef", openalex: "OpenAlex", doaj: "DOAJ" };

/** On-demand test of the backend's connection to the data sources, with the reason when one fails. */
export function ConnectionCheck() {
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<SourcesHealthResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  const run = async () => {
    setBusy(true);
    setError(null);
    try {
      setResult(await api.sourcesHealth());
    } catch (e) {
      setResult(null);
      setError(e instanceof Error ? e.message : "The test could not be run");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="conn-check">
      <button className="btn small-btn" onClick={run} disabled={busy}>{busy ? "Testing…" : "Test connection to data sources"}</button>
      {error && <div className="notice error">{error}</div>}
      {result && (
        <ul className="conn-list">
          {Object.entries(result).map(([key, v]) => (
            <li key={key} className={v.ok ? "conn-ok" : "conn-bad"}>
              <strong>{v.ok ? "✓" : "✗"} {NAMES[key] ?? key}</strong>: {v.detail}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
