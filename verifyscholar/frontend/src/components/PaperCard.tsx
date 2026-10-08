import { useEffect, useState } from "react";
import { api } from "../api";
import { authorsLine, venueLine } from "../format";
import { useExportList } from "../exportList";
import type { NetworkResponse, PaperCardData, PaperRecord, Style } from "../types";
import { OABadge, RetractionBadge, ScoreBar, VerdictBadge } from "./Badges";
import { PaperList } from "./PaperList";

type Kind = "references" | "cited-by" | "related";

function NetworkList({ openalexId, kind, onOpen }: { openalexId: string; kind: Kind; onOpen: (r: PaperRecord) => void }) {
  const [items, setItems] = useState<PaperRecord[]>([]);
  const [page, setPage] = useState(1);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = async (p: number, replace: boolean) => {
    setLoading(true);
    setError(null);
    try {
      const r: NetworkResponse = await api.network(openalexId, kind, p);
      setItems((cur) => (replace ? r.items : [...cur, ...r.items]));
      setTotal(r.total);
      setPage(p);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not load");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    setItems([]);
    void load(1, true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [openalexId, kind]);

  const label = kind === "references" ? "references" : kind === "cited-by" ? "citing papers" : "related papers";
  return (
    <div>
      <p className="muted small">{total.toLocaleString()} {label}{kind === "cited-by" ? " (most cited first)" : ""}</p>
      {error && <div className="notice error">{error}</div>}
      <PaperList items={items} onOpen={onOpen} empty={loading ? "Loading…" : `No ${label} found in OpenAlex.`} />
      {items.length < total && <button className="btn" disabled={loading} onClick={() => void load(page + 1, false)}>{loading ? "Loading…" : "Load more"}</button>}
    </div>
  );
}

interface Props {
  data: PaperCardData;
  onOpen: (r: PaperRecord) => void;
  onBack?: () => void;
}

export function PaperCard({ data, onOpen, onBack }: Props) {
  const { record: r, access } = data;
  const list = useExportList();
  const [tab, setTab] = useState<"overview" | Kind>("overview");
  const [style, setStyle] = useState<Style>("apa");
  const [formatted, setFormatted] = useState<string | null>(null);
  const [fmtError, setFmtError] = useState<string | null>(null);

  useEffect(() => {
    setTab("overview");
    setFormatted(null);
  }, [r.doi, r.openalex_id, r.title]);

  const cite = async () => {
    setFmtError(null);
    try {
      const res = await api.formatCitation(r, style);
      setFormatted(res.text);
      try {
        await navigator.clipboard.writeText(res.text);
      } catch {
        /* the text stays visible for manual copy */
      }
    } catch (e) {
      setFmtError(e instanceof Error ? e.message : "Could not format");
    }
  };

  const hasNetwork = !!r.openalex_id;
  return (
    <article className="card paper-card">
      {onBack && <button className="link" onClick={onBack}>← Back to results</button>}
      <h2>{r.title || "(untitled)"}</h2>
      <div>{authorsLine(r.authors, 8)}</div>
      <div className="muted">{venueLine(r)}</div>
      <div className="badge-stack spaced">
        <VerdictBadge verdict={data.verdict} score={data.confidence} />
        <RetractionBadge info={data.retraction} />
        <OABadge record={r} />
        {r.cited_by_count != null && <span className="badge neutral">Cited by {r.cited_by_count.toLocaleString()}</span>}
        {r.doi && <span className="badge neutral mono">{r.doi}</span>}
      </div>
      {data.confidence != null && <div className="small muted">Confidence <ScoreBar score={data.confidence} /></div>}

      {data.retraction.notices.length > 0 && (
        <div className="notice warn">
          <strong>Retraction information:</strong> {data.retraction.notices.map((n) => `${n.type.replace(/_/g, " ")} (${n.source})`).join(", ")}
          {data.retraction.notices.some((n) => n.doi) && <> · {data.retraction.notices.filter((n) => n.doi).map((n, i) => <a key={i} href={`https://doi.org/${n.doi}`} target="_blank" rel="noreferrer">notice</a>)}</>}
        </div>
      )}
      {data.verdict === "ghost" && <div className="notice warn">{data.reason}</div>}

      <div className={`access access-${access.level}`}>
        <div className="access-title">{access.level === "open_access" ? "Read it now" : access.level === "publisher_page" ? "Get the paper" : "No link available"}</div>
        <p className="small">{access.note}</p>
        <div className="btn-row">
          {access.open_access_url && <a className="btn primary" href={access.open_access_url} target="_blank" rel="noreferrer">Free full text</a>}
          {access.publisher_url && <a className={access.open_access_url ? "btn" : "btn primary"} href={access.publisher_url} target="_blank" rel="noreferrer">{access.level === "open_access" ? "Publisher page" : "Open publisher page"}</a>}
        </div>
        <p className="muted small">VerifyScholar only links to legal copies; it never hosts or proxies paywalled PDFs.</p>
      </div>

      <div className="btn-row">
        <button className="btn" onClick={() => (list.has(r) ? list.remove(r) : list.add(r))}>{list.has(r) ? "✓ In export list (click to remove)" : "Add to export list"}</button>
        <select value={style} onChange={(e) => setStyle(e.target.value as Style)} aria-label="Citation style">
          <option value="apa">APA</option><option value="ieee">IEEE</option><option value="harvard">Harvard</option><option value="vancouver">Vancouver</option><option value="mla">MLA</option>
        </select>
        <button className="btn" onClick={cite}>Copy citation</button>
      </div>
      {formatted && <div className="notice ok selectable">{formatted}</div>}
      {fmtError && <div className="notice error">{fmtError}</div>}

      <div className="tabs">
        <button className={tab === "overview" ? "tab active" : "tab"} onClick={() => setTab("overview")}>Overview</button>
        <button className={tab === "references" ? "tab active" : "tab"} disabled={!hasNetwork} onClick={() => setTab("references")}>References</button>
        <button className={tab === "cited-by" ? "tab active" : "tab"} disabled={!hasNetwork} onClick={() => setTab("cited-by")}>Cited by</button>
        <button className={tab === "related" ? "tab active" : "tab"} disabled={!hasNetwork} onClick={() => setTab("related")}>Related</button>
      </div>
      {!hasNetwork && <p className="muted small">The citation network needs an OpenAlex record, and none was found for this paper.</p>}

      {tab === "overview" ? (
        <div>
          <h4>Abstract</h4>
          <p>{r.abstract ?? <span className="muted">No abstract is available from CrossRef or OpenAlex for this paper.</span>}</p>
          <dl className="facts">
            {r.publisher && <><dt>Publisher</dt><dd>{r.publisher}</dd></>}
            {r.issn.length > 0 && <><dt>ISSN</dt><dd>{r.issn.join(", ")}</dd></>}
            {r.type && <><dt>Type</dt><dd>{r.type}</dd></>}
            {r.in_doaj != null && <><dt>In DOAJ</dt><dd>{r.in_doaj ? "Yes" : "No (DOAJ lists open-access journals only)"}</dd></>}
            <dt>Found in</dt><dd>{r.sources.join(", ") || "—"}</dd>
          </dl>
        </div>
      ) : (
        r.openalex_id && <NetworkList openalexId={r.openalex_id} kind={tab} onOpen={onOpen} />
      )}
    </article>
  );
}
