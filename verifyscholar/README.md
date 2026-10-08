# VerifyScholar

**Check, find and cite real papers.** VerifyScholar verifies AI-generated academic citations against
authoritative bibliographic data, flags fabricated ("ghost") references, checks whether cited papers are
relevant to your own paper, and lets you look up and search papers without leaving the app.

* **Backend:** Python, FastAPI, SQLite
* **Frontend:** React + TypeScript (Vite)
* **Data sources:** CrossRef, OpenAlex, DOAJ (all free)
* **Reference extraction:** GROBID (free, self-hosted, runs in Docker)

---

## Contents

1. [What it does](#1-what-it-does)
2. [How it works](#2-how-it-works)
3. [Before you start](#3-before-you-start)
4. [Setup option A: Docker Compose (recommended)](#4-setup-option-a-docker-compose-recommended)
5. [Setup option B: run each part manually](#5-setup-option-b-run-each-part-manually)
6. [Configuration reference](#6-configuration-reference)
7. [Using the app](#7-using-the-app)
8. [How verdicts and scores are decided](#8-how-verdicts-and-scores-are-decided)
9. [API reference](#9-api-reference)
10. [Tests](#10-tests)
11. [Evaluation harness](#11-evaluation-harness)
12. [Optional extras](#12-optional-extras)
13. [Troubleshooting](#13-troubleshooting)
14. [Project structure and feature map](#14-project-structure-and-feature-map)
15. [Limitations and honest notes](#15-limitations-and-honest-notes)

---

## 1. What it does

| Mode | You give it | You get |
|---|---|---|
| **A. Citation check** | Pasted citation(s) in any style (APA, MLA, IEEE, Vancouver, Harvard) | A verdict per citation (Verified / Suspicious / Ghost), a confidence score, per-source results, a link to the paper |
| **B. Document check** | A PDF or DOCX paper (or a BibTeX / RIS file) | Every reference extracted and verified; references never cited in the text are flagged |
| **C. Relevance check** | The same PDF/DOCX, with "check relevance" ticked | For each cited paper: does its abstract support the sentence that cites it, and is it on-topic for your paper? |
| **D. Paper Finder** | A DOI, `doi.org` link, arXiv link, OpenAlex ID, title or keywords | A paper card (abstract, access links, retraction status), search with filters, and the paper's references / citing papers / related papers |

Also included:

* **Retraction alerts**: papers that were retracted, corrected or flagged with an expression of concern
  (CrossRef, which includes Retraction Watch data, plus OpenAlex). A retracted paper stays "Verified" because
  it exists, but it carries a separate red badge.
* **Corrected citation**: copy the verified citation, built from the source record, in APA, IEEE, Harvard,
  Vancouver or MLA.
* **Clean bibliography export**: BibTeX, RIS, formatted text or CSV, containing the verified citations plus
  anything you added to the session export list. Ghost entries are left out and listed under "Removed".
* **Source transparency log**: every lookup is recorded with a timestamp, the query sent, the response and
  the final verdict. Each entry is hash-chained to the previous one, so tampering can be detected. Export it
  as PDF or CSV to attach to a thesis chapter.
* **Report export** (CSV / PDF), live progress, and an evaluation harness.

## 2. How it works

```
 Input (paste / PDF / DOCX / .bib / .ris / DOI / title)
   |
   |  split + parse                       GROBID (or the built-in parser if GROBID is down)
   v
 Parsed citation: DOI, authors, title, journal, year, volume, pages
   |
   |--> CrossRef : look up the DOI; if missing or it points to a different paper, search by title
   |--> OpenAlex : same, independently
   |--> DOAJ     : is the journal listed? (a hit raises confidence; a miss never lowers it)
   |--> Heuristics: placeholder DOI suffixes, impossible volume/year, near-miss journal names,
   |                clusters of suspicious citations ...
   v
 Field-by-field comparison  ->  per-source score  ->  overall confidence  ->  verdict
   |
   |--> retraction check, paper links, corrected citation
   |--> (Mode C) abstracts + similarity scores
   v
 Report, exports, and the hash-chained transparency log
```

Key design points:

* **A source that cannot be reached is never reported as "not found".** If CrossRef or OpenAlex times out,
  returns an error or rate-limits us, the citation is marked *Inconclusive*, not *Ghost*.
* **GROBID consolidation is deliberately turned off.** Consolidation makes GROBID query CrossRef and "repair"
  the metadata, which would quietly fix hallucinated citations before they are checked.
* **Responses are cached** (24 hours by default) in SQLite to be polite to the free APIs and keep re-runs fast.

## 3. Before you start

### Accounts and keys (all free)

| What | Why | How |
|---|---|---|
| **A contact email** | CrossRef and OpenAlex give faster, more reliable service to callers who identify themselves ("polite pool"). The email is only sent as a `mailto` parameter and in the `User-Agent`. | Just use your real email in `CONTACT_EMAIL`. |
| **An OpenAlex API key** | OpenAlex's documentation recommends a free key (some of its pages describe it as required). It meters usage, and search/list calls draw on a small free allowance. | Create one at <https://openalex.org/settings/api> and put it in `OPENALEX_API_KEY`. |

No key is needed for CrossRef, DOAJ or GROBID.

### Software

| Setup | You need |
|---|---|
| **Docker Compose (option A)** | Docker Desktop (or Docker Engine + the Compose plugin). About 4 GB of free RAM for GROBID and about 3 GB of disk. |
| **Manual (option B)** | Docker (for GROBID only), Python 3.10+ (tested on 3.12), Node.js 18+ and npm. LibreOffice only if you want DOCX uploads without Docker. |

---

## 4. Setup option A: Docker Compose (recommended)

This starts GROBID, the backend and the frontend with one command.

**Step 1. Get the code.** Unzip the project and open a terminal in the `verifyscholar/` folder.

**Step 2. Create your configuration file.**

```bash
cp .env.example .env          # Windows (PowerShell): copy .env.example .env
```

Open `.env` and set at least:

```
CONTACT_EMAIL=your.real.address@example.edu
OPENALEX_API_KEY=your-free-key
```

**Step 3. Build and start everything.**

```bash
docker compose up --build
```

The first build downloads the images (GROBID is large) and installs LibreOffice for DOCX support, so allow
10-20 minutes on a typical connection. Later starts take seconds.

**Step 4. Open the app.**

| Service | URL |
|---|---|
| App (frontend) | <http://localhost:3000> |
| API docs (Swagger) | <http://localhost:8000/docs> |
| API health check | <http://localhost:8000/health> |
| GROBID | <http://localhost:8070> |

**Step 5. Check that it is healthy.** Open <http://localhost:8000/health>. You want:

```json
{"status": "ok", "grobid": true, "libreoffice": true, "openalex_api_key_set": true, "contact_email_set": true}
```

GROBID can take up to a minute after start-up to report `true`. Until it does, pasted citations use the
built-in parser and PDF uploads will fail with a clear message.

**Stop / restart / reset**

```bash
docker compose down            # stop (your database and cache are kept in the vs-data volume)
docker compose up              # start again
docker compose down -v         # stop AND delete the database, cache and transparency logs
```

**Build options** (edit `docker-compose.yml`, `backend.build.args`):

| Argument | Default | Effect |
|---|---|---|
| `INSTALL_LIBREOFFICE` | `true` | `false` skips LibreOffice (about 500 MB smaller); DOCX uploads then return a clear error, PDFs still work. |
| `INSTALL_ML` | `false` | `true` installs `sentence-transformers` (meaning-based relevance scores; adds PyTorch). See [section 12](#12-optional-extras). |

**Changing the API address.** The browser calls the API directly, so `VITE_API_BASE_URL` (in
`docker-compose.yml` under `frontend.build.args`) must be an address your *browser* can reach. If you deploy
on a server, change it from `http://localhost:8000` to the server's address and add the frontend's address to
`CORS_ORIGINS` in `.env`, then rebuild with `docker compose up --build`.

---

## 5. Setup option B: run each part manually

Use this for development. Open three terminals.

### Terminal 1: GROBID

```bash
docker run --rm --init --ulimit core=0 -p 8070:8070 lfoppiano/grobid:0.8.0
```

Wait until `curl http://localhost:8070/api/isalive` prints `true`. (`lfoppiano/grobid` is the lightweight
CRF image. The `grobid/grobid` image is more accurate but needs far more memory and ideally a GPU.)

### Terminal 2: backend

```bash
cd backend

python -m venv .venv
source .venv/bin/activate              # Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -r requirements.txt        # add requirements-dev.txt if you want to run the tests

cp ../.env.example .env                # Windows: copy ..\.env.example .env
# edit .env: set CONTACT_EMAIL and OPENALEX_API_KEY (GROBID_URL is already http://localhost:8070)

uvicorn app.main:app --reload --port 8000
```

Check <http://localhost:8000/health> and <http://localhost:8000/docs>. The SQLite database is created
automatically in `backend/data/`.

**DOCX uploads without Docker:** install LibreOffice so that the `soffice` command is on your PATH
(`sudo apt install libreoffice-writer` on Debian/Ubuntu, `brew install --cask libreoffice` on macOS,
the installer on Windows, then add its `program` folder to PATH). If you skip this, upload PDFs instead.

### Terminal 3: frontend

```bash
cd frontend
cp .env.example .env                   # VITE_API_BASE_URL=http://localhost:8000
npm install
npm run dev
```

Open <http://localhost:5173>. (This origin is already allowed in the default `CORS_ORIGINS`.)

**Production build of the frontend:** `npm run build` creates `frontend/dist/`, which any static web server can serve.

---

## 6. Configuration reference

Settings come from environment variables or the `.env` file (Docker: the project-root `.env`; manual setup:
`backend/.env`). Only the first four normally need changing.

| Variable | Default | Meaning |
|---|---|---|
| `CONTACT_EMAIL` | `you@example.com` | **Set this.** Sent to CrossRef/OpenAlex for polite-pool access. |
| `OPENALEX_API_KEY` | *(empty)* | **Recommended.** Free key from openalex.org/settings/api. |
| `GROBID_URL` | `http://localhost:8070` | Where GROBID runs (Compose sets `http://grobid:8070`). |
| `CORS_ORIGINS` | `http://localhost:5173,http://localhost:3000` | Browser origins allowed to call the API. |
| `DATABASE_URL` | `sqlite:///./data/verifyscholar.db` | Jobs, cache and transparency logs. |
| `EMBEDDING_BACKEND` | `auto` | `auto`, `sentence-transformers` or `tfidf` (see section 12). |
| `USE_GROBID_FOR_CITATIONS` | `true` | `false` = always use the built-in parser for pasted citations. |
| `USE_CSL_FORMATTING` | `true` | `false` = never call `doi.org` for reference formatting (local formatters only). |
| `VERIFIED_THRESHOLD` | `80` | Confidence at or above this is *Verified*. |
| `SUSPICIOUS_THRESHOLD` | `40` | At or above this (and below verified) is *Suspicious*; below is *Ghost*. |
| `MAX_CITATIONS_PER_JOB` | `200` | Upper limit per job. |
| `MAX_UPLOAD_MB` | `25` | Upload size limit. |
| `CACHE_TTL_HOURS` | `24` | How long successful API responses are cached. |
| `CACHE_NEGATIVE_TTL_HOURS` | `2` | How long "not found" answers are cached. |
| `HTTP_TIMEOUT_SECONDS` | `20` | Per-request timeout to the external APIs. |
| `HTTP_MAX_ATTEMPTS` | `3` | Retries (with backoff) for timeouts, 429 and 5xx. |
| `HTTP_MAX_CONCURRENT_REQUESTS` | `8` | Global cap on simultaneous outgoing requests. |
| `MAX_PARALLEL_CITATIONS` | `5` | Citations verified at the same time. |
| `GROBID_TIMEOUT_SECONDS` | `180` | Increase for very large PDFs. |
| `PENALTY_HIGH` / `PENALTY_MEDIUM` / `PENALTY_LOW` / `PENALTY_CAP` | `25` / `12` / `5` / `40` | How much heuristic flags reduce the score. |
| `DOAJ_BONUS`, `AGREEMENT_BONUS` | `5`, `5` | Score bonus for a DOAJ hit / when two sources agree. |
| `SENTENCE_HIGH_ST`, `PAPER_HIGH_ST` | `0.40`, `0.30` | Relevance thresholds for sentence-transformers. |
| `SENTENCE_HIGH_TFIDF`, `PAPER_HIGH_TFIDF` | `0.18`, `0.10` | Relevance thresholds for the TF-IDF fallback. |

## 7. Using the app

### Verify pasted citations (Mode A)

1. Open **Verify citations** and stay on the **Paste citations** tab.
2. Paste your list. A numbered list (`[1]`, `1.`), blank-line separated entries, or one citation per line all work.
   The counter shows *Citation count detected: N*.
3. Click **Verify Citations**. A progress bar shows *Checking citation 3 of 12…*.
4. Read the report. Click a column filter chip (Ghost, Suspicious, …) or change the sort order. Click
   **View detail** on any row to see:
   * your citation next to the source record, field by field (title, authors, journal, year, volume, pages, DOI);
   * the per-source result (CrossRef, OpenAlex, DOAJ) and any fabrication-pattern flags;
   * a plain-English reason, and links to the DOI, the publisher page and a free full-text copy if one exists;
   * **Copy corrected citation** (choose a style first), **Open in Paper Finder**, **Find a replacement**
     (ghost / suspicious rows) and **Add to export list**.
5. Use **Export report** (CSV / PDF), **Export bibliography…**, or the **Source transparency log** tab.

### Verify a whole document (Modes B and C)

1. Choose **Upload a document** and drop a PDF or DOCX (or a `.bib` / `.ris` file).
2. For PDF/DOCX, tick **Also check relevance** to compare each cited paper's abstract with the sentence that
   cites it and with your paper's title and abstract.
3. The report adds a **Relevance** column: *Well supported*, *Possibly misattributed*, *Background / off-topic*,
   *Needs review*, or *Never cited* (listed in the bibliography but not cited in the text).

How to read the relevance labels (they are advisory and never change a verdict):

| Sentence-level | Paper-level | Label | Meaning |
|---|---|---|---|
| high | high | Well supported | On-topic and supports the claim it is cited for |
| low | high | Possibly misattributed | Relevant paper, but perhaps cited for the wrong claim |
| high | low | Background / off-topic | Supports the claim but is off-topic overall; often a legitimate background citation |
| low | low | Needs review | Likely irrelevant |

Scanned (image-only) PDFs have no text layer and are not supported, because GROBID does not do OCR.

### Paper Finder (Mode D)

* **Look up a DOI, link or title:** paste a DOI, a `doi.org` or arXiv link, an OpenAlex ID (`W…`) or a title.
  A DOI gives a full paper card: abstract, verdict, retraction status, and the access options below. A title
  gives a list of candidates to pick from.
* **Search papers:** keywords with filters (years, type, open access only) and sorting (relevance, most
  cited, newest).
* **Access to the paper**, in this order: a free legal full-text copy (open access) → the publisher page via
  `doi.org` → a notice that no free copy exists (your institution's library login may open the publisher
  page). VerifyScholar never hosts or proxies paywalled PDFs.
* **Citation network:** the **References**, **Cited by** and **Related** tabs (powered by OpenAlex).

### Export list and bibliography export

Use **Add to export list** on any paper. The **Export list** tab (and the report's **Export bibliography…**
button) produce BibTeX, RIS, formatted text or CSV. From a report, the export contains the job's verified
citations built from the **source records** (not your original text), suspicious ones marked for review,
retracted ones flagged, plus anything on your list. Ghost citations are left out and listed under "Removed".
IEEE and Vancouver keep your original citation order so in-text numbers still match. The list is stored in
the browser **tab** (sessionStorage), so export it before you close the tab.

### Transparency log

Open the **Source transparency log** tab of a report. Each row shows the time, the query sent, the response and
the verdict. **Re-verify chain** recomputes every hash; **Export log** gives a PDF (with the final hash printed)
or CSV.

---

## 8. How verdicts and scores are decided

For each source that returns a candidate paper, the cited fields are compared with the record and scored:

| Field | Weight | Matching rule |
|---|---|---|
| Title | 40 | Fuzzy similarity (≥0.90 match, ≥0.70 close) |
| Authors | 25 | Surname overlap; the first author must match |
| Journal | 15 | Fuzzy similarity, also understands abbreviations ("IEEE Trans. Neural Netw." = full name) |
| Year | 10 | Exact; one year off (online vs print) counts as close |
| DOI | 10 | Equal, or found by lookup |
| Volume, Pages | 5 + 5 | Exact volume; same first page |

Fields you did not cite are left out and the weights re-normalised. Then:

* any hard mismatch (for example a wrong year) caps that source's score at 79, so it can never be "Verified";
* a cited DOI that resolves to a *different* paper caps it at 35;
* too little to compare (for example only a DOI) caps it at 60.

**Special cases, handled deliberately:**

* **Preprints (arXiv).** An arXiv id in a citation (`arXiv:1607.06450`, `CoRR abs/1409.0473`) is converted to its
  arXiv-issued DOI (`10.48550/arXiv.1607.06450`) and looked up directly. The "journal" (`arXiv preprint`) is a server,
  not a journal, so it is not compared. If the cited year equals the arXiv submission year but a source lists a
  later year, that counts as "close", not a mismatch.
* **Books.** VerifyScholar is built for journal articles and conference papers. A reference that looks like a book
  (an edition such as "(2nd ed.)", or a publisher such as Wiley where a journal would be) is searched by title and
  author, and if nothing is found it is reported as **Inconclusive**, never Ghost, because CrossRef and OpenAlex
  cover books poorly.
* **Placeholder text.** Citations copied from a style guide ("Author, A. A.", "Title of article", "Title of
  Periodical") are flagged as not real references.
* **Generic titles.** A similar title alone is not enough to accept a search result. A short or generic title
  ("Layer normalization") also needs the authors, journal or year to agree, and a result that disagrees on authors,
  journal *and* year is treated as a different paper. If the first search finds nothing acceptable and the year is
  known, a second search restricted to the cited year ±1 is made.
* **Conference names** are compared by their core words, so "In Proc. IEEE Conf. Comput. Vis. Pattern Recognit."
  matches "2016 IEEE Conference on Computer Vision and Pattern Recognition (CVPR)".

In the detail panel, **"closest search results considered"** lists the candidates a search returned and why each
was accepted or rejected, so a surprising verdict can be explained.

**Overall confidence** = the best source score, +5 if two sources agree, +5 if the journal is in DOAJ (never
a penalty for a miss), minus heuristic flag penalties (high 25, medium 12, low 5, total capped at 40).

| Confidence | Verdict | Typical situation |
|---|---|---|
| ≥ 80 | **Verified** | The paper exists and the cited details match |
| 40–79 | **Suspicious** | A real paper was found, but authors, year, volume, pages or the DOI differ |
| < 40 | **Ghost** | No matching record anywhere, or the DOI points to an unrelated paper |
| (none) | **Inconclusive** | A source did not answer, so no conclusion is possible |

**Fabrication-pattern flags** include: a placeholder-looking DOI suffix (such as `…1234567`), a DOI prefix
that CrossRef does not know, an IEEE journal with a non-IEEE DOI prefix, a future publication year, a journal
name that nearly (but not exactly) matches a real one, a year before the journal's earliest indexed article, a
volume higher than the journal's latest, an author who does not exist in OpenAlex, and bibliography-level
clusters (many unverified citations sharing a journal, first author or consecutive DOIs).

All thresholds are configurable in `.env`. Tune them with the evaluation harness.

## 9. API reference

Interactive documentation is at `http://localhost:8000/docs`.

| Method and path | Purpose |
|---|---|
| `GET /health` | Status of the API, GROBID, LibreOffice and key settings |
| `GET /health/sources` | Live test of the connection to CrossRef, OpenAlex and DOAJ, with the reason for any failure |
| `POST /citations/count` | `{ "text": "…" }` → `{ "count": N }` (live counter) |
| `POST /verify/citations` | Mode A: `{ "text": "…" }` → `{ "job_id": "…" }` |
| `POST /verify/document` | Modes B/C: multipart `file` (`.pdf`, `.docx`, `.bib`, `.ris`) and `relevance` (`true`/`false`) → `{ "job_id": "…" }` |
| `GET /jobs/{id}` | Job status and, when done, the full result |
| `GET /jobs/{id}/stream` | Server-Sent Events: `progress`, then `done` or `failed` |
| `GET /jobs/{id}/export?format=csv\|pdf` | Verification report download |
| `GET /jobs/{id}/log?format=json\|csv\|pdf` | Transparency log (JSON includes `chain_valid` and `final_hash`) |
| `GET /jobs/{id}/log/verify` | Recompute the hash chain |
| `GET /papers/lookup?id=…` | Paper Finder: DOI, link, OpenAlex ID or title |
| `GET /papers/search?q=…&year_from=&year_to=&oa=&type=&sort=&page=&per_page=` | Paper search |
| `GET /papers/{openalex_id}/references` · `/cited-by` · `/related` | Citation network (`openalex_id` looks like `W2741809807`) |
| `POST /papers/format` | `{ "record": {…}, "style": "apa" }` → corrected citation text and BibTeX |
| `POST /bibliography/export` | `{ "job_id"?, "extra_papers": […], "format": "text\|bibtex\|ris\|csv", "style": "apa\|ieee\|harvard\|vancouver\|mla", "include_suspicious": true }` → file |

Example:

```bash
curl -s -X POST http://localhost:8000/verify/citations \
  -H 'Content-Type: application/json' \
  -d '{"text": "LeCun, Y., Bengio, Y., & Hinton, G. (2015). Deep learning. Nature, 521(7553), 436-444. https://doi.org/10.1038/nature14539"}'
# -> {"job_id": "…"}   then:   curl -s http://localhost:8000/jobs/<job_id>
```

## 10. Tests

```bash
# backend (85 tests)
cd backend
pip install -r requirements-dev.txt
pytest

# frontend (20 tests) and type check
cd frontend
npm install
npm test
npm run typecheck
```

**Important:** the backend tests run against a *fake* CrossRef / OpenAlex / DOAJ / GROBID (see
`backend/tests/fakes.py`), so they need no network and no GROBID, but they cannot prove that the real services
answer exactly as modelled. After your first real run, do the checklist in
[section 15](#15-limitations-and-honest-notes) and run the evaluation harness.

## 11. Evaluation harness

`backend/evaluation/dataset.json` holds 41 seed citations: **real** papers cited correctly, **corrupted** ones
(right paper, wrong year / authors / volume / journal) and **fabricated** ones (invented titles and DOIs).
The harness runs the real engine against the live APIs and reports the metrics that matter for your report.

```bash
cd backend
python -m evaluation.run --fresh --out results.json
```

It prints the ghost-detection rate, the share of fabricated citations flagged at all, the share of corrupted
citations caught, the false-flag rate on real papers, and the ghost precision, then lists every citation whose
verdict differs from its label so that you can tune thresholds in `.env`. `--fresh` ignores the HTTP cache.

The seed set is a starting point. Add your own entries (up to roughly 50), but only mark a paper `"real"` after you
have checked it yourself. Conference papers and citations with abbreviated venue names are the hardest cases.

## 12. Optional extras

### Meaning-based relevance scores (sentence-transformers)

Without it, relevance uses a simple word-overlap (TF-IDF) similarity that only sees shared vocabulary. That is
fine for demos but weak for final results. To enable the real model:

```bash
cd backend
pip install torch --index-url https://download.pytorch.org/whl/cpu   # CPU build; avoids a multi-GB CUDA download
pip install -r requirements-ml.txt
```

Keep `EMBEDDING_BACKEND=auto`. The model (`all-MiniLM-L6-v2`, about 90 MB) downloads on the first relevance run.
With Docker, build with `INSTALL_ML: "true"`. The result screen says which backend was used.

### Using BibTeX / RIS files

Upload a `.bib` or `.ris` export from Zotero or Mendeley on the **Upload a document** tab. They are verified like
any other list (no relevance check, since there is no body text).

## 13. Troubleshooting

| Symptom | Likely cause and fix |
|---|---|
| Red banner "Cannot reach the VerifyScholar API" | Backend not running, or `VITE_API_BASE_URL` points to the wrong address. Open `/health` in the browser to test. |
| Browser console shows a CORS error | Add the frontend's address to `CORS_ORIGINS` and restart the backend. |
| Blue banner "The backend cannot reach GROBID right now" | The banner comes from the backend's own check (`/health`), not from the page. The page re-checks every few seconds; GROBID can take 1-2 minutes to start (longer on Apple Silicon, where the image runs under emulation). The banner text says why. Pasted citations still work meanwhile (built-in parser); only PDF/DOCX uploads need GROBID. If it never clears: `docker compose ps`, `docker compose logs grobid`, give Docker at least 4 GB of memory, and check that `GROBID_URL` is `http://grobid:8070` inside Compose. |
| I clicked *Verify Citations* and nothing seemed to happen | The progress bar and the report appear **below** the two input panels; the page scrolls to them automatically, but scroll down if it did not. If there is still nothing: open the browser's developer tools (F12) → **Network** tab and click Verify again. You should see `POST /verify/citations` (status 200) followed by `/jobs/<id>/stream`. A red or failed request tells you what is wrong. Then run `docker compose logs -f backend` while you click Verify. |
| The report appears but every citation is *Inconclusive* | The backend cannot reach the data sources. Click **Test connection to data sources** (Verify page, right-hand panel) or open <http://localhost:8000/health/sources>. It reports CrossRef, OpenAlex and DOAJ separately and says whether the problem is no internet access from the container, a refused request (HTTP 401/403 usually means a wrong or missing `OPENALEX_API_KEY`), or something else. |
| Document job fails: "Could not reach GROBID" | GROBID is down, or `GROBID_URL` is wrong. Inside Compose it must be `http://grobid:8070`. |
| Document job fails: "No reference list was found" | The PDF has no extractable bibliography, or is a scanned image. |
| DOCX upload error mentioning LibreOffice | Install LibreOffice (section 5), build the image with `INSTALL_LIBREOFFICE=true`, or upload a PDF. |
| A real paper is marked *Ghost* | It may not be indexed by CrossRef/OpenAlex (older, regional or non-English venues), or the search did not surface it. Open the detail panel and expand **closest search results considered** to see what was found and why it was rejected. Papers cited with an arXiv id, a DOI or a distinctive title are the most reliable. Verdicts are advisory. |
| A book is marked *Inconclusive* | Intended: books are not what VerifyScholar checks (see section 8). Check the publisher's catalogue, WorldCat or Google Books. |
| A real paper is marked *Suspicious* | Open the detail panel: usually an abbreviated journal name, an online-vs-print year, or a page range written differently. |
| "OpenAlex did not respond" warnings | Missing or exhausted OpenAlex key or allowance. The app caches results and falls back to CrossRef where it can. |
| First relevance run is slow | The sentence-transformers model is downloading (only if installed). |
| Large PDFs time out | Raise `GROBID_TIMEOUT_SECONDS`, and give Docker more memory. |
| Port already in use | Change the published port in `docker-compose.yml`, or stop whatever uses 3000, 8000 or 8070. |
| Frontend shows old data after code changes | Hard-reload the page; with Docker, rebuild (`docker compose up --build`). |

## 14. Project structure and feature map

```
verifyscholar/
├── docker-compose.yml        GROBID + backend + frontend
├── .env.example              copy to .env (Docker) or backend/.env (manual)
├── backend/
│   ├── app/
│   │   ├── main.py, config.py, database.py, schemas.py, deps.py
│   │   ├── routers/          verify.py (A/B/C, jobs, exports), papers.py (D), bibliography.py
│   │   ├── services/
│   │   │   ├── adapters/     crossref.py, openalex.py, doaj.py, base.py          (F6)
│   │   │   ├── http_client.py   retries, cache, concurrency, polite pool        (F11)
│   │   │   ├── splitter.py   splitting + BibTeX/RIS parsing                    (F1, F5)
│   │   │   ├── parsing.py    built-in citation parser                          (F4)
│   │   │   ├── grobid.py     GROBID client + TEI parsing                       (F2, F4, F16)
│   │   │   ├── docx_convert.py  DOCX → PDF via LibreOffice                     (F3)
│   │   │   ├── verifier.py   DOI-first lookup, search, DOAJ, retraction        (F7-F9, F34)
│   │   │   ├── matching.py   field-by-field comparison                         (F10)
│   │   │   ├── scoring.py    scores, verdicts, reasons                         (F13)
│   │   │   ├── heuristics.py fabrication-pattern detector                      (F12)
│   │   │   ├── merge.py      combining records and retraction signals          (F14)
│   │   │   ├── relevance.py, embeddings.py   relevance analysis                (F16-F20)
│   │   │   ├── papers.py     Paper Finder, search, network                     (F31-F33)
│   │   │   ├── formatters.py citations, BibTeX, RIS, bibliography export       (F15, F35, F36)
│   │   │   ├── transparency.py  hash-chained log                               (F26, F27)
│   │   │   ├── reports.py    CSV / PDF exports                                 (F25)
│   │   │   └── jobs.py       background jobs and progress                      (F24)
│   ├── tests/                85 tests (fake CrossRef/OpenAlex/DOAJ/GROBID)
│   ├── evaluation/           dataset.json, run.py, metrics.py                  (F30)
│   ├── Dockerfile, requirements*.txt, pytest.ini
└── frontend/
    ├── src/
    │   ├── api.ts, types.ts, format.ts, exportList.tsx, App.tsx, styles.css
    │   ├── components/       VerifyPage, JobProgress, Report, DetailPanel, TransparencyLog,
    │   │                     PaperFinder, PaperCard, PaperList, BibliographyExport, ExportListPanel, Badges
    │   └── test/             20 component and API-client tests
    ├── Dockerfile, nginx.conf, package.json
```

## 15. Limitations and honest notes

* **Tested against fakes.** The development environment could not reach CrossRef, OpenAlex, DOAJ or GROBID, and
  had no browser or Docker, so the Docker images and the UI's visual appearance were never run there. The API
  calls follow the services' public documentation, and the adapters retry without the optional `select` field
  if a service rejects it. **Do this on your first run:**
  1. `/health` shows `grobid: true`.
  2. Paste the example citation from the placeholder text; it should be *Verified*.
  3. Look up `10.1038/nature14539` in Paper Finder; you should see an abstract and a link.
  4. Paste a made-up citation with a made-up DOI; it should be *Ghost*.
  5. Open the transparency log and press **Re-verify chain**.
  6. Run the evaluation harness, and note anything it flags.
  If something differs, the log shows the exact query and response for each call.
* **Coverage gaps.** CrossRef and OpenAlex do not index everything. "Ghost" means "no matching record found",
  which is strong evidence but not proof of fabrication. DOAJ lists open-access journals only, so a miss there
  is meaningless on its own.
* **Retraction data lags** real events, so "no retraction found" does not guarantee a paper is clean.
  If you publish results that use Retraction Watch data, Crossref asks that you cite it as the source.
* **Relevance is advisory.** It compares text similarity, not scientific content. Background citations
  can legitimately score low. It needs an abstract, and not every paper has one in CrossRef or OpenAlex.
* **Scanned PDFs** are not supported, and reference extraction accuracy depends on GROBID and the PDF layout.
* **The export list and results are per browser tab / per job.** There are no user accounts. Jobs and logs are
  stored in the SQLite database (`vs-data` volume in Docker).
* **Legal access only.** The app links to open-access copies and publisher pages. It does not host, proxy
  or link to paywalled PDFs from shadow libraries.
* **Books and other non-article works** (theses, reports, web pages, standards) are not verified; books are
  reported as Inconclusive. Reference extraction and matching are tuned for journal articles, conference papers and
  preprints.
* **Not implemented** (possible future work): ORCID, IEEE Xplore, Web of Science and Scopus checks; user
  accounts; a saved reading list; browser or Word extensions.
* **Be a good API citizen.** Keep `CONTACT_EMAIL` real, keep the cache on, and avoid very large batches.

---

Data comes from [CrossRef](https://www.crossref.org), [OpenAlex](https://openalex.org) and
[DOAJ](https://doaj.org). Reference extraction uses [GROBID](https://github.com/kermitt2/grobid).
