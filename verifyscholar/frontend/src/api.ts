import type {
  ExportFormat, FormatResponse, HealthResponse, JobStatus, LogResponse, LookupResponse, NetworkResponse, PaperRecord,
  SearchParams, SearchResponse, SourcesHealthResponse, Style,
} from "./types";

export const API_BASE: string = (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? "http://localhost:8000";

export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

async function errorMessage(r: Response): Promise<string> {
  try {
    const j = await r.json();
    if (typeof j.detail === "string") return j.detail;
    if (Array.isArray(j.detail) && j.detail[0]?.msg) return String(j.detail[0].msg);
  } catch {
    /* not JSON */
  }
  return `${r.status} ${r.statusText}`;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let r: Response;
  try {
    r = await fetch(`${API_BASE}${path}`, init);
  } catch {
    throw new ApiError(`Cannot reach the VerifyScholar API at ${API_BASE}. Is the backend running?`, 0);
  }
  if (!r.ok) throw new ApiError(await errorMessage(r), r.status);
  return (await r.json()) as T;
}

const json = (body: unknown): RequestInit => ({
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

/** Fetch a file from the API and hand it to the browser as a download. */
async function download(path: string, init?: RequestInit, fallbackName = "download"): Promise<void> {
  let r: Response;
  try {
    r = await fetch(`${API_BASE}${path}`, init);
  } catch {
    throw new ApiError(`Cannot reach the VerifyScholar API at ${API_BASE}.`, 0);
  }
  if (!r.ok) throw new ApiError(await errorMessage(r), r.status);
  const blob = await r.blob();
  const cd = r.headers.get("Content-Disposition") ?? "";
  const name = /filename="?([^";]+)"?/.exec(cd)?.[1] ?? fallbackName;
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
}

export const api = {
  health: () => request<HealthResponse>("/health"),
  sourcesHealth: () => request<SourcesHealthResponse>("/health/sources"),
  countCitations: (text: string) => request<{ count: number }>("/citations/count", json({ text })),
  verifyCitations: (text: string) => request<{ job_id: string }>("/verify/citations", json({ text })),
  verifyDocument: (file: File, relevance: boolean) => {
    const form = new FormData();
    form.append("file", file);
    form.append("relevance", String(relevance));
    return request<{ job_id: string }>("/verify/document", { method: "POST", body: form });
  },
  getJob: (id: string) => request<JobStatus>(`/jobs/${id}`),
  streamUrl: (id: string) => `${API_BASE}/jobs/${id}/stream`,
  downloadReport: (id: string, format: "csv" | "pdf") => download(`/jobs/${id}/export?format=${format}`, undefined, `verification-report.${format}`),
  getLog: (id: string) => request<LogResponse>(`/jobs/${id}/log`),
  verifyLog: (id: string) => request<{ chain_valid: boolean; entries: number; final_hash: string | null; first_invalid_seq: number | null }>(`/jobs/${id}/log/verify`),
  downloadLog: (id: string, format: "csv" | "pdf") => download(`/jobs/${id}/log?format=${format}`, undefined, `transparency-log.${format}`),

  lookup: (q: string) => request<LookupResponse>(`/papers/lookup?id=${encodeURIComponent(q)}`),
  search: (p: SearchParams) => {
    const qs = new URLSearchParams({ q: p.q });
    if (p.year_from) qs.set("year_from", String(p.year_from));
    if (p.year_to) qs.set("year_to", String(p.year_to));
    if (p.oa) qs.set("oa", "true");
    if (p.type) qs.set("type", p.type);
    qs.set("sort", p.sort ?? "relevance");
    qs.set("page", String(p.page ?? 1));
    qs.set("per_page", String(p.per_page ?? 10));
    return request<SearchResponse>(`/papers/search?${qs}`);
  },
  network: (openalexId: string, kind: "references" | "cited-by" | "related", page = 1) =>
    request<NetworkResponse>(`/papers/${openalexId}/${kind}?page=${page}&per_page=10`),
  formatCitation: (record: PaperRecord, style: Style) => request<FormatResponse>("/papers/format", json({ record, style })),

  exportBibliography: (body: { job_id?: string; extra_papers: PaperRecord[]; format: ExportFormat; style: Style; include_suspicious: boolean }) =>
    download("/bibliography/export", json(body), "bibliography.txt"),
};
