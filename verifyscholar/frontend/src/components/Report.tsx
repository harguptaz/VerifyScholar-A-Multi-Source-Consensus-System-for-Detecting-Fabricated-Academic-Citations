import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api";
import { DOI_STATUS_LABEL, JOURNAL_STATUS_LABEL, RELEVANCE_LABEL } from "../format";
import type { CitationResult, JobResult, Verdict } from "../types";
import { BibliographyExport } from "./BibliographyExport";
import { DetailPanel } from "./DetailPanel";
import { Modal } from "./Modal";
import { RetractionBadge, ScoreBar, VerdictBadge } from "./Badges";
import { TransparencyLog } from "./TransparencyLog";

type Filter = "all" | Verdict | "retracted";
type Sort = "index" | "verdict" | "score";
const RANK: Record<Verdict, number> = { ghost: 0, suspicious: 1, inconclusive: 2, verified: 3 };

interface Props {
  jobId: string;
  result: JobResult;
  onOpenFinder: (kind: "lookup" | "search", q: string) => void;
}

export function Report({ jobId, result, onOpenFinder }: Props) {
  const [tab, setTab] = useState<"results" | "log">("results");
  const [filter, setFilter] = useState<Filter>("all");
  const [sort, setSort] = useState<Sort>("verdict");
  const [selected, setSelected] = useState<number | null>(null);
  const [showExport, setShowExport] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const s = result.summary;
  const hasRelevance = result.results.some((r) => r.relevance && r.relevance.status === "assessed");
  const isDocument = !!result.document;

  const rows = useMemo(() => {
    let out: CitationResult[] = result.results.filter((r) => (filter === "all" ? true : filter === "retracted" ? r.retraction.status === "retracted" : r.verdict === filter));
    out = [...out].sort((a, b) => {
      if (sort === "index") return a.index - b.index;
      if (sort === "score") return (a.confidence ?? 101) - (b.confidence ?? 101);
      return RANK[a.verdict] - RANK[b.verdict] || a.index - b.index;
    });
    return out;
  }, [result.results, filter, sort]);

  const selectedResult = result.results.find((r) => r.index === selected) ?? null;

  // prev / next follow the table as currently filtered and sorted
  const pos = selected == null ? -1 : rows.findIndex((r) => r.index === selected);
  const prevIndex = pos > 0 ? rows[pos - 1].index : null;
  const nextIndex = pos >= 0 && pos < rows.length - 1 ? rows[pos + 1].index : null;

  // On close, put keyboard focus back on the row that is now highlighted (it may differ from the one
  // that was opened if the user stepped through citations).
  const detailButtons = useRef(new Map<number, HTMLButtonElement>());
  const lastShown = useRef<number | null>(null);
  useEffect(() => {
    if (selected != null) {
      lastShown.current = selected;
    } else if (lastShown.current != null) {
      detailButtons.current.get(lastShown.current)?.focus();
      lastShown.current = null;
    }
  }, [selected]);

  const closeDetail = useCallback(() => setSelected(null), []);
  // "Open in Paper Finder" / "Find a replacement" switch tabs, so the modal must not stay on top
  const openFinderFromDetail = useCallback((kind: "lookup" | "search", q: string) => {
    lastShown.current = null;
    setSelected(null);
    onOpenFinder(kind, q);
  }, [onOpenFinder]);
  const download = async (fmt: "csv" | "pdf") => {
    setError(null);
    try {
      await api.downloadReport(jobId, fmt);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Download failed");
    }
  };

  const chips: { key: Filter; label: string; n: number }[] = [
    { key: "all", label: "All", n: s.total },
    { key: "ghost", label: "Ghost", n: s.ghost },
    { key: "suspicious", label: "Suspicious", n: s.suspicious },
    { key: "verified", label: "Verified", n: s.verified },
    ...(s.inconclusive ? [{ key: "inconclusive" as Filter, label: "Inconclusive", n: s.inconclusive }] : []),
    ...(s.retracted ? [{ key: "retracted" as Filter, label: "Retracted", n: s.retracted }] : []),
  ];

  return (
    <section className="report">
      <div className="report-top">
        <div>
          <h2>Verification report</h2>
          <p className="summary-row">
            <strong>{s.total} citations checked</strong> — <span className="t-verified">{s.verified} Verified</span>, <span className="t-suspicious">{s.suspicious} Suspicious</span>,{" "}
            <span className="t-ghost">{s.ghost} Ghost</span>
            {s.inconclusive > 0 && <>, <span className="t-inconclusive">{s.inconclusive} Inconclusive</span></>}
            {s.retracted > 0 && <> · <span className="t-ghost">{s.retracted} retracted</span></>}
            <span className="muted small"> · {s.elapsed_seconds}s</span>
          </p>
        </div>
        <div className="btn-row">
          <button className="btn" onClick={() => void download("csv")}>Export report (CSV)</button>
          <button className="btn" onClick={() => void download("pdf")}>Export report (PDF)</button>
          <button className={showExport ? "btn primary" : "btn"} onClick={() => setShowExport((v) => !v)}>Export bibliography…</button>
        </div>
      </div>

      {error && <div className="notice error">{error}</div>}
      {showExport && <BibliographyExport jobId={jobId} />}
      {result.warnings.map((w, i) => <div key={i} className="notice warn">{w}</div>)}

      {isDocument && result.document && (
        <div className="notice info">
          <strong>{result.document.title || result.document.filename || "Uploaded document"}</strong> — {result.document.n_references} references found.
          {result.document.orphan_indices.length > 0 && <> Never cited in the text: {result.document.orphan_indices.map((i) => `#${i}`).join(", ")}.</>}
          {result.document.unmatched_markers.length > 0 && <> In-text markers with no matching reference: {result.document.unmatched_markers.slice(0, 8).join(", ")}.</>}
          {result.document.embedding_backend === "tfidf" && hasRelevance && <> Relevance used the simple word-overlap fallback; install the optional sentence-transformers package for meaning-based scores.</>}
        </div>
      )}

      <div className="tabs">
        <button className={tab === "results" ? "tab active" : "tab"} onClick={() => setTab("results")}>Results</button>
        <button className={tab === "log" ? "tab active" : "tab"} onClick={() => setTab("log")}>Source transparency log</button>
      </div>

      {tab === "log" ? (
        <TransparencyLog jobId={jobId} />
      ) : (
        <div className="report-body">
          <div className="report-main">
            <div className="toolbar">
              <div className="chips">
                {chips.map((c) => (
                  <button key={c.key} className={filter === c.key ? "chip active" : "chip"} onClick={() => setFilter(c.key)}>{c.label} ({c.n})</button>
                ))}
              </div>
              <label className="sort">
                Sort by
                <select value={sort} onChange={(e) => setSort(e.target.value as Sort)}>
                  <option value="verdict">Verdict (reds first)</option>
                  <option value="index">Citation number</option>
                  <option value="score">Confidence (lowest first)</option>
                </select>
              </label>
            </div>
            <div className="table-wrap">
              <table className="data report-table">
                <thead>
                  <tr>
                    <th>#</th><th>Citation (as submitted)</th><th>Verdict</th><th>Score</th><th>DOI status</th><th>Journal status</th>
                    {isDocument && <th>Relevance</th>}<th>Action</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r) => (
                    <tr key={r.index} className={r.index === selected ? "selected" : ""} onClick={() => setSelected(r.index)}>
                      <td>{r.index}</td>
                      <td className="cit-cell"><div className="clamp2" title={r.raw}>{r.raw}</div></td>
                      <td><div className="badge-stack"><VerdictBadge verdict={r.verdict} score={r.confidence} /><RetractionBadge info={r.retraction} /></div></td>
                      <td><ScoreBar score={r.confidence} /></td>
                      <td>{DOI_STATUS_LABEL[r.doi_status]}</td>
                      <td>{JOURNAL_STATUS_LABEL[r.journal_status]}</td>
                      {isDocument && (
                        <td className="small">
                          {r.relevance?.orphan ? <span className="chip src-no_record">Never cited</span> : r.relevance?.label ? RELEVANCE_LABEL[r.relevance.label] : <span className="muted">—</span>}
                        </td>
                      )}
                      <td>
                        <button
                          className="link"
                          ref={(el) => { if (el) detailButtons.current.set(r.index, el); else detailButtons.current.delete(r.index); }}
                          onClick={(e) => { e.stopPropagation(); setSelected(r.index); }}
                        >
                          View detail
                        </button>
                      </td>
                    </tr>
                  ))}
                  {rows.length === 0 && <tr><td colSpan={isDocument ? 8 : 7} className="muted center">No citations match this filter.</td></tr>}
                </tbody>
              </table>
            </div>
          </div>
          {selectedResult && (
            <Modal label={`Citation ${selectedResult.index} detail`} onClose={closeDetail}>
              <DetailPanel
                result={selectedResult}
                onClose={closeDetail}
                onOpenFinder={openFinderFromDetail}
                position={pos >= 0 ? { current: pos + 1, total: rows.length } : undefined}
                onPrev={prevIndex != null ? () => setSelected(prevIndex) : undefined}
                onNext={nextIndex != null ? () => setSelected(nextIndex) : undefined}
              />
            </Modal>
          )}
        </div>
      )}
    </section>
  );
}
