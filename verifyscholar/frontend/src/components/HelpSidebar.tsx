import { useEffect, useRef, useState, type RefObject } from "react";
import { modalIsOpen, trapTab } from "../a11y";
import { ConnectionCheck } from "./ConnectionCheck";

export type HelpTopic = "verify" | "finder" | "export";

/** At this width and above, the sidebar pushes the page left; below it, it slides over the page. */
export const PUSH_QUERY = "(min-width: 1200px)";

const TOPIC_TITLE: Record<HelpTopic, string> = {
  verify: "Verify citations",
  finder: "Paper Finder",
  export: "Export list",
};

interface Props {
  open: boolean;
  /** True on wide screens, where the sidebar sits beside the page instead of over it (see PUSH_QUERY). */
  push: boolean;
  topic: HelpTopic;
  onClose: () => void;
  /** The button that opened the sidebar; focus goes back to it on close. */
  returnFocusTo: RefObject<HTMLButtonElement>;
}

export function useMediaQuery(query: string): boolean {
  const get = () => (typeof window !== "undefined" && typeof window.matchMedia === "function" ? window.matchMedia(query).matches : false);
  const [matches, setMatches] = useState(get);
  useEffect(() => {
    if (typeof window.matchMedia !== "function") return;
    const mq = window.matchMedia(query);
    const update = () => setMatches(mq.matches);
    update();
    mq.addEventListener?.("change", update);
    return () => mq.removeEventListener?.("change", update);
  }, [query]);
  return matches;
}

/**
 * "How it works" help, sliding in from the right. On wide screens it sits beside the page (which shifts left)
 * and the page stays usable; on narrow screens it slides over a dimmed page and behaves like a dialog.
 */
export function HelpSidebar({ open, push, topic, onClose, returnFocusTo }: Props) {
  const overlay = !push;
  const panelRef = useRef<HTMLElement>(null);
  const closeRef = useRef(onClose);
  closeRef.current = onClose;

  // focus in on open; on close, give focus back to the button unless the user has moved on to the page
  const wasOpen = useRef(false);
  useEffect(() => {
    const panel = panelRef.current;
    if (open) {
      panel?.focus({ preventScroll: true });
    } else if (wasOpen.current) {
      const active = document.activeElement;
      if (!active || active === document.body || (panel && panel.contains(active))) returnFocusTo.current?.focus();
    }
    wasOpen.current = open;
  }, [open, returnFocusTo]);

  // Escape closes (unless a detail modal is on top, which handles Escape itself); Tab stays inside while it overlays the page
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (modalIsOpen()) return;
      if (e.key === "Escape") {
        e.preventDefault();
        closeRef.current();
      } else if (overlay && panelRef.current) {
        trapTab(e, panelRef.current);
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, overlay]);

  // while it covers the page, the page behind should not scroll
  useEffect(() => {
    if (!open || !overlay) return;
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => { document.body.style.overflow = prev; };
  }, [open, overlay]);

  // a closed sidebar is hidden from screen readers and from the Tab order (it stays mounted so it can slide out)
  const closedProps = open ? {} : ({ "aria-hidden": true, inert: "" } as Record<string, unknown>);

  return (
    <>
      {overlay && open && <div className="help-backdrop" onClick={onClose} aria-hidden="true" />}
      <aside
        id="help-sidebar"
        ref={panelRef}
        className={`help ${push ? "push" : "overlay"}${open ? " open" : ""}`}
        role="dialog"
        aria-modal={overlay && open ? true : undefined}
        aria-labelledby="help-title"
        tabIndex={-1}
        {...closedProps}
      >
        <div className="help-inner">
          <header className="help-head">
            <div>
              <h3 id="help-title">How it works</h3>
              <span className="muted small">{TOPIC_TITLE[topic]}</span>
            </div>
            <button className="icon-btn help-close" onClick={onClose} aria-label="Close help" title="Close (Esc)">×</button>
          </header>
          <div className="help-body">
            {topic === "verify" && <VerifyHelp />}
            {topic === "finder" && <FinderHelp />}
            {topic === "export" && <ExportHelp />}
            <p className="muted small help-shortcut">Press <kbd>?</kbd> to open or close this panel.</p>
          </div>
        </div>
      </aside>
    </>
  );
}

function VerifyHelp() {
  return (
    <>
      <p>VerifyScholar checks each citation against authoritative bibliographic data instead of trusting how it looks. Paste citations in any style, or upload a PDF, DOCX, BibTeX or RIS file.</p>

      <section>
        <h4>Verdicts</h4>
        <ul className="help-verdicts">
          <li><span className="badge verdict-verified">Verified</span> the paper exists and the details match.</li>
          <li><span className="badge verdict-suspicious">Suspicious</span> a real paper was found but some details differ.</li>
          <li><span className="badge verdict-ghost">Ghost</span> no matching paper was found.</li>
          <li><span className="badge verdict-inconclusive">Inconclusive</span> a source didn't respond, so no verdict was given.</li>
        </ul>
        <p className="muted small">A retracted paper stays Verified because it exists, but it gets a separate red Retracted badge.</p>
      </section>

      <section>
        <h4>Confidence score</h4>
        <p>Each citation gets a score from 0 to 100, built from how closely the title, authors, journal, year, volume, pages and DOI match the record that was found.</p>
        <ul className="help-scores">
          <li><strong>80 to 100</strong>: Verified</li>
          <li><strong>40 to 79</strong>: Suspicious</li>
          <li><strong>Below 40</strong>: Ghost</li>
        </ul>
        <p className="muted small">Two sources agreeing, or the journal being listed in DOAJ, raises the score. Fabrication patterns, such as placeholder DOIs or impossible volume and year combinations, lower it. A field that clearly contradicts the record keeps a citation at Suspicious or below, and a DOI that points to a different paper keeps it below 40.</p>
      </section>

      <section>
        <h4>Sources checked</h4>
        <ul>
          <li><strong>CrossRef</strong>: the DOI registration agency; resolves DOIs and searches by title.</li>
          <li><strong>OpenAlex</strong>: an independent index that also covers preprints and works CrossRef misses.</li>
          <li><strong>DOAJ</strong>: confirms the journal exists. It lists open-access journals only, so a miss is never held against a citation.</li>
          <li><strong>DBLP, DataCite and Open Library</strong>: asked only when the sources above leave a citation unsettled, for computer-science papers, arXiv and dataset DOIs, and books. A miss there is never held against a citation.</li>
        </ul>
        <p className="muted small">Every lookup is recorded in a tamper-evident transparency log you can export from the report.</p>
      </section>

      <section>
        <h4>Not getting results?</h4>
        <ConnectionCheck />
      </section>
    </>
  );
}

function FinderHelp() {
  return (
    <>
      <p>Look up a paper you already know, or search for papers on a topic, without leaving the app.</p>
      <section>
        <h4>Look up</h4>
        <p>Paste a DOI, a doi.org or arXiv link, an OpenAlex ID (starting with W) or an exact title. You get the paper's details, abstract, where to read it, and whether it was retracted.</p>
      </section>
      <section>
        <h4>Search</h4>
        <p>Enter keywords or part of a title. Narrow the results by year range, type (article, preprint, book, chapter or dissertation) and open access only, and sort by relevance, most cited or newest.</p>
      </section>
      <section>
        <h4>From a paper</h4>
        <ul>
          <li>Browse its references, the papers that cite it, and related papers.</li>
          <li>Copy a formatted citation in APA, IEEE, Harvard, Vancouver or MLA.</li>
          <li>Add it to your export list to build a bibliography.</li>
        </ul>
      </section>
    </>
  );
}

function ExportHelp() {
  return (
    <>
      <p>Papers you add from the Paper Finder or from a citation's details collect here, ready to export as one bibliography.</p>
      <section>
        <h4>Formats</h4>
        <ul>
          <li><strong>Formatted text</strong>: ready to paste into a document, in APA, IEEE, Harvard, Vancouver or MLA.</li>
          <li><strong>BibTeX (.bib)</strong>: for LaTeX and Overleaf.</li>
          <li><strong>RIS (.ris)</strong>: for Zotero, Mendeley and EndNote.</li>
          <li><strong>CSV</strong>: for a spreadsheet.</li>
        </ul>
      </section>
      <section>
        <h4>Good to know</h4>
        <ul>
          <li>The list lives in this browser tab only, so export it before you close the tab.</li>
          <li>"Export bibliography" on a verification report includes its verified citations (and, if you choose, suspicious ones marked for review) plus this list. Ghost citations are left out and listed as removed.</li>
        </ul>
      </section>
    </>
  );
}
