import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app import deps
from app.main import app
from app.services.http_client import http_service
from tests.fakes import DEEP_LEARNING_DOI, FakeWeb, crossref_item, openalex_work, seed_deep_learning
from tests.test_relevance_and_grobid import TEI

PASTED = """[1] LeCun, Y., Bengio, Y., & Hinton, G. (2015). Deep learning. Nature, 521(7553), 436-444. https://doi.org/10.1038/nature14539
[2] Verma, P. (2023). Quantum blockchain enabled sentiment oracle for Hindi tweets. IEEE Access, 45, 1-12. https://doi.org/10.1109/ACCESS.2023.1234567"""


@pytest.fixture
def web():
    w = FakeWeb()
    seed_deep_learning(w)
    return w


@pytest.fixture
def client(web):
    http_service.use_client(httpx.AsyncClient(transport=httpx.MockTransport(web.handler), follow_redirects=True))
    deps.grobid._transport = httpx.MockTransport(lambda r: (_ for _ in ()).throw(httpx.ConnectError("no grobid")))
    with TestClient(app) as c:
        yield c
    deps.grobid._transport = None


def finish(client, job_id):
    job = client.get(f"/jobs/{job_id}").json()
    assert job["status"] == "done", job
    return job


def test_health_and_count(client):
    h = client.get("/health").json()
    assert h["status"] == "ok" and h["grobid"] is False
    assert client.post("/citations/count", json={"text": PASTED}).json() == {"count": 2}
    assert client.post("/citations/count", json={"text": "   "}).json() == {"count": 0}


def test_mode_a_pasted_citations_end_to_end(client):
    r = client.post("/verify/citations", json={"text": PASTED})
    assert r.status_code == 200
    job = finish(client, r.json()["job_id"])
    res = job["result"]
    assert res["summary"]["total"] == 2 and res["summary"]["verified"] == 1 and res["summary"]["ghost"] == 1
    assert any("GROBID is not reachable" in w for w in res["warnings"])  # falls back to the built-in parser
    good, bad = res["results"]
    assert good["verdict"] == "verified" and good["links"]["doi_url"] == f"https://doi.org/{DEEP_LEARNING_DOI}"
    assert good["links"]["open_access"] == "https://example.org/dl.pdf"
    assert bad["verdict"] == "ghost" and bad["doi_status"] == "not_found" and bad["links"]["doi_url"] is None
    assert "calls" not in good  # log material never leaks to the client

    # report exports
    csv_resp = client.get(f"/jobs/{job['id']}/export?format=csv")
    assert csv_resp.status_code == 200 and "verification-report.csv" in csv_resp.headers["content-disposition"]
    assert csv_resp.text.splitlines()[0].startswith("#,citation,verdict")
    pdf = client.get(f"/jobs/{job['id']}/export?format=pdf")
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")

    # transparency log
    log = client.get(f"/jobs/{job['id']}/log").json()
    assert log["chain_valid"] and len(log["entries"]) >= 6 and log["final_hash"] == log["entries"][-1]["entry_hash"]
    assert {e["verdict"] for e in log["entries"]} == {"verified", "ghost"}
    assert client.get(f"/jobs/{job['id']}/log/verify").json()["chain_valid"] is True
    assert client.get(f"/jobs/{job['id']}/log?format=csv").text.splitlines()[0].startswith("seq,timestamp_utc")
    assert client.get(f"/jobs/{job['id']}/log?format=pdf").content.startswith(b"%PDF")


def test_progress_stream_ends_with_done(client):
    job_id = client.post("/verify/citations", json={"text": PASTED}).json()["job_id"]
    with client.stream("GET", f"/jobs/{job_id}/stream") as s:
        body = "".join(s.iter_text())
    assert "event: progress" in body and "event: done" in body
    last = json.loads(body.strip().split("data: ")[-1])
    assert last["status"] == "done" and last["completed"] == 2


def test_input_validation(client):
    assert client.post("/verify/citations", json={"text": "  "}).status_code == 400
    assert client.post("/verify/document", files={"file": ("x.exe", b"abc")}).status_code == 400
    assert client.post("/verify/document", files={"file": ("x.pdf", b"")}).status_code == 400
    assert client.get("/jobs/nope").status_code == 404
    done = client.post("/verify/citations", json={"text": PASTED}).json()["job_id"]
    assert client.get(f"/jobs/{done}/export?format=xml").status_code == 422


def _grobid_ok(client):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/isalive"):
            return httpx.Response(200, text="true")
        return httpx.Response(200, content=TEI)

    deps.grobid._transport = httpx.MockTransport(handler)


def test_mode_b_and_c_document_with_relevance(client, web):
    _grobid_ok(client)
    web.crossref["10.1234/hindi.2022.1"] = crossref_item("10.1234/hindi.2022.1", "Hindi sentiment classification with transformer language models", [("Kumar", "Rahul"), ("Singh", "Priya")],
                                                         "Journal of Language Models", 2022, volume="34", issue="5", page="1234-1245",
                                                         abstract="We fine-tune transformer language models for sentiment classification of Hindi text and report strong results.")
    web.openalex["10.1234/hindi.2022.1"] = openalex_work("10.1234/hindi.2022.1", "Hindi sentiment classification with transformer language models", ["Rahul Kumar", "Priya Singh"], "Journal of Language Models", 2022, "W111", volume="34", pages=("1234", "1245"))
    r = client.post("/verify/document", files={"file": ("paper.pdf", b"%PDF-1.4 fake")}, data={"relevance": "true"})
    job = finish(client, r.json()["job_id"])
    assert job["mode"] == "document+relevance"
    res = job["result"]
    assert res["document"]["title"].startswith("Transformer models") and res["document"]["n_references"] == 3
    assert res["document"]["orphan_indices"] == [3] and res["document"]["unmatched_markers"] == ["[10]"]
    first = res["results"][0]
    assert first["verdict"] == "verified" and first["relevance"]["status"] == "assessed" and first["relevance"]["label"] == "well_supported"
    assert "Hindi text" in first["relevance"]["best_sentence"]
    assert res["results"][2]["relevance"]["orphan"] is True
    assert res["summary"]["orphans"] == 1


def test_mode_b_without_relevance_still_reports_orphans(client, web):
    _grobid_ok(client)
    r = client.post("/verify/document", files={"file": ("paper.pdf", b"%PDF-1.4 fake")}, data={"relevance": "false"})
    res = finish(client, r.json()["job_id"])["result"]
    assert res["document"]["orphan_indices"] == [3]
    assert res["results"][0]["relevance"]["status"] == "not_assessed"


def test_document_job_reports_grobid_failure(client):
    r = client.post("/verify/document", files={"file": ("paper.pdf", b"%PDF-1.4 fake")})
    job = client.get(f"/jobs/{r.json()['job_id']}").json()
    assert job["status"] == "error" and "Could not reach GROBID" in job["error"]


def test_bibtex_import(client):
    bib = b"@article{lecun2015, author={LeCun, Yann and Bengio, Yoshua}, title={Deep learning}, journal={Nature}, year={2015}, volume={521}, pages={436--444}, doi={10.1038/nature14539}}"
    r = client.post("/verify/document", files={"file": ("refs.bib", bib)})
    job = finish(client, r.json()["job_id"])
    assert job["mode"] == "import" and job["result"]["results"][0]["verdict"] == "verified"
    assert job["result"]["results"][0]["parsed"]["parse_method"] == "import"


def test_paper_finder_doi_link_and_title(client, web):
    web.cr_search = [web.crossref[DEEP_LEARNING_DOI]]
    web.oa_search = [web.openalex[DEEP_LEARNING_DOI]]
    for q in (DEEP_LEARNING_DOI, f"https://doi.org/{DEEP_LEARNING_DOI}"):
        body = client.get("/papers/lookup", params={"id": q}).json()
        assert body["query_type"] == "doi" and body["paper"]["verdict"] == "verified"
        assert body["paper"]["access"]["level"] == "open_access" and body["paper"]["access"]["open_access_url"] == "https://example.org/dl.pdf"
        assert body["paper"]["record"]["abstract"].startswith("Deep learning allows")
    body = client.get("/papers/lookup", params={"id": "10.9999/does-not-exist"}).json()
    assert body["paper"]["verdict"] == "ghost" and "No record" in body["message"]
    body = client.get("/papers/lookup", params={"id": "Deep learning LeCun"}).json()
    assert body["query_type"] == "title" and len(body["candidates"]) == 1 and body["paper"] is None


def test_paywalled_paper_gets_publisher_page_access(client, web):
    web.crossref["10.5555/closed"] = crossref_item("10.5555/closed", "A paywalled paper", [("Doe", "Jane")], "Closed Journal", 2020)
    body = client.get("/papers/lookup", params={"id": "10.5555/closed"}).json()
    access = body["paper"]["access"]
    assert access["level"] == "publisher_page" and access["publisher_url"] == "https://doi.org/10.5555/closed" and "library login" in access["note"]


def test_search_merges_and_dedupes(client, web):
    other = crossref_item("10.1/other", "Deep learning in medicine", [("Smith", "A")], "Med AI", 2019, cited=5)
    web.cr_search = [web.crossref[DEEP_LEARNING_DOI], other]
    web.oa_search = [web.openalex[DEEP_LEARNING_DOI]]
    body = client.get("/papers/search", params={"q": "deep learning", "sort": "cited_by"}).json()
    dois = [i["doi"] for i in body["items"]]
    assert dois == [DEEP_LEARNING_DOI, "10.1/other"]  # de-duplicated, most cited first
    assert body["items"][0]["sources"] == ["crossref", "openalex"] and body["items"][0]["is_oa"] is True
    before = len(web.calls)
    only_oa = client.get("/papers/search", params={"q": "deep learning", "oa": "true"}).json()
    assert [i["doi"] for i in only_oa["items"]] == [DEEP_LEARNING_DOI]
    new_calls = web.calls[before:]
    assert new_calls and not any("api.crossref.org" in c for c in new_calls)  # CrossRef cannot filter on OA, so it is skipped


def test_search_reports_a_down_source(client, web):
    web.oa_search = [web.openalex[DEEP_LEARNING_DOI]]
    web.down = {"api.crossref.org"}
    body = client.get("/papers/search", params={"q": "deep learning"}).json()
    assert len(body["items"]) == 1 and any("CrossRef did not respond" in w for w in body["warnings"])


def test_network_endpoints(client, web):
    ref = openalex_work("10.1/ref", "A referenced paper", ["Ann Lee"], "J", 2010, "W222")
    rel = openalex_work("10.1/rel", "A related paper", ["Bo Chan"], "J", 2014, "W333")
    web.openalex["10.1/ref"], web.openalex["10.1/rel"] = ref, rel
    web.openalex[DEEP_LEARNING_DOI]["referenced_works"] = ["https://openalex.org/W222"]
    web.openalex[DEEP_LEARNING_DOI]["related_works"] = ["https://openalex.org/W333"]
    web.oa_search = [web.openalex[DEEP_LEARNING_DOI]]
    refs = client.get("/papers/W2919115771/references").json()
    assert refs["total"] == 1 and refs["items"][0]["title"] == "A referenced paper"
    assert client.get("/papers/W2919115771/related").json()["items"][0]["title"] == "A related paper"
    cited = client.get("/papers/W2919115771/cited-by").json()
    assert cited["kind"] == "cited_by" and cited["total"] == 1
    assert client.get("/papers/W2919115771/nonsense").status_code == 404
    assert client.get("/papers/abc/references").status_code == 400


def test_corrected_citation_endpoint_and_bibliography_export(client, web):
    job_id = client.post("/verify/citations", json={"text": PASTED}).json()["job_id"]
    rec = client.get(f"/jobs/{job_id}").json()["result"]["results"][0]["record"]
    fmt = client.post("/papers/format", json={"record": rec, "style": "ieee"}).json()
    assert fmt["source"] == "local" and fmt["text"].startswith("Y. LeCun") and fmt["bibtex"].startswith("@article{lecun2015deep,")

    extra = {"title": "Another real paper", "authors": [{"family": "Abel", "given": "Zed"}], "journal": "J Things", "year": 2001, "doi": "10.1/extra"}
    out = client.post("/bibliography/export", json={"job_id": job_id, "extra_papers": [extra, rec], "format": "text", "style": "ieee"})
    assert out.status_code == 200 and "bibliography.txt" in out.headers["content-disposition"]
    text = out.text
    assert text.startswith("[1] Y. LeCun") and "[2] Z. Abel" in text  # numbered style keeps the original order, extras appended
    assert text.count("LeCun") == 1  # the duplicate in extra_papers was dropped
    assert "Removed from this list" in text and "#2 [ghost]" in text
    bib = client.post("/bibliography/export", json={"job_id": job_id, "format": "bibtex"})
    assert bib.text.count("@article{") == 1 and "% Removed (not verified): #2" in bib.text
    assert client.post("/bibliography/export", json={"format": "text"}).status_code == 400
    assert client.post("/bibliography/export", json={"job_id": "nope"}).status_code == 404


def test_health_explains_why_grobid_is_unreachable(client):
    h = client.get("/health").json()
    assert h["grobid"] is False and "Cannot connect" in h["grobid_detail"] and h["grobid_url"].startswith("http")
    _grobid_ok(client)
    ok = client.get("/health").json()
    assert ok["grobid"] is True and ok["grobid_detail"] == "GROBID is up"


def test_health_sources_reports_each_source_and_the_reason(client, web):
    web.crossref["10.1038/nature14539"] = crossref_item("10.1038/nature14539", "Deep learning", [("LeCun", "Y")], "Nature", 2015)
    web.openalex["10.1038/nature14539"] = openalex_work("10.1038/nature14539", "Deep learning", ["Yann LeCun"], "Nature", 2015, "W1")
    body = client.get("/health/sources").json()
    assert {k: v["ok"] for k, v in body.items()} == {"crossref": True, "openalex": True, "doaj": True}

    web.down = {"api.openalex.org"}
    body = client.get("/health/sources").json()
    assert body["openalex"]["ok"] is False and "Cannot reach" in body["openalex"]["detail"] and body["crossref"]["ok"] is True
