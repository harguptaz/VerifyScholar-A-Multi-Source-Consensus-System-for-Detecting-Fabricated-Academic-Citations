import httpx
import pytest

from app.schemas import Author, FieldMatch, HeuristicFlag, PaperRecord, ParsedCitation, SourceResult
from app.services import heuristics, scoring
from app.services.docx_convert import ConversionError, docx_to_pdf, libreoffice_binary
from app.services.http_client import HttpService
from app.services.matching import abbreviation_similarity, compare
from app.services.parsing import heuristic_parse, parse_authors
from app.services.splitter import parse_bibtex, parse_ris, split_citations
from app.utils.text import arxiv_to_doi, clean_doi, extract_doi, similarity


# ------------------------------------------------------------------ text utilities
def test_doi_extraction_and_cleaning():
    assert extract_doi("see https://doi.org/10.1109/ACCESS.2023.1234567.") == "10.1109/access.2023.1234567"
    assert extract_doi("(doi:10.1000/xyz123)") == "10.1000/xyz123"
    assert extract_doi("https://link.springer.com/article/10.1007/s00521-020-05001-1") == "10.1007/s00521-020-05001-1"
    assert extract_doi("no identifier here") is None
    assert clean_doi("DOI: 10.1038/NATURE14539") == "10.1038/nature14539"
    assert arxiv_to_doi("https://arxiv.org/abs/1706.03762v5") == "10.48550/arxiv.1706.03762"


def test_similarity_behaviour():
    assert similarity("Attention is all you need", "Attention Is All You Need.") == 1.0
    assert similarity("Deep learning", "Baking sourdough bread at home") < 0.3
    assert 0.6 < similarity("Hindi Sentiment Analysis Using BERT-Based Models", "Sentiment Analysis of Hindi Text Using Transformers") < 0.85
    assert similarity("Deep", "Deep learning for the analysis of a very long collection of unrelated things") < 0.7  # subset trick is damped


# ------------------------------------------------------------------ HTTP service
def make_http(handler, **settings_over):
    from app.config import get_settings

    s = get_settings().model_copy(update={"http_max_attempts": 3, **settings_over})
    svc = HttpService(s)
    svc.use_client(httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    return svc


async def test_http_retries_server_errors_then_succeeds():
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        return httpx.Response(503) if calls["n"] < 3 else httpx.Response(200, json={"ok": True})

    r = await make_http(handler).get_json("https://x.test/a", use_cache=False)
    assert r.status == "ok" and r.data == {"ok": True} and calls["n"] == 3


async def test_http_persistent_failure_is_unavailable_not_not_found():
    r = await make_http(lambda req: httpx.Response(503)).get_json("https://x.test/a", use_cache=False)
    assert r.status == "unavailable"
    r = await make_http(lambda req: (_ for _ in ()).throw(httpx.ConnectTimeout("slow"))).get_json("https://x.test/b", use_cache=False)
    assert r.status == "unavailable" and "ConnectTimeout" in r.error


async def test_http_404_is_not_found_and_auth_errors_are_explained():
    assert (await make_http(lambda req: httpx.Response(404)).get_json("https://x.test/c", use_cache=False)).status == "not_found"
    r = await make_http(lambda req: httpx.Response(403)).get_json("https://x.test/d", use_cache=False)
    assert r.status == "unavailable" and "API key" in r.error


async def test_http_cache_hits_skip_the_network_and_ignore_credentials():
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        return httpx.Response(200, json={"v": calls["n"]})

    svc = make_http(handler)
    a = await svc.get_json("https://x.test/e", params={"q": "1", "api_key": "AAA", "mailto": "a@b.c"})
    b = await svc.get_json("https://x.test/e", params={"q": "1", "api_key": "BBB", "mailto": "z@z.z"})
    assert a.data == b.data == {"v": 1} and b.from_cache and calls["n"] == 1
    await svc.get_json("https://x.test/e", params={"q": "2"})
    assert calls["n"] == 2


async def test_http_does_not_cache_failures():
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        return httpx.Response(503) if calls["n"] <= 3 else httpx.Response(200, json={"ok": 1})

    svc = make_http(handler)
    assert (await svc.get_json("https://x.test/f")).status == "unavailable"
    assert (await svc.get_json("https://x.test/f")).status == "ok"


# ------------------------------------------------------------------ splitting
def test_split_numbered_blank_line_and_wrapped_input():
    assert split_citations('[1] A. B, "T," J., 2020.\n[2] C. D, "U," J., 2021.') == ['A. B, "T," J., 2020.', 'C. D, "U," J., 2021.']
    assert len(split_citations("1. Smith J. Title. J. 2020.\n2. Jones K. Other. J. 2019.")) == 2
    assert len(split_citations("Smith, J. (2020). One. Journal.\n\nJones, K. (2019). Two. Journal.")) == 2
    wrapped = "Smith, J. (2020). A long title that wraps\nover two lines. Journal of X, 1(2), 3-4.\nJones, K. (2019). Another. Journal Y, 5, 6-7."
    out = split_citations(wrapped)
    assert len(out) == 2 and "wraps over two lines" in out[0]
    assert split_citations("") == [] and split_citations("   \n ") == []
    assert len(split_citations("Just one reference (2020). Title. Journal.")) == 1


def test_bibtex_and_ris_parsing():
    bib = "@article{k1, author = {Doe, Jane and Roe, Richard}, title = {A {Nested} Title}, journal = \"J. Tests\", year = 2020, volume = {5}, number = {2}, pages = {10--20}, doi = {10.1/x}}\n@comment{ignored}"
    e = parse_bibtex(bib)
    assert len(e) == 1 and e[0]["title"] == "A Nested Title" and e[0]["authors"] == ["Doe, Jane", "Roe, Richard"] and e[0]["year"] == "2020" and e[0]["pages"] == "10--20"
    ris = "TY  - JOUR\nAU  - Doe, Jane\nTI  - A title\nJO  - J. Tests\nPY  - 2020///\nVL  - 5\nSP  - 10\nEP  - 20\nDO  - 10.1/x\nER  - \n\nTY  - JOUR\nAU  - Roe, R\nTI  - Second\nPY  - 2019\nER  - "
    r = parse_ris(ris)
    assert len(r) == 2 and r[0]["pages"] == "10-20" and r[0]["year"] == "2020" and r[1]["title"] == "Second"


# ------------------------------------------------------------------ heuristic parser
@pytest.mark.parametrize("raw,fams,journal,year,vol", [
    ('R. Kumar, A. Sharma, and P. Singh, "Hindi sentiment analysis," IEEE Trans. Neural Netw. Learn. Syst., vol. 34, no. 5, pp. 1234-1245, 2022.', ["Kumar", "Sharma", "Singh"], "IEEE Trans. Neural Netw. Learn. Syst", 2022, "34"),
    ("Kumar, R., & Singh, P. (2022). Hindi sentiment analysis. IEEE Transactions on Neural Networks, 34(5), 1234-1245.", ["Kumar", "Singh"], "IEEE Transactions on Neural Networks", 2022, "34"),
    ("Kumar R, Singh P. Hindi sentiment analysis. IEEE Trans Neural Netw. 2022;34(5):1234-1245.", ["Kumar", "Singh"], "IEEE Trans Neural Netw", 2022, "34"),
    ('Kumar, Rahul, and Priya Singh. "Hindi Sentiment Analysis." IEEE Transactions on Neural Networks, vol. 34, no. 5, 2022, pp. 1234-45.', ["Kumar", "Singh"], "IEEE Transactions on Neural Networks", 2022, "34"),
    ("Kumar, R. and Singh, P. (2022) 'Hindi sentiment analysis', IEEE Transactions on Neural Networks, 34(5), pp. 1234-1245.", ["Kumar", "Singh"], "IEEE Transactions on Neural Networks", 2022, "34"),
])
def test_heuristic_parser_across_styles(raw, fams, journal, year, vol):
    p = heuristic_parse(raw, 1)
    assert [a.family for a in p.authors] == fams and p.journal == journal and p.year == year and p.volume == vol
    assert p.title and "sentiment analysis" in p.title.lower()


def test_author_edge_cases():
    assert [a.family for a in parse_authors("Smith, J., Jones, K., et al.")] == ["Smith", "Jones"]
    assert [a.family for a in parse_authors("van der Berg J, Li W")] == ["van der Berg", "Li"]


# ------------------------------------------------------------------ matching and scoring
def rec(**kw):
    base = dict(title="Hindi Sentiment Analysis Using BERT", authors=[Author(family="Kumar", given="Rahul"), Author(family="Singh", given="Priya")],
                journal="IEEE Transactions on Neural Networks and Learning Systems", journal_aliases=["IEEE Trans. Neural Netw. Learn. Syst."], year=2022, volume="34", pages="1234-1245", doi="10.1/real")
    base.update(kw)
    return PaperRecord(**base)


def test_abbreviation_similarity():
    full = "IEEE Transactions on Neural Networks and Learning Systems"
    assert abbreviation_similarity("IEEE Trans. Neural Netw. Learn. Syst.", full) == 1.0
    assert abbreviation_similarity("Nature", "Nature Communications") <= 0.5


def test_compare_fields():
    c = ParsedCitation(index=1, raw="x", title="Hindi sentiment analysis using BERT", authors=[Author(family="Kumar")], journal="IEEE Trans Neural Netw Learn Syst",
                       year=2023, volume="34", pages="1234-45", doi="10.1/real")
    cmp = compare(c, rec(), matched_by="search")
    f = cmp.fields
    assert f["title"].status == "match" and f["authors"].status == "match" and f["journal"].status == "match"
    assert f["year"].status == "partial"  # one year off (online vs print) is not a hard mismatch
    assert f["volume"].status == "match" and f["pages"].status == "match" and f["doi"].status == "match"
    bad = compare(ParsedCitation(index=1, raw="x", authors=[Author(family="Nguyen")], year=2010, volume="9"), rec())
    assert bad.fields["authors"].status == "mismatch" and bad.fields["year"].status == "mismatch" and bad.fields["volume"].status == "mismatch"
    assert set(bad.mismatched) == {"authors", "year", "volume"}


def fm(status, sim=None):
    return FieldMatch(submitted="s", found="f", similarity=sim, status=status)


def test_source_score_rules():
    perfect = {"title": fm("match", 1.0), "authors": fm("match", 1.0), "journal": fm("match", 1.0), "year": fm("match", 1.0)}
    assert scoring.source_score(perfect) == 100
    wrong_year = dict(perfect, year=fm("mismatch", 0.0))
    assert 60 <= scoring.source_score(wrong_year) <= 79  # a hard disagreement can never be "verified"
    assert scoring.source_score(perfect, doi_mismatch=True) == 35
    thin = {"title": fm("missing"), "authors": fm("missing"), "doi": fm("match", 1.0)}
    assert scoring.source_score(thin) == 60 and scoring.source_score(thin, lookup_only=True) == 100


def test_overall_score_and_verdict_bands(settings):
    def src(source, status, score, kind="record"):
        return SourceResult(source=source, label=source, kind=kind, status=status, score=score)

    verified = scoring.score_result([src("crossref", "match", 95), src("openalex", "match", 95), src("doaj", "no_record", 0, "journal")], [], settings)
    assert verified.verdict == "verified" and verified.confidence == 100  # +agreement bonus, capped
    sus = scoring.score_result([src("crossref", "partial", 60), src("openalex", "no_record", 0)], [], settings)
    assert sus.verdict == "suspicious" and sus.confidence == 60
    penalised = scoring.score_result([src("crossref", "match", 85)], [HeuristicFlag(code="a", severity="high", message="m"), HeuristicFlag(code="b", severity="medium", message="m")], settings)
    assert penalised.confidence == 85 - 37 and penalised.verdict == "suspicious"
    ghost = scoring.score_result([src("crossref", "no_record", 0), src("openalex", "no_record", 0)], [], settings)
    assert ghost.verdict == "ghost"
    unknown = scoring.score_result([src("crossref", "no_record", 0), src("openalex", "unavailable", None)], [], settings)
    assert unknown.verdict == "inconclusive" and unknown.confidence is None  # never a ghost when a source did not answer
    doaj = scoring.score_result([src("crossref", "partial", 78), src("doaj", "match", 100, "journal")], [], settings)
    assert doaj.confidence == 83 and doaj.verdict == "verified"  # DOAJ can raise confidence...
    no_doaj = scoring.score_result([src("crossref", "partial", 78), src("doaj", "no_record", 0, "journal")], [], settings)
    assert no_doaj.confidence == 78  # ...but a DOAJ miss never lowers it


# ------------------------------------------------------------------ heuristics
def test_local_flags():
    codes = lambda p: {f.code for f in heuristics.local_flags(p, now_year=2026)}
    assert "doi_placeholder" in codes(ParsedCitation(index=1, raw="", doi="10.1109/access.2023.1234567"))
    assert "doi_placeholder" not in codes(ParsedCitation(index=1, raw="", doi="10.1038/nature14539"))
    assert "year_future" in codes(ParsedCitation(index=1, raw="", year=2031))
    assert "prefix_journal_mismatch" in codes(ParsedCitation(index=1, raw="", doi="10.1016/j.x.2020.1", journal="IEEE Access"))
    assert "prefix_journal_mismatch" not in codes(ParsedCitation(index=1, raw="", doi="10.1109/access.2020.3000001", journal="IEEE Access"))
    assert codes(ParsedCitation(index=1, raw="", doi="10.1038/nature14539", year=2015, journal="Nature")) == set()


async def test_journal_range_rules_catch_impossible_volume_and_year(engine, web):
    from tests.fakes import crossref_item

    web.cr_journals = [{"title": "Tiny Journal of Tests", "ISSN": ["1111-2222"], "publisher": "P"}]
    web.crossref["10.7/a"] = crossref_item("10.7/a", "First paper", [("Ann", "A")], "Tiny Journal of Tests", 2018, volume="1", issn=["1111-2222"])
    web.crossref["10.7/z"] = crossref_item("10.7/z", "Latest paper", [("Zed", "Z")], "Tiny Journal of Tests", 2023, volume="6", issn=["1111-2222"])
    p = ParsedCitation(index=1, raw="Invented paper", title="A wholly invented study of imaginary things", authors=[Author(family="Fake", given="Fiona")], journal="Tiny Journal of Tests", year=2015, volume="45")
    r = await engine.verify_citation(p)
    codes = {f.code for f in r.flags}
    assert {"volume_exceeds_range", "year_before_journal_start"} <= codes
    assert r.verdict == "ghost"
    near = ParsedCitation(index=2, raw="x", title="Another invented study of imaginary things", authors=[Author(family="Fake", given="Fiona")], journal="Tiny Journal of Test", year=2020)
    r2 = await engine.verify_citation(near)
    assert "journal_near_miss" in {f.code for f in r2.flags}


# ------------------------------------------------------------------ DOCX conversion
@pytest.mark.skipif(libreoffice_binary() is None, reason="LibreOffice not installed")
async def test_docx_to_pdf_roundtrip(tmp_path):
    import asyncio
    import subprocess

    txt = tmp_path / "doc.txt"
    txt.write_text("References\n[1] Smith, J. (2020). A title. Journal, 1(2), 3-4.")
    subprocess.run([libreoffice_binary(), "--headless", "--convert-to", "docx", "--outdir", str(tmp_path), str(txt)], check=True, capture_output=True, timeout=120)
    pdf = await docx_to_pdf((tmp_path / "doc.docx").read_bytes(), "doc.docx")
    assert pdf.startswith(b"%PDF")


async def test_docx_conversion_error_is_clear(monkeypatch):
    monkeypatch.setattr("app.services.docx_convert.libreoffice_binary", lambda: None)
    with pytest.raises(ConversionError, match="LibreOffice"):
        await docx_to_pdf(b"x", "a.docx")


def test_same_journal_name_ignores_filler_words_and_punctuation():
    assert heuristics.same_journal_name("The Journal of Tests", "Journal Tests")
    assert heuristics.same_journal_name("Science & Technology", "Science and Technology")
    assert not heuristics.same_journal_name("Tiny Journal of Test", "Tiny Journal of Tests")
