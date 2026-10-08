import { useEffect, useState, type FormEvent } from "react";
import { api } from "../api";
import type { FinderSeed, LookupResponse, PaperCardData, PaperRecord, SearchResponse } from "../types";
import { PaperCard } from "./PaperCard";
import { PaperList } from "./PaperList";

export function PaperFinder({ seed }: { seed: FinderSeed | null }) {
  const [mode, setMode] = useState<"lookup" | "search">("lookup");
  const [lookupQ, setLookupQ] = useState("");
  const [q, setQ] = useState("");
  const [yearFrom, setYearFrom] = useState("");
  const [yearTo, setYearTo] = useState("");
  const [oa, setOa] = useState(false);
  const [type, setType] = useState("");
  const [sort, setSort] = useState<"relevance" | "cited_by" | "newest">("relevance");
  const [page, setPage] = useState(1);

  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [card, setCard] = useState<PaperCardData | null>(null);
  const [lookup, setLookup] = useState<LookupResponse | null>(null);
  const [results, setResults] = useState<SearchResponse | null>(null);

  const guard = async (fn: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    try {
      await fn();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Something went wrong");
    } finally {
      setBusy(false);
    }
  };

  const runLookup = (query: string) =>
    guard(async () => {
      const r = await api.lookup(query.trim());
      setLookup(r);
      setCard(r.paper ?? null);
      if (!r.paper && r.candidates.length === 0) setError(r.message || "Nothing found.");
    });

  const runSearch = (p = 1, override?: string) =>
    guard(async () => {
      const query = (override ?? q).trim();
      const r = await api.search({
        q: query, year_from: yearFrom ? Number(yearFrom) : undefined, year_to: yearTo ? Number(yearTo) : undefined,
        oa, type: type || undefined, sort, page: p,
      });
      setResults(r);
      setPage(p);
      setCard(null);
    });

  // open the finder from elsewhere in the app ("Open in Paper Finder", "Find a replacement")
  useEffect(() => {
    if (!seed) return;
    if (seed.kind === "lookup") {
      setMode("lookup");
      setLookupQ(seed.q);
      void runLookup(seed.q);
    } else {
      setMode("search");
      setQ(seed.q);
      void runSearch(1, seed.q);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [seed?.nonce]);

  const open = (r: PaperRecord) => {
    const id = r.doi || r.openalex_id;
    if (!id) {
      setError("This record has no DOI or OpenAlex ID to open.");
      return;
    }
    void guard(async () => {
      const res = await api.lookup(id);
      if (res.paper) setCard(res.paper);
      else setError(res.message || "Could not open this paper.");
    });
  };

  const onLookup = (e: FormEvent) => {
    e.preventDefault();
    if (lookupQ.trim().length >= 3) void runLookup(lookupQ);
  };
  const onSearch = (e: FormEvent) => {
    e.preventDefault();
    if (q.trim().length >= 2) void runSearch(1);
  };

  return (
    <div>
      <div className="card">
        <div className="tabs">
          <button className={mode === "lookup" ? "tab active" : "tab"} onClick={() => setMode("lookup")}>Look up a DOI, link or title</button>
          <button className={mode === "search" ? "tab active" : "tab"} onClick={() => setMode("search")}>Search papers</button>
        </div>

        {mode === "lookup" ? (
          <form onSubmit={onLookup} className="finder-form">
            <input className="text-input" value={lookupQ} onChange={(e) => setLookupQ(e.target.value)} placeholder="10.1038/nature14539  ·  https://doi.org/…  ·  arXiv link  ·  OpenAlex ID (W…)  ·  paper title" aria-label="DOI, link or title" />
            <button className="btn primary" disabled={busy || lookupQ.trim().length < 3}>{busy ? "Looking up…" : "Look up"}</button>
          </form>
        ) : (
          <form onSubmit={onSearch} className="finder-form wrap">
            <input className="text-input grow" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Keywords or title, e.g. transformer sentiment analysis Hindi" aria-label="Search papers" />
            <button className="btn primary" disabled={busy || q.trim().length < 2}>{busy ? "Searching…" : "Search"}</button>
            <div className="filters">
              <label>From <input className="year" inputMode="numeric" value={yearFrom} onChange={(e) => setYearFrom(e.target.value.replace(/\D/g, "").slice(0, 4))} placeholder="2018" /></label>
              <label>To <input className="year" inputMode="numeric" value={yearTo} onChange={(e) => setYearTo(e.target.value.replace(/\D/g, "").slice(0, 4))} placeholder="2025" /></label>
              <label>Type
                <select value={type} onChange={(e) => setType(e.target.value)}>
                  <option value="">Any</option><option value="article">Article</option><option value="preprint">Preprint</option><option value="book">Book</option><option value="book-chapter">Book chapter</option><option value="dissertation">Dissertation</option>
                </select>
              </label>
              <label>Sort
                <select value={sort} onChange={(e) => setSort(e.target.value as typeof sort)}>
                  <option value="relevance">Relevance</option><option value="cited_by">Most cited</option><option value="newest">Newest</option>
                </select>
              </label>
              <label className="check"><input type="checkbox" checked={oa} onChange={(e) => setOa(e.target.checked)} /> Open access only</label>
            </div>
          </form>
        )}
        {error && <div className="notice error">{error}</div>}
      </div>

      {card ? (
        <PaperCard data={card} onOpen={open} onBack={lookup?.candidates.length || results ? () => setCard(null) : undefined} />
      ) : mode === "lookup" && lookup && lookup.candidates.length > 0 ? (
        <div className="card">
          <h3>Pick a paper</h3>
          <PaperList items={lookup.candidates} onOpen={open} />
        </div>
      ) : mode === "search" && results ? (
        <div className="card">
          {results.warnings.map((w, i) => <div key={i} className="notice warn">{w}</div>)}
          <p className="muted small">{results.total != null ? `${results.total.toLocaleString()} results` : "Results"} · page {results.page}</p>
          <PaperList items={results.items} onOpen={open} empty="No papers matched. Try different keywords or loosen the filters." />
          <div className="btn-row">
            <button className="btn" disabled={busy || page <= 1} onClick={() => void runSearch(page - 1)}>← Previous</button>
            <button className="btn" disabled={busy || results.items.length < results.per_page} onClick={() => void runSearch(page + 1)}>Next →</button>
          </div>
        </div>
      ) : null}
    </div>
  );
}
