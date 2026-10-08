import { authorsLine } from "../format";
import { useExportList } from "../exportList";
import { BibliographyExport } from "./BibliographyExport";

export function ExportListPanel({ lastJobId }: { lastJobId: string | null }) {
  const list = useExportList();
  return (
    <div className="card">
      <h2>Export list</h2>
      <p className="muted">
        Papers you add from the Paper Finder or a verification report collect here. The list lives in this browser tab only, so export it before you close the tab.
      </p>
      {list.items.length === 0 ? (
        <p className="muted">Nothing here yet. Use “Add to export list” on any paper.</p>
      ) : (
        <ul className="paper-list">
          {list.items.map((r, i) => (
            <li key={`${r.doi ?? r.title}-${i}`} className="paper-item">
              <div className="paper-main">
                <strong>{r.title}</strong>
                <div className="small">{authorsLine(r.authors, 4)}{r.year ? ` (${r.year})` : ""}</div>
                <div className="muted small">{r.journal}{r.doi ? ` · ${r.doi}` : ""}</div>
              </div>
              <div className="paper-actions"><button className="btn small-btn" onClick={() => list.remove(r)}>Remove</button></div>
            </li>
          ))}
        </ul>
      )}
      {list.items.length > 0 && <button className="link" onClick={list.clear}>Clear list</button>}
      <h3>Export</h3>
      <BibliographyExport jobId={lastJobId} />
    </div>
  );
}
