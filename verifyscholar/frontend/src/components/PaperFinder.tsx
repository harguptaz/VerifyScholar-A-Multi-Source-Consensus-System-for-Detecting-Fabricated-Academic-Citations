import { useEffect, useRef, useState, type FormEvent } from "react";
import { api } from "../api";
import type { FinderSeed, LookupResponse, PaperCardData, PaperRecord, SearchResponse } from "../types";
import { LoadingOverlay, type Pending } from "./LoadingOverlay";
import { PaperCard } from "./PaperCard";
import { PaperList } from "./PaperList";

type Mode = "lookup" | "search";

export const MIN_YEAR = 1900;

interface YearCheck {
  from?: string;
  to?: string;
  /** True when a search must not run: an error is showing, or a half-typed year would be revealed on submit. */
  blocking: boolean;
}

/**
 * Year filter rules: each box is empty or a 4-digit year between MIN_YEAR and this year, and "To" can't be earlier
 * than "From". A half-typed year is only reported once the user leaves the box or presses Search.
 */
export function checkYears(from: string, to: string, reveal: { from: boolean; to: boolean }, maxYear = new Date().getFullYear()): YearCheck {
  const one = (v: string, show: boolean): { msg?: string; bad: boolean } => {
    if (v === "") return { bad: false };
    if (v.length < 4) return { msg: show ? "Enter a 4-digit year" : undefined, bad: true };
    const y = Number(v);
    if (y < MIN_YEAR || y > maxYear) return { msg: `Year must be between ${MIN_YEAR} and ${maxYear}`, bad: true };
    return { bad: false };
  };
  const f = one(from, reveal.from);
  const t = one(to, reveal.to);
  let toMsg = t.msg;
  let rangeBad = false;
  if (!f.bad && !t.bad && from && to && Number(to) < Number(from)) {
    toMsg = "'To' can't be earlier than 'From'";
    rangeBad = true;
  }
  return { from: f.msg, to: toMsg, blocking: f.bad || t.bad || rangeBad };
}

/** Each tab keeps its own result, so a lookup never shows up under Search (and the other way round). */
interface TabState<R> {
  response: R | null;
  card: PaperCardData | null;
  error: string | null;
  busy: boolean;
  pending: Pending | null; // what the loader says while busy
}

const EMPTY = { response: null, card: null, error: null, busy: false, pending: null };

export function PaperFinder({ seed }: { seed: FinderSeed | null }) {
  const [mode, setMode] = useState<Mode>("lookup");
  const [lookupQ, setLookupQ] = useState("");
  const [q, setQ] = useState("");
  const [yearFrom, setYearFrom] = useState("");
  const [yearTo, setYearTo] = useState("");
  const [reveal, setReveal] = useState({ from: false, to: false });
  const [oa, setOa] = useState(false);
  const [type, setType] = useState("");
  const [sort, setSort] = useState<"relevance" | "cited_by" | "newest">("relevance");
  const [page, setPage] = useState(1);

  const [lookupTab, setLookupTab] = useState<TabState<LookupResponse>>(EMPTY);
  const [searchTab, setSearchTab] = useState<TabState<SearchResponse>>(EMPTY);

  const years = checkYears(yearFrom, yearTo, reveal);

  const setTab = (m: Mode, patch: Partial<TabState<LookupResponse>> | Partial<TabState<SearchResponse>>) => {
    if (m === "lookup") setLookupTab((s) => ({ ...s, ...(patch as Partial<TabState<LookupResponse>>) }));
    else setSearchTab((s) => ({ ...s, ...(patch as Partial<TabState<SearchResponse>>) }));
  };

  // Each tab numbers its requests. Cancel (or a newer request) bumps the number, so a late answer is dropped.
  const requestNo = useRef<Record<Mode, number>>({ lookup: 0, search: 0 });

  const guard = async (m: Mode, pending: Pending, fn: (current: () => boolean) => Promise<void>) => {
    const no = ++requestNo.current[m];
    const current = () => requestNo.current[m] === no;
    setTab(m, { busy: true, error: null, pending });
    try {
      await fn(current);
    } catch (e) {
      if (current()) setTab(m, { error: e instanceof Error ? e.message : "Something went wrong" });
    } finally {
      if (current()) setTab(m, { busy: false, pending: null });
    }
  };

  const cancel = (m: Mode) => {
    requestNo.current[m]++;
    setTab(m, { busy: false, pending: null }); // the previous results stay on screen
  };

  const runLookup = (query: string) =>
    guard("lookup", { title: "Looking up…", detail: query.trim() }, async (current) => {
      const r = await api.lookup(query.trim());
      if (!current()) return;
      setLookupTab((s) => ({ ...s, response: r, card: r.paper ?? null, error: !r.paper && r.candidates.length === 0 ? r.message || "Nothing found." : null }));
    });

  const runSearch = (p = 1, override?: string) => {
    const query = (override ?? q).trim();
    return guard("search", { title: p > 1 ? `Loading page ${p}…` : "Searching papers…", detail: query }, async (current) => {
      const r = await api.search({
        q: query, year_from: yearFrom ? Number(yearFrom) : undefined, year_to: yearTo ? Number(yearTo) : undefined,
        oa, type: type || undefined, sort, page: p,
      });
      if (!current()) return;
      setSearchTab((s) => ({ ...s, response: r, card: null }));
      setPage(p);
    });
  };

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

  /** Opens a paper in the tab it was picked from. */
  const openIn = (m: Mode) => (r: PaperRecord) => {
    const id = r.doi || r.openalex_id;
    if (!id) {
      setTab(m, { error: "This record has no DOI or OpenAlex ID to open." });
      return;
    }
    void guard(m, { title: "Opening paper…", detail: r.title || id }, async (current) => {
      const res = await api.lookup(id);
      if (!current()) return;
      if (res.paper) setTab(m, { card: res.paper });
      else setTab(m, { error: res.message || "Could not open this paper." });
    });
  };

  const onLookup = (e: FormEvent) => {
    e.preventDefault();
    if (lookupQ.trim().length >= 3) void runLookup(lookupQ);
  };
  const onSearch = (e: FormEvent) => {
    e.preventDefault();
    setReveal({ from: true, to: true }); // a half-typed year is reported now
    if (q.trim().length >= 2 && !years.blocking) void runSearch(1);
  };
  const editYear = (which: "from" | "to", v: string) => {
    const clean = v.replace(/\D/g, "").slice(0, 4);
    if (which === "from") setYearFrom(clean);
    else setYearTo(clean);
    setReveal((r) => ({ ...r, [which]: false })); // don't nag while typing; checked again on leaving the box
  };

  const tab = mode === "lookup" ? lookupTab : searchTab;
  const hasContent =
    tab.card != null ||
    (mode === "lookup" ? (lookupTab.response?.candidates.length ?? 0) > 0 : searchTab.response != null);
  const yearShown = Boolean(years.from || years.to);

  return (
    <div className="finder">
      {tab.pending && <LoadingOverlay key={requestNo.current[mode]} pending={tab.pending} onCancel={() => cancel(mode)} />}
      {/* the stage fills the screen and centres the card while there is nothing to show; the card only hugs its content */}
      <div className={hasContent ? "finder-stage" : "finder-stage hero"}>
      <div className={hasContent ? "card finder-card" : "card finder-card hero"}>
        <div className="finder-inner">
          <div className="tabs">
            <button className={mode === "lookup" ? "tab active" : "tab"} onClick={() => setMode("lookup")}>Look up a DOI, link or title</button>
            <button className={mode === "search" ? "tab active" : "tab"} onClick={() => setMode("search")}>Search papers</button>
          </div>

          {!hasContent && (
            <div className="finder-intro">
              <h2>Find a paper</h2>
              <p className="muted">
                {mode === "lookup"
                  ? "Paste a DOI, a doi.org or arXiv link, an OpenAlex ID or an exact title."
                  : "Search by keywords or part of a title, and narrow the results by year, type or open access."}
              </p>
            </div>
          )}

          {mode === "lookup" ? (
            <form onSubmit={onLookup} className="finder-form">
              <input className="text-input" value={lookupQ} onChange={(e) => setLookupQ(e.target.value)} placeholder="10.1038/nature14539  ·  https://doi.org/…  ·  arXiv link  ·  OpenAlex ID (W…)  ·  paper title" aria-label="DOI, link or title" />
              <button className="btn primary" disabled={lookupTab.busy || lookupQ.trim().length < 3}>{lookupTab.busy ? "Looking up…" : "Look up"}</button>
            </form>
          ) : (
            <form onSubmit={onSearch} className="finder-form wrap" noValidate>
              <input className="text-input grow" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Keywords or title, e.g. transformer sentiment analysis Hindi" aria-label="Search papers" />
              <div className={yearShown ? "filters has-error" : "filters"}>
                <div className="year-field">
                  <label>From <input className={years.from ? "year invalid" : "year"} inputMode="numeric" value={yearFrom} placeholder="2018"
                    onChange={(e) => editYear("from", e.target.value)} onBlur={() => setReveal((r) => ({ ...r, from: true }))}
                    aria-invalid={years.from ? true : undefined} aria-describedby={years.from ? "year-from-error" : undefined} /></label>
                  {years.from && <span id="year-from-error" className="field-error" role="alert">{years.from}</span>}
                </div>
                <div className="year-field">
                  <label>To <input className={years.to ? "year invalid" : "year"} inputMode="numeric" value={yearTo} placeholder={String(new Date().getFullYear())}
                    onChange={(e) => editYear("to", e.target.value)} onBlur={() => setReveal((r) => ({ ...r, to: true }))}
                    aria-invalid={years.to ? true : undefined} aria-describedby={years.to ? "year-to-error" : undefined} /></label>
                  {years.to && <span id="year-to-error" className="field-error" role="alert">{years.to}</span>}
                </div>
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
              <button className="btn primary" disabled={searchTab.busy || q.trim().length < 2 || yearShown}>{searchTab.busy ? "Searching…" : "Search"}</button>
            </form>
          )}
          {tab.error && <div className="notice error">{tab.error}</div>}
        </div>
      </div>
      </div>

      {mode === "lookup" ? (
        lookupTab.card ? (
          <PaperCard data={lookupTab.card} onOpen={openIn("lookup")} onBack={lookupTab.response?.candidates.length ? () => setTab("lookup", { card: null }) : undefined} />
        ) : lookupTab.response && lookupTab.response.candidates.length > 0 ? (
          <div className="card">
            <h3>Pick a paper</h3>
            <PaperList items={lookupTab.response.candidates} onOpen={openIn("lookup")} />
          </div>
        ) : null
      ) : searchTab.card ? (
        <PaperCard data={searchTab.card} onOpen={openIn("search")} onBack={searchTab.response ? () => setTab("search", { card: null }) : undefined} />
      ) : searchTab.response ? (
        <div className="card">
          {searchTab.response.warnings.map((w, i) => <div key={i} className="notice warn">{w}</div>)}
          <p className="muted small">{searchTab.response.total != null ? `${searchTab.response.total.toLocaleString()} results` : "Results"} · page {searchTab.response.page}</p>
          <PaperList items={searchTab.response.items} onOpen={openIn("search")} empty="No papers matched. Try different keywords or loosen the filters." />
          <div className="btn-row">
            <button className="btn" disabled={searchTab.busy || page <= 1} onClick={() => void runSearch(page - 1)}>← Previous</button>
            <button className="btn" disabled={searchTab.busy || searchTab.response.items.length < searchTab.response.per_page} onClick={() => void runSearch(page + 1)}>Next →</button>
          </div>
        </div>
      ) : null}
    </div>
  );
}
