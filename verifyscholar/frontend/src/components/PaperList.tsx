import { authorsLine, venueLine } from "../format";
import { useExportList } from "../exportList";
import type { PaperRecord } from "../types";
import { OABadge, RetractionBadge } from "./Badges";

interface Props {
  items: PaperRecord[];
  onOpen: (r: PaperRecord) => void;
  empty?: string;
}

export function PaperList({ items, onOpen, empty }: Props) {
  const list = useExportList();
  if (items.length === 0) return <p className="muted">{empty ?? "No papers to show."}</p>;
  return (
    <ul className="paper-list">
      {items.map((r, i) => (
        <li key={`${r.doi ?? r.openalex_id ?? r.title}-${i}`} className="paper-item">
          <div className="paper-main">
            <button className="paper-title" onClick={() => onOpen(r)}>{r.title || "(untitled)"}</button>
            <div className="small">{authorsLine(r.authors, 4)}</div>
            <div className="muted small">{venueLine(r)}{r.cited_by_count != null ? ` · cited by ${r.cited_by_count}` : ""}</div>
            <div className="badge-stack"><OABadge record={r} /><RetractionBadge info={r.retraction} />{r.type && <span className="badge neutral">{r.type}</span>}</div>
          </div>
          <div className="paper-actions">
            <button className="btn small-btn" onClick={() => onOpen(r)}>Open</button>
            <button className="btn small-btn" onClick={() => (list.has(r) ? list.remove(r) : list.add(r))}>{list.has(r) ? "✓ In export list" : "Add to export list"}</button>
          </div>
        </li>
      ))}
    </ul>
  );
}
