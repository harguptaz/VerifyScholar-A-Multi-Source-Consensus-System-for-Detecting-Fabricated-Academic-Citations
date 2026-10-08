import type { Author, CitationResult, PaperRecord, Verdict } from "./types";

export const VERDICT_LABEL: Record<Verdict, string> = {
  verified: "Verified",
  suspicious: "Suspicious",
  ghost: "Ghost",
  inconclusive: "Inconclusive",
};

export const DOI_STATUS_LABEL: Record<CitationResult["doi_status"], string> = {
  found: "Found",
  mismatch: "Mismatch",
  not_found: "Not found",
  no_doi: "No DOI in citation",
  unavailable: "Source unavailable",
};

export const JOURNAL_STATUS_LABEL: Record<CitationResult["journal_status"], string> = {
  verified_in_index: "Verified in CrossRef / OpenAlex",
  found_in_doaj: "Found in DOAJ only",
  not_found: "Not found",
  not_provided: "Not provided",
  unavailable: "Source unavailable",
};

export const RELEVANCE_LABEL: Record<string, string> = {
  well_supported: "Well supported",
  possibly_misattributed: "Possibly misattributed",
  background: "Background / off-topic",
  needs_review: "Needs review",
};

export const FIELD_LABEL: Record<string, string> = {
  title: "Title",
  authors: "Authors",
  journal: "Journal",
  year: "Year",
  volume: "Volume",
  pages: "Pages",
  doi: "DOI",
};

export function authorsLine(authors: Author[], max = 3): string {
  if (!authors.length) return "";
  const names = authors.slice(0, max).map((a) => a.family || a.name);
  return names.join(", ") + (authors.length > max ? " et al." : "");
}

export function venueLine(r: PaperRecord): string {
  const parts: string[] = [];
  if (r.journal) parts.push(r.journal);
  if (r.volume) parts.push(`vol. ${r.volume}${r.issue ? `(${r.issue})` : ""}`);
  if (r.pages) parts.push(`pp. ${r.pages}`);
  if (r.year) parts.push(String(r.year));
  return parts.join(" · ");
}

/** Stable identity of a paper for the session export list. */
export function recordKey(r: PaperRecord): string {
  return r.doi ? r.doi.toLowerCase() : `t:${(r.title ?? "").toLowerCase().replace(/\W+/g, " ").trim()}`;
}

export function pct(x?: number | null): string {
  return x === null || x === undefined ? "—" : x.toFixed(2);
}

export function localTime(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
}
