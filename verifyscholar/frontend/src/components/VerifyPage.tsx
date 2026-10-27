import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import type { JobResult, JobStatus } from "../types";
import { ConnectionCheck } from "./ConnectionCheck";
import { JobProgress } from "./JobProgress";
import { Report } from "./Report";

const PLACEHOLDER = `Paste your citation list here: any format (APA / MLA / IEEE / Vancouver / Harvard)

[1] LeCun, Y., Bengio, Y., & Hinton, G. (2015). Deep learning. Nature, 521(7553), 436-444. https://doi.org/10.1038/nature14539`;

interface Props {
  onOpenFinder: (kind: "lookup" | "search", q: string) => void;
  onJobDone: (jobId: string) => void;
}

export function VerifyPage({ onOpenFinder, onJobDone }: Props) {
  const [mode, setMode] = useState<"paste" | "upload">("paste");
  const [text, setText] = useState("");
  const [count, setCount] = useState(0);
  const [file, setFile] = useState<File | null>(null);
  const [relevance, setRelevance] = useState(false);
  const [jobId, setJobId] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<{ id: string; data: JobResult } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const progressRef = useRef<HTMLDivElement>(null);
  const resultRef = useRef<HTMLDivElement>(null);

  // The progress bar and the report sit below the input panels: bring them into view.
  useEffect(() => {
    if (running) progressRef.current?.scrollIntoView?.({ behavior: "smooth", block: "start" });
  }, [running]);
  useEffect(() => {
    if (result) resultRef.current?.scrollIntoView?.({ behavior: "smooth", block: "start" });
  }, [result]);

  // live "citations detected" counter, debounced
  useEffect(() => {
    if (!text.trim()) {
      setCount(0);
      return;
    }
    const t = window.setTimeout(() => {
      api.countCitations(text).then((r) => setCount(r.count)).catch(() => undefined);
    }, 350);
    return () => window.clearTimeout(t);
  }, [text]);

  const isDocFile = !!file && /\.(pdf|docx)$/i.test(file.name);
  const canSubmit = !running && (mode === "paste" ? text.trim().length > 0 : !!file);

  const submit = async () => {
    setError(null);
    setResult(null);
    setJobId(null); // never show the previous job's progress
    setRunning(true);
    try {
      const res = mode === "paste" ? await api.verifyCitations(text) : await api.verifyDocument(file as File, relevance && isDocFile);
      setJobId(res.job_id);
    } catch (e) {
      setRunning(false);
      setError(e instanceof Error ? e.message : "Something went wrong");
    }
  };

  const finished = (job: JobStatus) => {
    setRunning(false);
    if (job.status === "done" && job.result) {
      setResult({ id: job.id, data: job.result });
      onJobDone(job.id);
    } else {
      setError(job.error || "The job failed.");
    }
  };

  return (
    <div>
      <div className="two-col">
        <div className="card">
          <div className="tabs">
            <button className={mode === "paste" ? "tab active" : "tab"} onClick={() => setMode("paste")}>Paste citations</button>
            <button className={mode === "upload" ? "tab active" : "tab"} onClick={() => setMode("upload")}>Upload a document</button>
          </div>

          {mode === "paste" ? (
            <>
              <textarea className="paste" value={text} onChange={(e) => setText(e.target.value)} placeholder={PLACEHOLDER} rows={11} aria-label="Citation list" />
              <div className="counter">Citation count detected: <strong>{count}</strong></div>
            </>
          ) : (
            <div className="upload">
              <div
                className="dropzone"
                onClick={() => fileInput.current?.click()}
                onDragOver={(e) => e.preventDefault()}
                onDrop={(e) => {
                  e.preventDefault();
                  if (e.dataTransfer.files[0]) setFile(e.dataTransfer.files[0]);
                }}
              >
                {file ? <><strong>{file.name}</strong><span className="muted small">{(file.size / 1024).toFixed(0)} KB · click to change</span></> : <><strong>Drop a file here or click to choose</strong><span className="muted small">PDF or DOCX paper · BibTeX (.bib) · RIS (.ris)</span></>}
                <input ref={fileInput} type="file" accept=".pdf,.docx,.bib,.ris" hidden onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
              </div>
              <label className={`check ${isDocFile ? "" : "disabled"}`}>
                <input type="checkbox" checked={relevance && isDocFile} disabled={!isDocFile} onChange={(e) => setRelevance(e.target.checked)} />
                Also check relevance: compare each cited paper's abstract with the sentence that cites it and with your paper
              </label>
              <p className="muted small">References are extracted with GROBID. Scanned (image-only) PDFs are not supported.</p>
            </div>
          )}

          <button className="btn primary big" onClick={submit} disabled={!canSubmit}>{running ? "Verifying…" : "Verify Citations"}</button>
          {error && <div className="notice error">{error}</div>}
        </div>

        <div className="card info-card">
          <h3>How it works</h3>
          <p>VerifyScholar checks each citation against authoritative bibliographic data instead of trusting how it looks.</p>
          <ul>
            <li><strong>CrossRef</strong>: the DOI registration agency; resolves DOIs and searches by title.</li>
            <li><strong>OpenAlex</strong>: an independent index that also covers preprints and works CrossRef misses.</li>
            <li><strong>DOAJ</strong>: confirms the journal exists (open-access journals only; a miss is never held against a citation).</li>
            <li><strong>DBLP, DataCite and Open Library</strong>: asked only when the sources above leave a citation unsettled. DBLP covers computer-science papers, DataCite covers arXiv, Zenodo and dataset DOIs, and Open Library covers books. They can confirm that a work exists; a miss there is never held against a citation.</li>
          </ul>
          <p>You get a verdict for each citation:</p>
          <ul className="verdicts">
            <li><span className="badge verdict-verified">Verified</span> the paper exists and the details match.</li>
            <li><span className="badge verdict-suspicious">Suspicious</span> a real paper was found but details differ.</li>
            <li><span className="badge verdict-ghost">Ghost</span> no matching paper was found.</li>
          </ul>
          <p className="muted small">Works on any citation style. Every lookup is recorded in a tamper-evident transparency log you can export.</p>
          <h4>Not getting results?</h4>
          <ConnectionCheck />
        </div>
      </div>

      {running && (
        <div ref={progressRef}>
          {jobId ? <JobProgress jobId={jobId} onFinished={finished} /> : <div className="card progress-card" role="status">Sending your citations to the server…</div>}
        </div>
      )}
      {result && <div ref={resultRef}><Report jobId={result.id} result={result.data} onOpenFinder={onOpenFinder} /></div>}
    </div>
  );
}