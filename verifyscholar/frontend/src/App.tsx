import { useCallback, useEffect, useState } from "react";
import { API_BASE, api } from "./api";
import { ExportListProvider, useExportList } from "./exportList";
import type { FinderSeed, HealthResponse } from "./types";
import { ExportListPanel } from "./components/ExportListPanel";
import { PaperFinder } from "./components/PaperFinder";
import { VerifyPage } from "./components/VerifyPage";

type Tab = "verify" | "finder" | "export";

function Shell() {
  const list = useExportList();
  const [tab, setTab] = useState<Tab>("verify");
  const [seed, setSeed] = useState<FinderSeed | null>(null);
  const [lastJobId, setLastJobId] = useState<string | null>(null);
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [apiDown, setApiDown] = useState(false);

  const refreshHealth = useCallback(async () => {
    try {
      setHealth(await api.health());
      setApiDown(false);
    } catch {
      setApiDown(true);
    }
  }, []);

  useEffect(() => {
    void refreshHealth();
  }, [refreshHealth]);

  // GROBID (and the API itself, right after "docker compose up") can take a minute or two to start:
  // keep re-checking until they are reachable instead of showing a stale banner.
  const grobidUp = health?.grobid;
  useEffect(() => {
    if (!apiDown && grobidUp !== false) return;
    const t = window.setInterval(() => void refreshHealth(), 5000);
    return () => window.clearInterval(t);
  }, [apiDown, grobidUp, refreshHealth]);

  const openFinder = useCallback((kind: "lookup" | "search", q: string) => {
    setSeed({ kind, q, nonce: Date.now() });
    setTab("finder");
  }, []);

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand"><span className="logo">✓</span> VerifyScholar <span className="tagline">Check, find and cite real papers</span></div>
        <nav className="nav">
          <button className={tab === "verify" ? "nav-btn active" : "nav-btn"} onClick={() => setTab("verify")}>Verify citations</button>
          <button className={tab === "finder" ? "nav-btn active" : "nav-btn"} onClick={() => setTab("finder")}>Paper Finder</button>
          <button className={tab === "export" ? "nav-btn active" : "nav-btn"} onClick={() => setTab("export")}>Export list ({list.items.length})</button>
        </nav>
      </header>

      <main className="container">
        {apiDown && <div className="notice error">Cannot reach the VerifyScholar API at {API_BASE}. Start the backend (see the README) and reload this page.</div>}
        {health && !health.contact_email_set && <div className="notice warn">Set CONTACT_EMAIL in the backend .env so CrossRef and OpenAlex can serve you reliably.</div>}
        {health && !health.grobid && (
          <div className="notice info">
            <strong>The backend cannot reach GROBID right now.</strong> {health.grobid_detail} This page re-checks every few seconds.
            Until it is reachable, pasted citations are parsed by the built-in parser (less accurate) and PDF/DOCX uploads will fail.{" "}
            <button className="link" onClick={() => void refreshHealth()}>Check again</button>
          </div>
        )}

        {/* keep all tabs mounted so results survive switching tabs */}
        <div hidden={tab !== "verify"}><VerifyPage onOpenFinder={openFinder} onJobDone={setLastJobId} /></div>
        <div hidden={tab !== "finder"}><PaperFinder seed={seed} /></div>
        <div hidden={tab !== "export"}><ExportListPanel lastJobId={lastJobId} /></div>
      </main>

      <footer className="footer">
        Verdicts are advisory. A missing record can mean a paper is not indexed, not only that it is fabricated. Data from CrossRef, OpenAlex, DOAJ, DBLP, DataCite and Open Library.
      </footer>
    </div>
  );
}

export default function App() {
  return (
    <ExportListProvider>
      <Shell />
    </ExportListProvider>
  );
}