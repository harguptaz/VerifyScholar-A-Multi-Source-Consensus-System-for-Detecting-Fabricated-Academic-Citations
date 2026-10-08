from app.schemas import Author, ParsedCitation
from app.services.parsing import heuristic_parse
from tests.fakes import DEEP_LEARNING_DOI, crossref_item, openalex_work, seed_deep_learning


def cite(index=1, **kw):
    base = dict(index=index, raw=kw.pop("raw", "raw citation text"))
    base.update(kw)
    return ParsedCitation(**base)


async def test_verified_by_doi(engine, web):
    seed_deep_learning(web)
    web.doaj_issns["0028-0836"] = {"title": "Nature", "eissn": "1476-4687", "publisher": {"name": "Springer"}}
    p = cite(doi=DEEP_LEARNING_DOI, title="Deep learning", authors=[Author(family="LeCun", given="Y."), Author(family="Bengio", given="Y."), Author(family="Hinton", given="G.")],
             journal="Nature", year=2015, volume="521", pages="436-444")
    r = await engine.verify_citation(p)
    assert r.verdict == "verified" and r.confidence >= 90
    assert r.doi_status == "found"
    assert r.links.doi_url == f"https://doi.org/{DEEP_LEARNING_DOI}"
    assert r.links.open_access == "https://example.org/dl.pdf"
    assert r.record and r.record.abstract and "multiple processing layers" in r.record.abstract
    assert {s.source for s in r.sources} == {"crossref", "openalex", "doaj"}
    assert r.journal_status == "verified_in_index"
    assert len(r.calls) >= 3


async def test_wrong_year_and_authors_is_suspicious(engine, web):
    seed_deep_learning(web)
    p = cite(doi=DEEP_LEARNING_DOI, title="Deep learning", authors=[Author(family="Kumar", given="R."), Author(family="Sharma", given="A.")],
             journal="Nature", year=2018, volume="521")
    r = await engine.verify_citation(p)
    assert r.verdict == "suspicious"
    assert 40 <= r.confidence < 80
    assert "year" in r.reason or "authors" in r.reason


async def test_doi_resolves_to_other_paper_is_flagged(engine, web):
    seed_deep_learning(web)
    p = cite(doi=DEEP_LEARNING_DOI, title="Hindi sentiment analysis using transformer models for social media",
             authors=[Author(family="Kumar", given="R."), Author(family="Sharma", given="A.")], journal="IEEE Transactions on Neural Networks", year=2023)
    r = await engine.verify_citation(p)
    assert r.doi_status == "mismatch"
    assert r.verdict == "ghost"
    assert "different paper" in r.reason


async def test_fabricated_citation_is_ghost(engine, web):
    web.doaj_titles = {}
    p = cite(doi="10.1109/ACCESS.2023.1234567", title="Quantum blockchain enabled sentiment oracle for Hindi tweets",
             authors=[Author(family="Verma", given="Priyanka")], journal="IEEE Access", year=2023, volume="45")
    r = await engine.verify_citation(p)
    assert r.verdict == "ghost"
    assert r.doi_status == "not_found"
    codes = {f.code for f in r.flags}
    assert "doi_placeholder" in codes
    assert r.links.doi_url is None  # never link to a DOI that does not resolve


async def test_title_search_without_doi_finds_paper(engine, web):
    seed_deep_learning(web)
    web.cr_search = [web.crossref[DEEP_LEARNING_DOI]]
    web.oa_search = [web.openalex[DEEP_LEARNING_DOI]]
    p = cite(title="Deep learning", authors=[Author(family="LeCun", given="Y.")], journal="Nature", year=2015)
    r = await engine.verify_citation(p)
    assert r.verdict == "verified"
    assert all(s.method == "search" for s in r.sources if s.kind == "record")
    assert r.doi_status == "no_doi"


async def test_approximate_title_match_is_suspicious_not_verified(engine, web):
    web.crossref["10.1/real"] = crossref_item("10.1/real", "Hindi Sentiment Analysis Using BERT-Based Models", [("Kumar", "R"), ("Singh", "P")], "IEEE Transactions on Neural Networks and Learning Systems", 2022, volume="34")
    web.cr_search = [web.crossref["10.1/real"]]
    p = cite(title="Sentiment Analysis of Hindi Text Using Transformers", authors=[Author(family="Kumar", given="R."), Author(family="Sharma", given="A.")],
             journal="IEEE Transactions on Neural Systems", year=2023, volume="45")
    r = await engine.verify_citation(p)
    assert r.verdict in ("suspicious", "ghost")
    assert r.verdict != "verified"


async def test_unavailable_source_is_never_reported_as_ghost(engine, web):
    web.down = {"api.crossref.org", "api.openalex.org"}
    p = cite(doi="10.1/anything", title="Some title that we cannot check right now", authors=[Author(family="Doe", given="J.")], year=2020)
    r = await engine.verify_citation(p)
    assert r.verdict == "inconclusive"
    assert r.confidence is None
    assert "did not respond" in r.reason


async def test_one_source_down_still_verifies(engine, web):
    seed_deep_learning(web)
    web.down = {"api.openalex.org"}
    p = cite(doi=DEEP_LEARNING_DOI, title="Deep learning", authors=[Author(family="LeCun", given="Y.")], journal="Nature", year=2015)
    r = await engine.verify_citation(p)
    assert r.verdict == "verified"


async def test_bare_doi_is_a_lookup(engine, web):
    seed_deep_learning(web)
    r = await engine.verify_citation(cite(doi=DEEP_LEARNING_DOI, raw=DEEP_LEARNING_DOI))
    assert r.verdict == "verified"
    r2 = await engine.verify_citation(cite(doi="10.9999/nope", raw="10.9999/nope"))
    assert r2.verdict == "ghost"


async def test_retraction_is_a_badge_not_a_verdict(engine, web):
    seed_deep_learning(web)
    web.notices[DEEP_LEARNING_DOI] = [{"DOI": "10.1038/retraction-notice", "type": "retraction", "source": "retraction-watch"}]
    p = cite(doi=DEEP_LEARNING_DOI, title="Deep learning", authors=[Author(family="LeCun", given="Y.")], journal="Nature", year=2015)
    r = await engine.verify_citation(p)
    assert r.verdict == "verified"
    assert r.retraction.status == "retracted"
    assert r.retraction.notices[0].source == "retraction-watch"
    assert "retracted" in r.reason


async def test_heuristic_parser_feeds_engine(engine, web):
    seed_deep_learning(web)
    raw = "LeCun, Y., Bengio, Y., & Hinton, G. (2015). Deep learning. Nature, 521(7553), 436-444. https://doi.org/10.1038/nature14539"
    r = await engine.verify_citation(heuristic_parse(raw, 1))
    assert r.verdict == "verified"


async def test_cluster_flags_on_sequential_dois(engine, web):
    items = [cite(index=i + 1, doi=f"10.1109/ACCESS.2023.{3000000 + i}", title=f"Invented title number {i} about quantum nonsense", authors=[Author(family=f"Name{i}", given="Alex")], journal="IEEE Access", year=2023) for i in range(3)]
    results = await engine.verify_many(items)
    for r in results:
        assert r.verdict in ("ghost", "suspicious")
        assert "cluster_sequential_doi" in {f.code for f in r.flags}
