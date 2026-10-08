import { describe, expect, it, vi } from "vitest";
import { ApiError, api } from "../api";
import { mockFetch } from "./helpers";

describe("API client", () => {
  it("explains when the backend cannot be reached", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => { throw new TypeError("Failed to fetch"); }));
    await expect(api.health()).rejects.toThrow(/Cannot reach the VerifyScholar API/);
  });

  it("maps FastAPI error bodies to readable messages", async () => {
    mockFetch([
      { method: "POST", path: "/verify/citations", handler: () => ({ status: 400, body: { detail: "Paste at least one citation." } }) },
      { method: "GET", path: "/papers/search", handler: () => ({ status: 422, body: { detail: [{ msg: "String should have at least 2 characters" }] } }) },
    ]);
    await expect(api.verifyCitations(" ")).rejects.toMatchObject({ message: "Paste at least one citation.", status: 400 });
    await expect(api.search({ q: "x" })).rejects.toBeInstanceOf(ApiError);
    await expect(api.search({ q: "x" })).rejects.toThrow("String should have at least 2 characters");
  });

  it("builds search queries with only the filters that were set", async () => {
    const { calls } = mockFetch([{ method: "GET", path: "/papers/search", handler: () => ({ query: "q", page: 1, per_page: 10, items: [], warnings: [] }) }]);
    await api.search({ q: "deep learning", year_from: 2020, oa: true, sort: "newest", page: 2 });
    const p = calls[0].url.searchParams;
    expect(Object.fromEntries(p.entries())).toEqual({ q: "deep learning", year_from: "2020", oa: "true", sort: "newest", page: "2", per_page: "10" });
  });

  it("uploads documents as multipart form data with the relevance flag", async () => {
    const { fetch } = mockFetch([{ method: "POST", path: "/verify/document", handler: () => ({ job_id: "j" }) }]);
    await api.verifyDocument(new File(["%PDF"], "paper.pdf", { type: "application/pdf" }), true);
    const init = fetch.mock.calls[0][1] as RequestInit;
    expect(init.body).toBeInstanceOf(FormData);
    expect((init.body as FormData).get("relevance")).toBe("true");
    expect(((init.body as FormData).get("file") as File).name).toBe("paper.pdf");
  });
});
