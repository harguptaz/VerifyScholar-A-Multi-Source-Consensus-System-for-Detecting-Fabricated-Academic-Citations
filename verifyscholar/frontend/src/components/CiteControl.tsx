import type { Style } from "../types";

const STYLES: { value: Style; label: string }[] = [
  { value: "apa", label: "APA" },
  { value: "ieee", label: "IEEE" },
  { value: "harvard", label: "Harvard" },
  { value: "vancouver", label: "Vancouver" },
  { value: "mla", label: "MLA" },
];

interface Props {
  style: Style;
  onStyle: (s: Style) => void;
  onCopy: () => void;
  /** Button text, e.g. "Copy citation" or "Copy corrected citation". */
  label: string;
  busy?: boolean;
  primary?: boolean;
}

/** "Cite as [style ▾][Copy …]": the style and the copy button as one joined control. */
export function CiteControl({ style, onStyle, onCopy, label, busy, primary }: Props) {
  return (
    <div className="cite-control" role="group" aria-label="Cite as">
      <span className="cite-label" aria-hidden="true">Cite as</span>
      <div className="cite-group">
        <select value={style} onChange={(e) => onStyle(e.target.value as Style)} aria-label="Citation style">
          {STYLES.map((s) => <option key={s.value} value={s.value}>{s.label}</option>)}
        </select>
        <button className={primary ? "btn primary" : "btn"} onClick={onCopy} disabled={busy}>{busy ? "Formatting…" : label}</button>
      </div>
    </div>
  );
}
