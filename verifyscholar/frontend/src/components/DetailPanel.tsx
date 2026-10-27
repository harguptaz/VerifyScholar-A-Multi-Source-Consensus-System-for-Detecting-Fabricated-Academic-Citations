import { useEffect, useMemo, useRef, useState } from "react";
import { isTypingTarget } from "../a11y";
import { api } from "../api";
import { DOI_STATUS_LABEL, FIELD_LABEL, JOURNAL_STATUS_LABEL, RELEVANCE_LABEL, VERDICT_LABEL, pct } from "../format";
import { useExportList } from "../exportList";
import type { CitationResult, SourceResult, Style } from "../types";
import { FieldStatusTag, RetractionBadge, ScoreBar, VerdictBadge } from "./Badges";

interface Props {
  result: CitationResult;
  onClose: () => void;
  onOpenFinder: (kind: "lookup" | "search", q: string) => void;
  /** Where this citation sits in the report's current filter and sort, e.g. { current: 2, total: 4 }. */
  position?: { current: number; total: number };
  /** Step to the previous / next citation; the buttons and arrow keys are disabled when omitted. */
  onPrev?: () => void;
  onNext?: () => void;
}


const KIND_TEXT: Record<string, string> = { article: "an article", preprint: "a preprint", conference: "a conference paper", book: "a book" };
const FIELD_ORDER = ["title", "authors", "journal", "year", "volume", "pages", "doi"];

function sourceStatusText(s: SourceResult): string {
  switch (s.status) {
    case "match": return "Match";
    case "partial": return "Partial match";
    case "no_record": return s.kind === "journal" ? "Not listed" : "No record";
    case "unavailable": return "Unavailable";
    default: return "Skipped";
  }
}

export function DetailPanel({ result, onClose, onOpenFinder, position, onPrev, onNext }: Props) {
  const list = useExportList();
  const [style, setStyle] = useState<Style>("apa");
  const [copied, setCopied] = useState<string | null>(null);
  const [copyError, setCopyError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const recordSources = useMemo(
    () => result.sources.filter((s) => s.kind === "record" && s.record).sort((a, b) => (b.score ?? 0) - (a.score ?? 0)),
    [result.sources],
  );
  const [tab, setTab] = useState(0);
  const active = recordSources[Math.min(tab, Math.max(0, recordSources.length - 1))];
  const record = result.record;
  const bodyRef = useRef<HTMLDivElement>(null);

  // Moving to another citation: start at the top with a fresh comparison tab and no stale "copied" text.
  useEffect(() => {
    setTab(0);
    setCopied(null);
    setCopyError(null);
    if (bodyRef.current) bodyRef.current.scrollTop = 0;
  }, [result.index]);

  // Left / right arrow keys step through citations.
  useEffect(() => {
    if (!onPrev && !onNext) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.altKey || e.ctrlKey || e.metaKey || e.shiftKey || isTypingTarget(e.target)) return;
      if (e.key === "ArrowLeft" && onPrev) { e.preventDefault(); onPrev(); }
      if (e.key === "ArrowRight" && onNext) { e.preventDefault(); onNext(); }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onPrev, onNext]);

  const copyCorrected = async () => {
    if (!record) return;
    setBusy(true);
    setCopyError(null);
    try {
      const r = await api.formatCitation(record, style);
      setCopied(r.text);
      try {
        await navigator.clipboard.writeText(r.text);
      } catch {
        setCopyError("Could not access the clipboard automatically; select the text below and copy it.");
      }
    } catch (e) {
      setCopyError(e instanceof Error ? e.message : "Could not format the citation");
    } finally {
      setBusy(false);
    }
  };

  const mismatchedDoi = recordSources.find((s) => s.doi_resolves_to)?.doi_resolves_to;
  const rel = result.relevance;

  return (
    <div className="detail">
      <header className="detail-head">
        <div>
          <span className="muted small">
            Citation #{result.index}
            {position && position.total > 1 && <> · {position.current} of {position.total}</>}
          </span>
          <div className="detail-verdict">
            <VerdictBadge verdict={result.verdict} score={result.confidence} />
            <RetractionBadge info={result.retraction} />
            {result.confidence != null && <span className="muted small">confidence {result.confidence}/100</span>}
          </div>
        </div>
        <div className="detail-nav">
          {(onPrev || onNext) && (
            <>
              <button className="icon-btn" onClick={onPrev} disabled={!onPrev} aria-label="Previous citation" title="Previous citation (←)">‹</button>
              <button className="icon-btn" onClick={onNext} disabled={!onNext} aria-label="Next citation" title="Next citation (→)">›</button>
            </>
          )}
          <button className="icon-btn" onClick={onClose} aria-label="Close details" title="Close (Esc)">×</button>
        </div>
      </header>

      <div className="detail-body" ref={bodyRef}>
      <blockquote className="raw">{result.raw}</blockquote>
      {result.parsed.kind && result.parsed.kind !== "unknown" && (
        <div className="muted small">
          Read as {KIND_TEXT[result.parsed.kind] ?? `a ${result.parsed.kind}`}
          {result.parsed.publisher ? ` (publisher: ${result.parsed.publisher})` : ""}
          {result.parsed.arxiv_id ? ` · arXiv:${result.parsed.arxiv_id}` : ""}
        </div>
      )}
      <p className="reason">{result.reason}</p>

      {result.retraction.notices.length > 0 && (
        <div className="notice warn">
          <strong>Retraction information</strong>
          <ul>
            {result.retraction.notices.map((n, i) => (
              <li key={i}>
                {n.type.replace(/_/g, " ")} <span className="muted">({n.source}{n.date ? `, ${n.date}` : ""})</span>
                {n.doi && <> · <a href={`https://doi.org/${n.doi}`} target="_blank" rel="noreferrer">notice</a></>}
              </li>
            ))}
          </ul>
        </div>
      )}

      <section>
        <h4>What you cited vs what the source says</h4>
        {recordSources.length > 1 && (
          <div className="tabs small-tabs">
            {recordSources.map((s, i) => (
              <button key={s.source} className={i === tab ? "tab active" : "tab"} onClick={() => setTab(i)}>{s.label.replace(/ (REST )?API$/, "")}</button>
            ))}
          </div>
        )}
        {active ? (
          <>
            <div className="muted small">
              Compared against {active.label} (found {active.method === "doi" ? "by DOI" : "by title search"}) · score <ScoreBar score={active.score} />
            </div>
            <table className="data compare">
              <thead><tr><th>Field</th><th>Your citation</th><th>Source record</th><th>Match?</th></tr></thead>
              <tbody>
                {FIELD_ORDER.filter((f) => active.fields[f]).map((f) => {
                  const m = active.fields[f];
                  return (
                    <tr key={f}>
                      <td>{FIELD_LABEL[f]}</td>
                      <td>{m.submitted || <span className="muted">not given</span>}</td>
                      <td>{m.found || <span className="muted">—</span>}</td>
                      <td><FieldStatusTag status={m.status} /></td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </>
        ) : (
          <p className="muted">{result.verdict === "inconclusive" ? "No comparison was possible; see the explanation above." : "No matching record was found in any of the sources that were checked, so there is nothing to compare against."}</p>
        )}
        {mismatchedDoi && (
          <div className="notice warn">
            The DOI you cited resolves to a different paper: <strong>{mismatchedDoi.title}</strong>
            {mismatchedDoi.doi && <> (<a href={`https://doi.org/${mismatchedDoi.doi}`} target="_blank" rel="noreferrer">{mismatchedDoi.doi}</a>)</>}
          </div>
        )}
      </section>

      <section>
        <h4>Per-source result</h4>
        <table className="data">
          <thead><tr><th>Source</th><th>Result</th><th>Score</th><th>How</th></tr></thead>
          <tbody>
            {result.sources.map((s) => (
              <tr key={s.source}>
                <td>{s.label}</td>
                <td><span className={`chip src-${s.status}`}>{sourceStatusText(s)}</span></td>
                <td>{s.kind === "record" ? <ScoreBar score={s.score} /> : <span className="muted">—</span>}</td>
                <td className="small muted">{s.kind === "journal" ? s.note : s.method ? (s.method === "doi" ? "DOI lookup" : "Title search") : s.note ?? ""}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <div className="muted small">DOI: {DOI_STATUS_LABEL[result.doi_status]} · Journal: {JOURNAL_STATUS_LABEL[result.journal_status]}</div>
        {result.sources.filter((s) => s.candidates && s.candidates.length > 0).map((s) => (
          <details key={s.source} className="candidates">
            <summary>{s.label}: {s.candidates!.length} closest search result{s.candidates!.length === 1 ? "" : "s"} considered</summary>
            <ul>
              {s.candidates!.map((c, i) => (
                <li key={i}>
                  <strong>{c.title || "(untitled)"}</strong> ({c.year ?? "n.d."}{c.journal ? `, ${c.journal}` : ""}) · title similarity {c.title_similarity.toFixed(2)} ·{" "}
                  {c.accepted ? <span className="t-verified">accepted as the match</span> : <span className="muted">not accepted{c.note ? `: ${c.note}` : ""}</span>}
                </li>
              ))}
            </ul>
          </details>
        ))}
      </section>

      {result.flags.length > 0 && (
        <section>
          <h4>Fabrication-pattern flags</h4>
          <ul className="flags">
            {result.flags.map((f) => (
              <li key={f.code}><span className={`sev sev-${f.severity}`}>{f.severity}</span> {f.message}</li>
            ))}
          </ul>
        </section>
      )}

      {rel && (
        <section>
          <h4>Relevance to your paper (advisory)</h4>
          {rel.orphan && <div className="notice warn">This reference is listed in the bibliography but never cited in the text.</div>}
          {rel.status === "assessed" ? (
            <>
              <div className="rel-grid">
                <div><span className="muted small">Verdict</span><div><strong>{rel.label ? RELEVANCE_LABEL[rel.label] : rel.orphan ? "Never cited" : "—"}</strong></div></div>
                <div><span className="muted small">Sentence-level similarity</span><div>{pct(rel.sentence_score)}</div></div>
                <div><span className="muted small">Paper-level similarity</span><div>{pct(rel.paper_score)}</div></div>
              </div>
              {rel.best_sentence && <blockquote className="raw">“{rel.best_sentence}”</blockquote>}
              {rel.sentences.length > 1 && (
                <details><summary>All {rel.sentences.length} citing sentences</summary><ul>{rel.sentences.map((s, i) => <li key={i}>{s}</li>)}</ul></details>
              )}
            </>
          ) : (
            <p className="muted small">{rel.reason}</p>
          )}
        </section>
      )}

      {(result.links.doi_url || result.links.landing_page || result.links.open_access) && (
        <section>
          <h4>Open the paper</h4>
          <div className="btn-row">
            {result.links.open_access && <a className="btn primary" href={result.links.open_access} target="_blank" rel="noreferrer">Free full text</a>}
            {result.links.doi_url && <a className="btn" href={result.links.doi_url} target="_blank" rel="noreferrer">DOI link</a>}
            {result.links.landing_page && result.links.landing_page !== result.links.doi_url && <a className="btn" href={result.links.landing_page} target="_blank" rel="noreferrer">Publisher page</a>}
          </div>
        </section>
      )}

      <section className="actions">
        <h4>Actions</h4>
        {record && (
          <div className="copy-row">
            <select value={style} onChange={(e) => setStyle(e.target.value as Style)} aria-label="Citation style">
              <option value="apa">APA</option><option value="ieee">IEEE</option><option value="harvard">Harvard</option>
              <option value="vancouver">Vancouver</option><option value="mla">MLA</option>
            </select>
            <button className="btn primary" onClick={copyCorrected} disabled={busy}>{busy ? "Formatting…" : "Copy corrected citation"}</button>
          </div>
        )}
        {copied && <div className="notice ok"><div className="small muted">{copyError ? "Corrected citation" : "Copied to clipboard"}</div><div className="selectable">{copied}</div></div>}
        {copyError && <div className="notice error">{copyError}</div>}
        <div className="btn-row">
          {record?.doi && <button className="btn" onClick={() => onOpenFinder("lookup", record.doi!)}>Open in Paper Finder</button>}
          {(result.verdict === "ghost" || result.verdict === "suspicious") && (
            <button className="btn" onClick={() => onOpenFinder("search", result.parsed.title || result.raw.slice(0, 150))}>Find a replacement</button>
          )}
          {record && (
            <button className="btn" onClick={() => (list.has(record) ? list.remove(record) : list.add(record))}>
              {list.has(record) ? "Remove from export list" : "Add to export list"}
            </button>
          )}
        </div>
        {record && result.verdict === "verified" && <p className="muted small">{VERDICT_LABEL.verified}: the corrected citation is built from the source record, not from your original text.</p>}
      </section>
      </div>
    </div>
  );
}