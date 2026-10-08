import { useEffect, useState } from "react";
import { api } from "../api";
import { VERDICT_LABEL, localTime } from "../format";
import type { LogResponse, Verdict } from "../types";

export function TransparencyLog({ jobId }: { jobId: string }) {
  const [log, setLog] = useState<LogResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [check, setCheck] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let live = true;
    api.getLog(jobId).then((l) => live && setLog(l)).catch((e: Error) => live && setError(e.message));
    return () => {
      live = false;
    };
  }, [jobId]);

  const verify = async () => {
    setBusy(true);
    try {
      const r = await api.verifyLog(jobId);
      setCheck(r.chain_valid ? `Chain verified: all ${r.entries} entries recomputed correctly.` : `Chain BROKEN at entry #${r.first_invalid_seq}.`);
    } catch (e) {
      setCheck(e instanceof Error ? e.message : "Could not verify");
    } finally {
      setBusy(false);
    }
  };

  if (error) return <div className="notice error">{error}</div>;
  if (!log) return <div className="muted">Loading log…</div>;

  return (
    <div>
      <div className="log-head card">
        <div>
          <strong>{log.entries.length}</strong> recorded actions ·{" "}
          <span className={log.chain_valid ? "chain-ok" : "chain-bad"}>{log.chain_valid ? "hash chain intact" : `chain broken at #${log.first_invalid_seq}`}</span>
          <div className="small muted mono">Final hash: {log.final_hash ?? "n/a"}</div>
          {check && <div className="small">{check}</div>}
        </div>
        <div className="btn-row">
          <button className="btn" onClick={verify} disabled={busy}>Re-verify chain</button>
          <button className="btn" onClick={() => void api.downloadLog(jobId, "pdf")}>Export log (PDF)</button>
          <button className="btn" onClick={() => void api.downloadLog(jobId, "csv")}>Export log (CSV)</button>
        </div>
      </div>
      <p className="muted small">
        Each row's SHA-256 hash covers its own content and the previous row's hash, so changing or deleting any earlier entry invalidates everything after it.
        Attach the PDF as an appendix to show when and against which sources your citations were checked.
      </p>
      <div className="table-wrap">
        <table className="data log-table">
          <thead>
            <tr><th>#</th><th>Time</th><th>Cit.</th><th>Source</th><th>Query sent</th><th>Response</th><th>Verdict</th><th>Hash</th></tr>
          </thead>
          <tbody>
            {log.entries.map((e) => (
              <tr key={e.seq}>
                <td>{e.seq}</td>
                <td title={e.timestamp}>{localTime(e.timestamp)}</td>
                <td>{e.citation_index ?? ""}</td>
                <td>{e.source}</td>
                <td className="mono small">{e.query}</td>
                <td>{e.outcome}</td>
                <td>{e.verdict ? <span className={`badge verdict-${e.verdict}`}>{VERDICT_LABEL[e.verdict as Verdict] ?? e.verdict}</span> : ""}</td>
                <td className="mono small" title={e.entry_hash}>{e.entry_hash.slice(0, 10)}…</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
