import { VERDICT_LABEL } from "../format";
import type { FieldStatus, PaperRecord, RetractionInfo, Verdict } from "../types";

export function VerdictBadge({ verdict, score }: { verdict: Verdict; score?: number | null }) {
  return (
    <span className={`badge verdict-${verdict}`} title={score != null ? `Confidence ${score}/100` : undefined}>
      {VERDICT_LABEL[verdict]}
    </span>
  );
}

const RETRACTION_TEXT: Record<RetractionInfo["status"], string> = {
  retracted: "Retracted",
  concern: "Expression of concern",
  corrected: "Correction issued",
  none_found: "",
  unknown: "",
};

/** Retraction status is a separate warning: it never changes the verdict (a retracted paper still exists). */
export function RetractionBadge({ info }: { info: RetractionInfo }) {
  const text = RETRACTION_TEXT[info.status];
  if (!text) return null;
  return (
    <span className={`badge retraction-${info.status}`} title={info.notices.map((n) => `${n.type} (${n.source})`).join(", ")}>
      {text}
    </span>
  );
}

export function OABadge({ record }: { record: PaperRecord }) {
  if (!record.is_oa) return null;
  return <span className="badge oa">Open access</span>;
}

const FIELD_ICON: Record<FieldStatus, string> = { match: "✓", partial: "≈", mismatch: "✗", missing: "–" };
const FIELD_TEXT: Record<FieldStatus, string> = { match: "Match", partial: "Close", mismatch: "Mismatch", missing: "Not comparable" };

export function FieldStatusTag({ status }: { status: FieldStatus }) {
  return (
    <span className={`field-status field-${status}`}>
      {FIELD_ICON[status]} {FIELD_TEXT[status]}
    </span>
  );
}

export function ScoreBar({ score }: { score?: number | null }) {
  if (score == null) return <span className="muted">—</span>;
  const tone = score >= 80 ? "good" : score >= 40 ? "warn" : "bad";
  return (
    <span className="scorebar" title={`${score}/100`}>
      <span className={`scorebar-fill ${tone}`} style={{ width: `${score}%` }} />
      <span className="scorebar-num">{score}</span>
    </span>
  );
}
