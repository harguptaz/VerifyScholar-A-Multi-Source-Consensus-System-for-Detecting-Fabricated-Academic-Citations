import { act } from "@testing-library/react";
import { vi } from "vitest";
import type { CitationResult, JobResult, JobStatus, PaperCardData, PaperRecord, SourceResult } from "../types";

// ---------- fake EventSource -------------------------------------------------------------
export class FakeEventSource {
  static instances: FakeEventSource[] = [];
  url: string;
  closed = false;
  onerror: (() => void) | null = null;
  private listeners: Record<string, Array<(e: MessageEvent) => void>> = {};

  constructor(url: string) {
    this.url = url;
    FakeEventSource.instances.push(this);
  }
  addEventListener(type: string, fn: (e: MessageEvent) => void) {
    (this.listeners[type] ??= []).push(fn);
  }
  close() {
    this.closed = true;
  }
  emit(type: string, data: unknown = {}) {
    act(() => {
      for (const fn of this.listeners[type] ?? []) fn(new MessageEvent(type, { data: JSON.stringify(data) }));
    });
  }
}

export function installFakeEventSource() {
  FakeEventSource.instances = [];
  vi.stubGlobal("EventSource", FakeEventSource);
}

// ---------- fetch router -------------------------------------------------------------------
type Handler = (url: URL, init?: RequestInit) => unknown | { status: number; body: unknown };
export interface Route { method: string; path: RegExp | string; handler: Handler }

export function mockFetch(routes: Route[]) {
  const calls: Array<{ method: string; url: URL; body?: unknown }> = [];
  const fn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input));
    const method = (init?.method ?? "GET").toUpperCase();
    let body: unknown;
    if (typeof init?.body === "string") body = JSON.parse(init.body);
    calls.push({ method, url, body });
    const route = routes.find((r) => r.method === method && (typeof r.path === "string" ? r.path === url.pathname : r.path.test(url.pathname)));
    if (!route) return new Response(JSON.stringify({ detail: `no mock for ${method} ${url.pathname}` }), { status: 404 });
    const out = route.handler(url, init) as { status?: number; body?: unknown } | unknown;
    if (out && typeof out === "object" && "status" in (out as object) && "body" in (out as object)) {
      const o = out as { status: number; body: unknown };
      return new Response(typeof o.body === "string" ? o.body : JSON.stringify(o.body), { status: o.status });
    }
    return new Response(JSON.stringify(out), { status: 200, headers: { "Content-Type": "application/json" } });
  });
  vi.stubGlobal("fetch", fn);
  return { fetch: fn, calls };
}

// ---------- fixtures -----------------------------------------------------------------------
export function record(over: Partial<PaperRecord> = {}): PaperRecord {
  return {
    doi: "10.1038/nature14539", title: "Deep learning", authors: [{ family: "LeCun", given: "Yann", name: "Yann LeCun" }, { family: "Bengio", given: "Yoshua", name: "Yoshua Bengio" }],
    journal: "Nature", journal_aliases: ["Nature"], issn: ["0028-0836"], year: 2015, alt_years: [], volume: "521", issue: "7553", pages: "436-444", type: "journal-article",
    abstract: "Deep learning allows computational models to learn representations of data.", doi_url: "https://doi.org/10.1038/nature14539", url: "https://doi.org/10.1038/nature14539",
    is_oa: true, oa_status: "gold", oa_url: "https://example.org/dl.pdf", openalex_id: "W2919115771", cited_by_count: 60000,
    retraction: { status: "none_found", notices: [] }, sources: ["crossref", "openalex"], ...over,
  };
}

function source(over: Partial<SourceResult>): SourceResult {
  return { source: "crossref", label: "CrossRef REST API", kind: "record", status: "match", method: "doi", score: 98, fields: {}, ...over };
}

export function citation(index: number, verdict: CitationResult["verdict"], over: Partial<CitationResult> = {}): CitationResult {
  const rec = verdict === "ghost" ? null : record();
  return {
    index, raw: `Raw citation text number ${index}`, parsed: { index, raw: `Raw citation text number ${index}`, authors: [], parse_method: "heuristic", title: `Title ${index}` },
    verdict, confidence: verdict === "verified" ? 96 : verdict === "suspicious" ? 62 : 4, sources: [], record: rec, flags: [], links: rec ? { doi_url: rec.doi_url, landing_page: rec.url, open_access: rec.oa_url } : {},
    doi_status: verdict === "ghost" ? "not_found" : "found", journal_status: verdict === "ghost" ? "not_found" : "verified_in_index", reason: `Reason text for citation ${index}.`,
    retraction: { status: "none_found", notices: [] }, relevance: null, ...over,
  };
}

export function jobResult(): JobResult {
  const suspicious = citation(2, "suspicious", {
    reason: "A real paper was found but your citation differs from it: year (you cited 2018, the record has 2015).",
    sources: [
      source({
        score: 62, status: "partial", record: record(),
        fields: {
          title: { submitted: "Deep learning", found: "Deep learning", similarity: 1, status: "match" },
          year: { submitted: "2018", found: "2015", similarity: 0, status: "mismatch" },
          authors: { submitted: "Kumar, R.", found: "LeCun, Yann; Bengio, Yoshua", similarity: 0, status: "mismatch" },
        },
      }),
      { source: "doaj", label: "DOAJ", kind: "journal", status: "no_record", score: 0, fields: {}, note: "Not listed in DOAJ (DOAJ lists open-access journals only)" },
    ],
  });
  const ghost = citation(3, "ghost", { flags: [{ code: "doi_placeholder", severity: "medium", message: "The DOI suffix looks like a placeholder" }] });
  const retracted = citation(4, "verified", { retraction: { status: "retracted", notices: [{ type: "retraction", source: "retraction-watch", doi: "10.1/notice" }] }, confidence: 97 });
  ghost.sources = [{
    source: "openalex", label: "OpenAlex API", kind: "record", status: "no_record", score: 0, fields: {},
    candidates: [
      { title: "Layer Normalization in Transformers", doi: "10.1/decoy", year: 2019, journal: "Some Journal", title_similarity: 0.82, accepted: false, note: "generic title and no author, journal or year in common" },
    ],
  }];
  return {
    summary: { total: 4, verified: 2, suspicious: 1, ghost: 1, inconclusive: 0, retracted: 1, orphans: 0, elapsed_seconds: 3.2 },
    results: [citation(1, "verified"), suspicious, ghost, retracted],
    warnings: ["GROBID is not reachable, so a built-in parser was used."],
  };
}

export function doneJob(id = "job1"): JobStatus {
  return { id, mode: "citations", status: "done", stage: "Done", total: 4, completed: 4, result: jobResult() };
}

export function card(over: Partial<PaperCardData> = {}): PaperCardData {
  const rec = over.record ?? record();
  return {
    record: rec, verdict: "verified", confidence: 100, reason: "Matched", sources: [],
    access: rec.oa_url
      ? { level: "open_access", open_access_url: rec.oa_url, publisher_url: rec.doi_url, note: "A free, legal full-text copy is available (open-access status: gold)." }
      : { level: "publisher_page", publisher_url: rec.doi_url, note: "No free full text was found. Open the publisher page; your institution's library login may give you access." },
    retraction: rec.retraction, ...over,
  };
}
