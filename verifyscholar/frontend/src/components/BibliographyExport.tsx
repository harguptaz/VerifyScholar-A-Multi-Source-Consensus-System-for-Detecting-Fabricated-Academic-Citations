import { useState } from "react";
import { api } from "../api";
import { useExportList } from "../exportList";
import type { ExportFormat, Style } from "../types";

interface Props {
  jobId?: string | null;
  onError?: (message: string) => void;
}

/** Export the corrected reference list: a job's verified citations plus the session export list (F35 + F36). */
export function BibliographyExport({ jobId }: Props) {
  const list = useExportList();
  const [format, setFormat] = useState<ExportFormat>("text");
  const [style, setStyle] = useState<Style>("apa");
  const [includeSuspicious, setIncludeSuspicious] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const nothing = !jobId && list.items.length === 0;

  const run = async () => {
    setBusy(true);
    setError(null);
    try {
      await api.exportBibliography({
        job_id: jobId ?? undefined,
        extra_papers: list.items,
        format,
        style,
        include_suspicious: includeSuspicious,
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : "Export failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="export-box">
      <div className="export-controls">
        <label>
          Format
          <select value={format} onChange={(e) => setFormat(e.target.value as ExportFormat)}>
            <option value="text">Formatted text</option>
            <option value="bibtex">BibTeX (.bib)</option>
            <option value="ris">RIS (.ris)</option>
            <option value="csv">CSV</option>
          </select>
        </label>
        <label>
          Style
          <select value={style} onChange={(e) => setStyle(e.target.value as Style)} disabled={format === "bibtex" || format === "ris"}>
            <option value="apa">APA</option>
            <option value="ieee">IEEE</option>
            <option value="harvard">Harvard</option>
            <option value="vancouver">Vancouver</option>
            <option value="mla">MLA</option>
          </select>
        </label>
        {jobId && (
          <label className="check">
            <input type="checkbox" checked={includeSuspicious} onChange={(e) => setIncludeSuspicious(e.target.checked)} />
            Include suspicious citations (marked for review)
          </label>
        )}
        <button className="btn primary" onClick={run} disabled={busy || nothing}>
          {busy ? "Preparing…" : "Export bibliography"}
        </button>
      </div>
      <p className="muted small">
        {jobId ? "Includes this job's verified citations (built from the verified source records), " : "Includes "}
        {list.items.length} paper{list.items.length === 1 ? "" : "s"} from your export list.
        {jobId && " Ghost citations are left out and listed under “Removed”. Retracted papers are flagged."}
        {(style === "ieee" || style === "vancouver") && " Numbered styles keep your original citation order."}
      </p>
      {error && <div className="notice error">{error}</div>}
    </div>
  );
}
