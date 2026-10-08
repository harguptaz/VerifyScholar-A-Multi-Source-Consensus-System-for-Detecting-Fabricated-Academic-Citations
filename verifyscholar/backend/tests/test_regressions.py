"""Regression tests for problems found by running the app on real citations."""
from app.schemas import Author, PaperRecord, ParsedCitation
from app.services.grobid import parse_citation_list_tei
from app.services.matching import accept_candidate, compare, journal_similarity
from app.services.parsing import finalize, heuristic_parse
from tests.fakes import crossref_item, openalex_work

LAYER_NORM_RAW = "Jimmy Lei Ba, Jamie Ryan Kiros, and Geoffrey E Hinton. Layer normalization. arXiv preprint arXiv:1607.06450, 2016."
AUTHORS = ["Jimmy Lei Ba", "Jamie Ryan Kiros", "Geoffrey E. Hinton"]
ARXIV_DOI = "10.48550/arxiv.1607.06450"


def layer_norm_work(year=2016, wid="W2530000001", authors=AUTHORS, title="Layer Normalization", doi=ARXIV_DOI):
    return openalex_work(doi, title, authors, "arXiv (Cornell University)", year, wid, abstract="Training deep neural networks is computationally expensive.")


# ----------------------------------------------------------------------------- real paper cited with an arXiv id
async def test_arxiv_cited_paper_is_verified_through_its_arxiv_doi(engine, web):
    web.openalex[ARXIV_DOI] = layer_norm_work()
    p = heuristic_parse(LAYER_NORM_RAW, 1)
    assert p.arxiv_id == "1607.06450" and p.doi == ARXIV_DOI and p.kind == "preprint"
    r = await engine.verify_citation(p)
    assert r.verdict == "verified" and r.doi_status == "found"
    assert r.record.title == "Layer Normalization"
    oa = next(s for s in r.sources if s.source == "openalex")
    assert oa.fields["journal"].status == "missing"  # "arXiv preprint arXiv" names a server, not a journal to compare
    assert not any(f.code == "doi_placeholder" for f in r.flags)


async def test_year_difference_for_an_arxiv_paper_is_not_a_hard_mismatch(engine, web):
    web.openalex[ARXIV_DOI] = layer_norm_work(year=2019)  # a source that lists a later version/venue year
    r = await engine.verify_citation(heuristic_parse(LAYER_NORM_RAW, 1))
    oa = next(s for s in r.sources if s.source == "openalex")
    assert oa.fields["year"].status == "partial" and r.verdict == "verified"


async def test_year_difference_is_still_a_mismatch_without_an_arxiv_id(engine, web):
    web.crossref["10.1/x"] = crossref_item("10.1/x", "A distinctive study of imaginary glaciers", [("Ba", "J")], "Ice Letters", 2019, volume="3")
    p = ParsedCitation(index=1, raw="r", doi="10.1/x", title="A distinctive study of imaginary glaciers", authors=[Author(family="Ba")], journal="Ice Letters", year=2016, volume="3")
    assert (await engine.verify_citation(p)).verdict == "suspicious"


# ----------------------------------------------------------------------------- generic title, no arXiv id: second-chance search
async def test_generic_title_found_by_the_year_window_search_and_decoys_rejected(engine, web):
    decoy = layer_norm_work(year=2019, wid="W111", authors=["Xu Jingjing", "Sun Xu"], title="Layer Normalization", doi="10.1/decoy")
    real = layer_norm_work()
    web.oa_search = [decoy]            # unrestricted search: the real paper is not in the pool
    web.oa_search_windowed = [real]    # restricted to 2015-2017: it is
    p = heuristic_parse("Jimmy Lei Ba, Jamie Ryan Kiros, and Geoffrey E Hinton. Layer normalization. 2016.", 1)
    assert p.arxiv_id is None and p.kind in ("article", "unknown")
    r = await engine.verify_citation(p)
    oa = next(s for s in r.sources if s.source == "openalex")
    assert r.verdict == "verified" and oa.record.year == 2016
    assert any("second search" in c["outcome"] for c in r.calls)
    decoy_info = next(c for c in oa.candidates if c.doi == "10.1/decoy")
    assert not decoy_info.accepted and "generic title" in decoy_info.note
    assert any(c.accepted for c in oa.candidates)


# ----------------------------------------------------------------------------- template placeholder from a style guide
async def test_template_citation_is_not_matched_to_an_unrelated_paper(engine, web):
    unrelated = crossref_item("10.1/micro", "Article title", [("Rahman", "S"), ("Lim", "K")], "Malaysian Journal of Microbiology", 2019, volume="15")
    web.cr_search = [unrelated]
    web.oa_search = [openalex_work("10.1/micro", "Article title", ["S Rahman", "K Lim"], "Malaysian Journal of Microbiology", 2019, "W9")]
    raw = "Author, A. A., & Author, B. B. (2024). Title of article. Title of Periodical, 12(3), 45–55. doi.org"
    r = await engine.verify_citation(heuristic_parse(raw, 1))
    assert r.verdict == "ghost" and r.record is None
    assert "template_placeholder" in {f.code for f in r.flags}
    assert r.reason.startswith("This does not look like a real reference")
    cr = next(s for s in r.sources if s.source == "crossref")
    assert cr.candidates and not cr.candidates[0].accepted and cr.candidates[0].note


def test_accept_candidate_rules():
    def cmp_for(cited_title, cited_authors, journal, year, record):
        c = ParsedCitation(index=1, raw="r", title=cited_title, authors=[Author(family=f) for f in cited_authors], journal=journal, year=year)
        return c, compare(c, record)

    rec = PaperRecord(title="Article title", authors=[Author(family="Rahman")], journal="Malaysian Journal of Microbiology", year=2019)
    c, cmp = cmp_for("Title of article", ["Author"], "Title of Periodical", 2024, rec)
    ok, why = accept_candidate(c, cmp)
    assert not ok and why
    # a long, specific title with partial agreement is still a candidate
    rec2 = PaperRecord(title="Hindi sentiment analysis using BERT based models", authors=[Author(family="Kumar")], journal="J Lang Models", year=2022)
    c2, cmp2 = cmp_for("Sentiment analysis of Hindi text using BERT models", ["Kumar"], "J Lang Models", 2023, rec2)
    assert accept_candidate(c2, cmp2)[0]
    # ... unless authors, journal and year ALL differ
    c3, cmp3 = cmp_for("Sentiment analysis of Hindi text using BERT models", ["Nguyen"], "Other Journal", 2010, rec2)
    ok3, why3 = accept_candidate(c3, cmp3)
    assert not ok3 and "different paper" in why3


# ----------------------------------------------------------------------------- books
async def test_book_that_is_not_found_is_inconclusive_not_ghost(engine, web):
    p = heuristic_parse("Smith, T. (2020). The citation manual for students: A quick guide (2nd ed.). Wiley", 1)
    assert p.kind == "book" and p.publisher == "Wiley" and p.journal is None and "(2nd ed.)" not in p.title
    r = await engine.verify_citation(p)
    assert r.verdict == "inconclusive" and r.confidence is None
    assert "looks like a book" in r.reason and "Wiley" in r.reason
    assert not any(f.code.startswith("journal_") for f in r.flags)  # "Wiley" must not be treated as a journal
    doaj = next(s for s in r.sources if s.source == "doaj")
    assert doaj.status == "skipped"


async def test_book_that_is_found_is_verified(engine, web):
    book = crossref_item("10.1002/book", "The citation manual for students: A quick guide", [("Smith", "Tom")], "", 2020, publisher="Wiley")
    book["container-title"], book["type"] = [], "book"
    web.crossref["10.1002/book"] = book
    web.cr_search = [book]
    r = await engine.verify_citation(heuristic_parse("Smith, T. (2020). The citation manual for students: A quick guide (2nd ed.). Wiley", 1))
    assert r.verdict == "verified"


def test_finalize_classification():
    art = finalize(ParsedCitation(index=1, raw="x", title="T", journal="Nature", volume="1", pages="1-2"))
    assert art.kind == "article"
    pub_as_journal = finalize(ParsedCitation(index=1, raw="x", title="T", journal="Oxford University Press"))
    assert pub_as_journal.kind == "book" and pub_as_journal.publisher == "Oxford University Press"
    # a journal literally named "... Press" with volume/pages is an article
    assert finalize(ParsedCitation(index=1, raw="x", title="T", journal="Cambridge Law Press", volume="3", pages="4-9")).kind == "article"


# ----------------------------------------------------------------------------- conference venue names
def test_conference_names_match_across_writing_styles():
    rec = PaperRecord(journal="2016 IEEE Conference on Computer Vision and Pattern Recognition (CVPR)", journal_aliases=[])
    s, _ = journal_similarity("In Proceedings of the IEEE Conference on Computer Vision and Pattern Recognition", rec)
    assert s >= 0.9
    s2, _ = journal_similarity("In Proc. IEEE Conf. Comput. Vis. Pattern Recognit.", rec)
    assert s2 >= 0.85
    # the journal "Machine Learning" is not the ICML conference
    icml = PaperRecord(journal="International Conference on Machine Learning")
    assert journal_similarity("Machine Learning", icml)[0] < 0.85


# ----------------------------------------------------------------------------- GROBID output
def test_grobid_arxiv_idno_and_publisher_are_used():
    xml = b"""<TEI xmlns="http://www.tei-c.org/ns/1.0"><text><body><listBibl>
      <biblStruct><analytic><title level="a" type="main">Layer normalization</title><idno type="arXiv">arXiv:1607.06450</idno></analytic>
        <monogr><title level="j">arXiv preprint</title><imprint><date when="2016"/></imprint></monogr></biblStruct>
      <biblStruct><monogr><title level="m">The citation manual for students: A quick guide</title><imprint><publisher>Wiley</publisher><date when="2020"/></imprint></monogr></biblStruct>
    </listBibl></body></text></TEI>"""
    out = parse_citation_list_tei(xml, ["Ba J. Layer normalization. arXiv preprint 2016", "Smith, T. (2020). The citation manual for students: A quick guide (2nd ed.). Wiley"])
    assert out[0].arxiv_id == "1607.06450" and out[0].doi == ARXIV_DOI and out[0].kind == "preprint"
    assert out[1].kind == "book" and out[1].publisher == "Wiley" and out[1].journal is None


def test_year_is_not_taken_from_arxiv_ids_or_page_ranges():
    assert heuristic_parse(LAYER_NORM_RAW, 1).year == 2016  # "1607" in arXiv:1607.06450 is not a year
    p = heuristic_parse('A. Writer, B. Maker, "Some study of things," J. Stuff, vol. 3, pp. 1600-1650, 2020.', 1)
    assert p.year == 2020 and p.pages == "1600-1650"
    assert heuristic_parse("Doe J, Roe R. Some study of things. J Stuff. 2019;3(2):1850-1860.", 1).year == 2019
    assert heuristic_parse("Kumar, R. (2022) 'A title', J Stuff, 4(1), pp. 1990-2005.", 1).year == 2022


# =============================================================================================
# Second round: results from running the app on real documents
# =============================================================================================
import asyncio

import httpx

from app.services.http_client import HttpService
from app.services.parsing import parse_authors

BOOK_RAW = "Smith, T. (2020). The citation manual for students: A quick guide (2nd ed.). Wiley"
HO_RAW = "Ho, Karen Zouwen. Liquidated: An Ethnography of Wall Street. Duke University Press, 2009"
PARIKH_RAW = "Ankur Parikh, Oscar Täckström, Dipanjan Das, and Jakob Uszkoreit. A decomposable attention model. In Empirical Methods in Natural Language Processing, 2016."
BENGIO_RAW = "Samy Bengio Łukasz Kaiser. Can active memory replace attention? In Advances in Neural Information Processing Systems, (NIPS), 2016."


async def test_book_is_not_matched_to_an_unrelated_paper_that_shares_a_phrase(engine, web):
    covid = crossref_item("10.1/covid", "COVID-19 Clinical Quick Guide for the Neonatologist", [("Lee", "Ann"), ("Kim", "Bo")], "Neonatology Today", 2020, volume="15")
    web.cr_search = [covid]
    web.oa_search = [openalex_work("10.1/covid", "COVID-19 Clinical Quick Guide for the Neonatologist", ["Ann Lee", "Bo Kim"], "Neonatology Today", 2020, "W5")]
    r = await engine.verify_citation(heuristic_parse(BOOK_RAW, 1))
    assert r.record is None and r.verdict == "inconclusive" and "looks like a book" in r.reason
    cands = [c for s in r.sources for c in s.candidates]
    assert cands and not any(c.accepted for c in cands)  # the neonatology paper was seen, and rejected


async def test_book_matched_only_to_a_review_is_inconclusive_and_says_why(engine, web):
    review = crossref_item("10.5860/choice.47-3041", "Liquidated: an ethnography of Wall Street", [], "Choice Reviews Online", 2010, volume="47", issue="6")
    web.cr_search = [review]
    web.oa_search = [openalex_work("10.5860/choice.47-3041", "Liquidated: an ethnography of Wall Street", [], "Choice Reviews Online", 2010, "W7")]
    r = await engine.verify_citation(heuristic_parse(HO_RAW, 1))
    assert r.verdict == "inconclusive" and r.confidence is None
    assert "Choice Reviews Online" in r.reason and "review of the book" in r.reason and "Matched to" not in r.reason


async def test_conference_paper_with_a_shortened_title_is_verified_without_journal_noise(engine, web):
    p = heuristic_parse(PARIKH_RAW, 1)
    assert p.kind == "conference" and [a.family for a in p.authors] == ["Parikh", "Täckström", "Das", "Uszkoreit"]
    work = openalex_work("10.18653/v1/d16-1244", "A Decomposable Attention Model for Natural Language Inference", ["Ankur Parikh", "Oscar Täckström", "Dipanjan Das", "Jakob Uszkoreit"],
                         "Proceedings of the 2016 Conference on Empirical Methods in Natural Language Processing", 2016, "W8")
    web.oa_search = [work]
    r = await engine.verify_citation(p)
    assert r.verdict == "verified", r.reason
    assert not any(f.code.startswith("journal_") for f in r.flags)
    assert next(s for s in r.sources if s.source == "doaj").status == "skipped"  # a conference is not a journal


async def test_unverified_conference_paper_is_not_blamed_for_its_venue_not_being_a_journal(engine, web):
    p = heuristic_parse(BENGIO_RAW, 1)
    assert p.kind == "conference" and p.title == "Can active memory replace attention?" and p.year == 2016
    r = await engine.verify_citation(p)
    assert not any(f.code.startswith("journal_") for f in r.flags)
    assert "DOAJ" not in r.reason and "name similar to" not in r.reason


def test_given_family_author_lists_are_parsed_by_the_fallback_parser():
    assert [a.family for a in parse_authors("Jimmy Lei Ba, Jamie Ryan Kiros, and Geoffrey E Hinton")] == ["Ba", "Kiros", "Hinton"]
    assert [a.family for a in parse_authors("Kumar, Rahul, and Priya Singh")] == ["Kumar", "Singh"]  # MLA is unchanged
    assert heuristic_parse("Smith J. Effects of things. Proc Natl Acad Sci U S A. 2019;116(3):1-9.", 1).kind == "article"  # PNAS is a journal


# ----------------------------------------------------------------------------- a source that throttles or times out
async def test_a_citation_whose_source_failed_is_retried_automatically_and_the_log_keeps_both_attempts(engine, web):
    web.openalex[ARXIV_DOI] = layer_norm_work()
    web.fail_first = {"api.openalex.org": 2}  # HTTP_MAX_ATTEMPTS is 2 in the tests, so the first pass ends "unavailable"
    seen = []
    results = await engine.verify_many([heuristic_parse(LAYER_NORM_RAW, 1)], on_retry=seen.append)
    r = results[0]
    assert seen == [1]
    assert r.verdict == "verified"
    outcomes = [c["outcome"] for c in r.calls]
    assert any("Source unavailable" in o for o in outcomes) and any(o == "Match found" for o in outcomes)


async def test_a_source_that_stays_down_is_inconclusive_after_the_retry(engine, web):
    web.down = {"api.openalex.org", "api.crossref.org"}
    results = await engine.verify_many([heuristic_parse(LAYER_NORM_RAW, 1)])
    assert results[0].verdict == "inconclusive"


async def test_requests_to_openalex_are_capped_per_service(settings):
    active = {"now": 0, "max": 0}

    async def handler(request: httpx.Request) -> httpx.Response:
        active["now"] += 1
        active["max"] = max(active["max"], active["now"])
        await asyncio.sleep(0.02)
        active["now"] -= 1
        return httpx.Response(200, json={"ok": True})

    svc = HttpService(settings)
    svc.use_client(httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    await asyncio.gather(*(svc.get_json(f"https://api.openalex.org/works/W{i}", use_cache=False) for i in range(12)))
    assert 1 <= active["max"] <= settings.openalex_max_concurrent_requests
    active["max"] = 0
    await asyncio.gather(*(svc.get_json(f"https://example.org/x{i}", use_cache=False) for i in range(12)))
    assert active["max"] > settings.openalex_max_concurrent_requests  # other hosts are not throttled by that cap
