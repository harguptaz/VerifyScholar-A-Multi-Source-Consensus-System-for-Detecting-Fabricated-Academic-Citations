// Mirrors backend/app/schemas.py

export type Verdict = "verified" | "suspicious" | "ghost" | "inconclusive";
export type FieldStatus = "match" | "partial" | "mismatch" | "missing";
export type Style = "apa" | "ieee" | "harvard" | "vancouver" | "mla";
export type ExportFormat = "text" | "bibtex" | "ris" | "csv";

export interface Author {
  family: string;
  given: string;
  name: string;
}

export interface RetractionNotice {
  type: string;
  source: string;
  doi?: string | null;
  date?: string | null;
  label?: string | null;
}

export interface RetractionInfo {
  status: "retracted" | "concern" | "corrected" | "none_found" | "unknown";
  notices: RetractionNotice[];
}

export interface PaperRecord {
  doi?: string | null;
  title?: string | null;
  authors: Author[];
  journal?: string | null;
  journal_aliases: string[];
  issn: string[];
  publisher?: string | null;
  year?: number | null;
  alt_years: number[];
  volume?: string | null;
  issue?: string | null;
  pages?: string | null;
  type?: string | null;
  abstract?: string | null;
  url?: string | null;
  doi_url?: string | null;
  is_oa?: boolean | null;
  oa_status?: string | null;
  oa_url?: string | null;
  openalex_id?: string | null;
  cited_by_count?: number | null;
  language?: string | null;
  in_doaj?: boolean | null;
  retraction: RetractionInfo;
  sources: string[];
}

export interface FieldMatch {
  submitted?: string | null;
  found?: string | null;
  similarity?: number | null;
  status: FieldStatus;
}

export interface CandidateInfo {
  title?: string | null;
  doi?: string | null;
  year?: number | null;
  journal?: string | null;
  title_similarity: number;
  accepted: boolean;
  note: string;
}

export interface SourceResult {
  source: string;
  label: string;
  kind: "record" | "journal";
  status: "match" | "partial" | "no_record" | "unavailable" | "skipped";
  method?: "doi" | "search" | "issn" | "title" | null;
  score?: number | null;
  fields: Record<string, FieldMatch>;
  record?: PaperRecord | null;
  doi_resolves_to?: PaperRecord | null;
  candidates?: CandidateInfo[];
  note?: string | null;
}

export interface HeuristicFlag {
  code: string;
  severity: "high" | "medium" | "low";
  message: string;
}

export interface Links {
  doi_url?: string | null;
  landing_page?: string | null;
  open_access?: string | null;
}

export interface Relevance {
  status: "assessed" | "not_assessed";
  reason?: string | null;
  sentence_score?: number | null;
  paper_score?: number | null;
  label?: "well_supported" | "possibly_misattributed" | "background" | "needs_review" | null;
  best_sentence?: string | null;
  mentions: number;
  orphan: boolean;
  sentences: string[];
}

export interface ParsedCitation {
  index: number;
  raw: string;
  doi?: string | null;
  title?: string | null;
  authors: Author[];
  journal?: string | null;
  year?: number | null;
  parse_method: string;
  kind?: "article" | "preprint" | "conference" | "book" | "unknown";
  publisher?: string | null;
  arxiv_id?: string | null;
}

export interface CitationResult {
  index: number;
  raw: string;
  parsed: ParsedCitation;
  verdict: Verdict;
  confidence: number | null;
  sources: SourceResult[];
  record?: PaperRecord | null;
  flags: HeuristicFlag[];
  links: Links;
  doi_status: "found" | "mismatch" | "not_found" | "no_doi" | "unavailable";
  journal_status: "verified_in_index" | "found_in_doaj" | "not_found" | "not_provided" | "unavailable";
  reason: string;
  retraction: RetractionInfo;
  relevance?: Relevance | null;
}

export interface JobSummary {
  total: number;
  verified: number;
  suspicious: number;
  ghost: number;
  inconclusive: number;
  retracted: number;
  orphans: number;
  elapsed_seconds: number;
}

export interface DocumentInfo {
  filename?: string | null;
  title?: string | null;
  abstract?: string | null;
  n_references: number;
  orphan_indices: number[];
  unmatched_markers: string[];
  embedding_backend?: string | null;
}

export interface JobResult {
  summary: JobSummary;
  results: CitationResult[];
  document?: DocumentInfo | null;
  warnings: string[];
}

export interface JobStatus {
  id: string;
  mode: string;
  status: "queued" | "running" | "done" | "error";
  stage: string;
  total: number;
  completed: number;
  error?: string | null;
  result?: JobResult | null;
}

export interface PaperAccess {
  level: "open_access" | "publisher_page" | "none";
  open_access_url?: string | null;
  publisher_url?: string | null;
  note: string;
}

export interface PaperCardData {
  record: PaperRecord;
  verdict: Verdict;
  confidence: number | null;
  reason: string;
  sources: SourceResult[];
  access: PaperAccess;
  retraction: RetractionInfo;
}

export interface LookupResponse {
  query: string;
  query_type: "doi" | "openalex_id" | "title";
  paper?: PaperCardData | null;
  candidates: PaperRecord[];
  message: string;
}

export interface SearchResponse {
  query: string;
  page: number;
  per_page: number;
  total?: number | null;
  items: PaperRecord[];
  warnings: string[];
}

export interface NetworkResponse {
  kind: "references" | "cited_by" | "related";
  openalex_id: string;
  page: number;
  per_page: number;
  total: number;
  items: PaperRecord[];
}

export interface LogEntry {
  seq: number;
  timestamp: string;
  citation_index?: number | null;
  source: string;
  query: string;
  outcome: string;
  verdict?: string | null;
  prev_hash: string;
  entry_hash: string;
}

export interface LogResponse {
  job_id: string;
  entries: LogEntry[];
  chain_valid: boolean;
  final_hash?: string | null;
  first_invalid_seq?: number | null;
}

export interface FormatResponse {
  style: string;
  text: string;
  bibtex: string;
  source: "csl" | "local";
}

export interface SourceHealth {
  ok: boolean;
  detail: string;
  http_status?: number | null;
}

export type SourcesHealthResponse = Record<"crossref" | "openalex" | "doaj", SourceHealth>;

export interface HealthResponse {
  status: string;
  grobid: boolean;
  grobid_url: string;
  grobid_detail: string;
  libreoffice: boolean;
  embedding_backend_setting: string;
  openalex_api_key_set: boolean;
  contact_email_set: boolean;
}

export interface SearchParams {
  q: string;
  year_from?: number;
  year_to?: number;
  oa?: boolean;
  type?: string;
  sort?: "relevance" | "cited_by" | "newest";
  page?: number;
  per_page?: number;
}

/** Asks the Paper Finder tab to run a lookup/search (used by "Open in Paper Finder" / "Find a replacement"). */
export interface FinderSeed {
  kind: "lookup" | "search";
  q: string;
  nonce: number;
}
