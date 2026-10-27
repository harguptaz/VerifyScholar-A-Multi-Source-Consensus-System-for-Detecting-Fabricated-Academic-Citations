"""Tests for the secondary sources: DBLP, DataCite and Open Library (all served by the fake web)."""
import pytest

from app.schemas import Author, PaperRecord, ParsedCitation
from app.services.adapters.base import SECONDARY_SOURCES
from app.services.adapters.crossref import CrossRefAdapter
from app.services.adapters.datacite import DataCiteAdapter, item_to_record as datacite_record
from app.services.adapters.dblp import DblpAdapter, hit_to_record
from app.services.adapters.doaj import DoajAdapter
from app.services.adapters.openalex import OpenAlexAdapter
from app.services.adapters.openlibrary import OpenLibraryAdapter, doc_to_record
from app.services.matching import compare
from app.services.merge import merge_all, year_compatible
from app.services.verifier import VerificationEngine
from tests.fakes import DEEP_LEARNING_DOI, crossref_item, datacite_item, dblp_hit, openlibrary_doc, seed_deep_learning

AUTH = [("Vaswani", "Ashish"), ("Shazeer", "Noam"), ("Parmar", "Niki")]
TITLE = "Attention Is All You Need"


@pytest.fixture
def full_engine(http, settings):
    """The engine as the application builds it: CrossRef and OpenAlex first, then DBLP, DataCite and Open Library."""
    return VerificationEngine(
        CrossRefAdapter(http, settings), OpenAlexAdapter(http, settings), DoajAdapter(http, settings), settings,
        secondary=[DblpAdapter(http, settings), DataCiteAdapter(http, settings), OpenLibraryAdapter(http, settings)],
    )


def conference_paper(year=2017):
    return ParsedCitation(
        index=1, raw=f"Vaswani, A., Shazeer, N., Parmar, N. ({year}). Attention is all you need. Advances in Neural Information Processing Systems, 30.",
        title="Attention is all you need", authors=[Author(family=f, given=g) for f, g in AUTH],
        journal="Advances in Neural Information Processing Systems", year=year, volume="30", kind="conference",
    )


def book(title="Thinking, Fast and Slow", family="Kahneman", given="Daniel", year=2011):
    return ParsedCitation(
        index=1, raw=f"{family}, {given[0]}. ({year}). {title}. Farrar, Straus and Giroux.", title=title, authors=[Author(family=family, given=given)],
        year=year, kind="book", publisher="Farrar, Straus and Giroux",
    )


def called(web, host):
    return [c for c in web.calls if c.startswith(host)]


# ----------------------------------------------------------------------------- adapters read the real response shapes
def test_dblp_hit_with_a_single_author_a_trailing_period_and_a_doi_link():
    rec = hit_to_record(dblp_hit("A tiny method", ["Wei Wang 0001"], "ICML", 2020, doi="10.1000/abc", pages="1-10"))
    assert rec.title == "A tiny method" and rec.year == 2020 and rec.pages == "1-10"
    assert [a.family for a in rec.authors] == ["Wang"]  # the "0001" that tells two people apart is dropped
    assert rec.doi == "10.1000/abc" and rec.sources == ["dblp"] and rec.type == "proceedings-article"


def test_dblp_corr_entries_are_preprints_without_a_volume():
    rec = hit_to_record(dblp_hit(TITLE, ["Ashish Vaswani", "Noam Shazeer"], "CoRR", 2017, type_="Informal and Other Publications", volume="abs/1706.03762"))
    assert rec.type == "posted-content" and rec.volume is None and len(rec.authors) == 2


def test_dblp_hit_without_a_title_is_ignored():
    assert hit_to_record({"info": {"year": "2020"}}) is None


def test_datacite_record_mapping():
    item = datacite_item("10.5281/zenodo.1", "A dataset", [("Rao", "Mina")], 2020, publisher={"name": "Zenodo"}, type_general="Dataset",
                         container={"title": "Some Series", "volume": "4", "firstPage": "10", "lastPage": "12"})
    rec = datacite_record(item)
    assert rec.doi == "10.5281/zenodo.1" and rec.publisher == "Zenodo" and rec.type == "dataset"
    assert rec.authors[0].family == "Rao" and rec.year == 2020 and rec.journal == "Some Series" and rec.volume == "4" and rec.pages == "10-12"
    assert datacite_record(datacite_item("10.1/x", "Prep", [("Ba", "Jimmy")], 2016, type_general="Preprint")).type == "posted-content"


def test_datacite_organisational_creator_and_missing_title():
    item = datacite_item("10.1/y", "Report", [], 2021)
    item["attributes"]["creators"] = [{"name": "World Health Organization", "nameType": "Organizational"}]
    assert datacite_record(item).authors == [Author(name="World Health Organization")]
    item["attributes"]["titles"] = []
    assert datacite_record(item) is None


def test_openlibrary_doc_mapping_keeps_every_edition_year():
    rec = doc_to_record(openlibrary_doc("Thinking, Fast and Slow", ["Daniel Kahneman"], 2011, editions=[2011, 2013, 2015]))
    assert rec.type == "book" and rec.year == 2011 and rec.alt_years == [2013, 2015]
    assert rec.authors[0].family == "Kahneman" and rec.url == "https://openlibrary.org/works/OL1W"


async def test_adapters_search_through_the_fake_web(http, settings, web):
    web.dblp_hits = [dblp_hit(TITLE, ["Ashish Vaswani"], "NIPS", 2017)]
    web.datacite_search = [datacite_item("10.5281/zenodo.9", TITLE, AUTH[:1], 2017)]
    web.openlibrary_docs = [openlibrary_doc("Thinking, Fast and Slow", ["Daniel Kahneman"], 2011)]
    cit = conference_paper()
    assert (await DblpAdapter(http, settings).search(cit)).records[0].title == TITLE
    assert (await DataCiteAdapter(http, settings).search(cit)).records[0].doi == "10.5281/zenodo.9"
    assert (await OpenLibraryAdapter(http, settings).search(book())).records[0].title == "Thinking, Fast and Slow"


async def test_a_search_without_results_is_not_found_not_an_error(http, settings, web):
    assert (await DblpAdapter(http, settings).search(conference_paper())).status == "not_found"  # the real API omits the "hit" key
    assert (await OpenLibraryAdapter(http, settings).search(book())).status == "not_found"
    assert (await DataCiteAdapter(http, settings).lookup_doi("10.5281/zenodo.404")).status == "not_found"


async def test_a_down_source_is_unavailable_never_not_found(http, settings, web):
    web.down = {"dblp.org", "api.datacite.org", "openlibrary.org"}
    assert (await DblpAdapter(http, settings).search(conference_paper())).status == "unavailable"
    assert (await DataCiteAdapter(http, settings).lookup_doi("10.5281/zenodo.1")).status == "unavailable"
    assert (await OpenLibraryAdapter(http, settings).search(book())).status == "unavailable"


async def test_each_new_host_gets_its_own_concurrency_cap(http):
    for url in ("https://dblp.org/search/publ/api", "https://api.datacite.org/dois", "https://openlibrary.org/search.json"):
        assert http._host_limit(url) is not None
    assert http._host_limit("https://example.org/x") is None


def test_secondary_source_names_are_registered():
    assert SECONDARY_SOURCES == {"dblp", "datacite", "openlibrary"}
    assert {DblpAdapter.name, DataCiteAdapter.name, OpenLibraryAdapter.name} == SECONDARY_SOURCES


# ----------------------------------------------------------------------------- when the secondary sources are asked
async def test_secondary_sources_are_not_asked_when_crossref_settles_the_citation(full_engine, web):
    seed_deep_learning(web)
    cit = ParsedCitation(index=1, raw="LeCun Y, Bengio Y, Hinton G. Deep learning. Nature 521 (2015) 436-444.", title="Deep learning", year=2015, journal="Nature",
                         authors=[Author(family="LeCun", given="Yann"), Author(family="Bengio", given="Yoshua"), Author(family="Hinton", given="Geoffrey")],
                         volume="521", pages="436-444", doi=DEEP_LEARNING_DOI, kind="article")
    r = await full_engine.verify_citation(cit)
    assert r.verdict == "verified"
    assert {s.source for s in r.sources} == {"crossref", "openalex", "doaj"}
    assert not (called(web, "dblp.org") or called(web, "api.datacite.org") or called(web, "openlibrary.org"))


async def test_dblp_finds_a_conference_paper_that_crossref_and_openalex_lack(full_engine, web):
    web.dblp_hits = [dblp_hit(TITLE, ["Ashish Vaswani", "Noam Shazeer", "Niki Parmar"], "NIPS", 2017, key="conf/nips/VaswaniSPUJGKP17", pages="5998-6008")]
    r = await full_engine.verify_citation(conference_paper())
    dblp = next(s for s in r.sources if s.source == "dblp")
    assert r.verdict == "verified" and dblp.status == "match" and dblp.method == "search"
    assert dblp.fields["journal"].status == "missing"  # DBLP says "NIPS": a different spelling of the venue is not a mismatch
    assert any(c["source"] == "DBLP API" for c in r.calls)
    assert next(s for s in r.sources if s.source == "doaj").status == "skipped"


async def test_a_late_crossref_copy_is_replaced_by_the_original_dblp_record(full_engine, web):
    late = crossref_item("10.65215/late", TITLE, AUTH, "", 2025)
    late["container-title"] = []
    web.cr_search = [late]
    web.dblp_hits = [dblp_hit(TITLE, ["Ashish Vaswani", "Noam Shazeer", "Niki Parmar"], "NIPS", 2017, pages="5998-6008")]
    r = await full_engine.verify_citation(conference_paper())
    assert r.verdict == "verified"
    assert r.record.year == 2017 and r.record.doi is None  # the 2025 re-registration's DOI and year are not mixed into the 2017 record
    assert "dblp" in r.record.sources and "crossref" not in r.record.sources


async def test_a_fabricated_conference_paper_is_still_a_ghost(full_engine, web):
    web.dblp_hits = [dblp_hit("Graph methods for protein folding", ["Someone Else"], "ICML", 2019)]
    r = await full_engine.verify_citation(conference_paper())
    assert r.verdict == "ghost"
    assert {s.source for s in r.sources if s.kind == "record"} == {"crossref", "openalex", "dblp", "datacite"}
    assert all(s.status == "no_record" for s in r.sources if s.kind == "record")


async def test_a_secondary_source_that_is_down_does_not_make_a_ghost_inconclusive(full_engine, web):
    web.down = {"dblp.org"}
    r = await full_engine.verify_citation(conference_paper())
    assert r.verdict == "ghost"
    assert next(s for s in r.sources if s.source == "dblp").status == "unavailable"


async def test_a_primary_source_that_is_down_still_makes_it_inconclusive(full_engine, web):
    web.down = {"api.openalex.org"}
    r = await full_engine.verify_citation(conference_paper())
    assert r.verdict == "inconclusive" and r.doi_status == "no_doi"


async def test_secondary_sources_can_confirm_a_paper_while_openalex_is_down(full_engine, web):
    web.down = {"api.openalex.org"}
    web.dblp_hits = [dblp_hit(TITLE, ["Ashish Vaswani", "Noam Shazeer", "Niki Parmar"], "NIPS", 2017)]
    assert (await full_engine.verify_citation(conference_paper())).verdict == "verified"


# ----------------------------------------------------------------------------- DataCite
def zenodo_citation(doi="10.5281/zenodo.1234567"):
    return ParsedCitation(
        index=1, raw=f"Rao, M. (2020). A benchmark of things. Zenodo. https://doi.org/{doi}", title="A benchmark of things",
        authors=[Author(family="Rao", given="Mina")], year=2020, doi=doi, kind="unknown",
    )


async def test_a_datacite_doi_is_verified_when_crossref_has_never_heard_of_it(full_engine, web):
    web.datacite["10.5281/zenodo.1234567"] = datacite_item("10.5281/zenodo.1234567", "A benchmark of things", [("Rao", "Mina")], 2020, type_general="Dataset")
    r = await full_engine.verify_citation(zenodo_citation())
    dc = next(s for s in r.sources if s.source == "datacite")
    assert r.verdict == "verified" and dc.method == "doi" and r.doi_status == "found"
    assert r.record.doi == "10.5281/zenodo.1234567" and "datacite" in r.record.sources


async def test_a_datacite_doi_that_points_to_another_paper_is_reported(full_engine, web):
    web.datacite["10.5281/zenodo.1234567"] = datacite_item("10.5281/zenodo.1234567", "Protein folding with graph networks", [("Chen", "Lu")], 2019)
    r = await full_engine.verify_citation(zenodo_citation())
    assert r.doi_status == "mismatch" and r.verdict in ("suspicious", "ghost")


async def test_a_datacite_prefix_is_not_called_unregistered(full_engine, web):
    web.datacite_prefixes = {"10.5281"}
    ghost = await full_engine.verify_citation(zenodo_citation("10.5281/zenodo.99999999"))
    assert ghost.verdict == "ghost" and not any(f.code == "prefix_not_registered" for f in ghost.flags)
    assert any(c["source"] == "DataCite API" and c["query"] == "GET /prefixes/10.5281" for c in ghost.calls)


async def test_a_prefix_unknown_to_both_registration_agencies_is_flagged(full_engine, web):
    r = await full_engine.verify_citation(zenodo_citation("10.9999/zenodo.99999999"))
    flag = next(f for f in r.flags if f.code == "prefix_not_registered")
    assert "CrossRef or DataCite" in flag.message


async def test_an_unreachable_datacite_does_not_flag_the_prefix(full_engine, web):
    web.down = {"api.datacite.org"}
    r = await full_engine.verify_citation(zenodo_citation("10.9999/zenodo.99999999"))
    assert not any(f.code == "prefix_not_registered" for f in r.flags)


# ----------------------------------------------------------------------------- Open Library and books
async def test_a_book_is_verified_by_open_library(full_engine, web):
    web.openlibrary_docs = [openlibrary_doc("Thinking, Fast and Slow", ["Daniel Kahneman"], 2011, editions=[2011, 2013])]
    r = await full_engine.verify_citation(book())
    assert r.verdict == "verified" and r.record.type == "book" and "openlibrary" in r.record.sources
    assert "Open Library" in r.reason


async def test_a_later_edition_year_still_matches(full_engine, web):
    web.openlibrary_docs = [openlibrary_doc("Thinking, Fast and Slow", ["Daniel Kahneman"], 2011, editions=[2011, 2013])]
    r = await full_engine.verify_citation(book(year=2013))
    ol = next(s for s in r.sources if s.source == "openlibrary")
    assert r.verdict == "verified" and ol.fields["year"].status == "match"


async def test_a_book_cited_with_the_wrong_author_is_suspicious(full_engine, web):
    web.openlibrary_docs = [openlibrary_doc("Thinking, Fast and Slow", ["Daniel Kahneman"], 2011)]
    r = await full_engine.verify_citation(book(family="Smith", given="John"))
    assert r.verdict == "suspicious" and "Kahneman" in r.reason


async def test_a_book_that_no_catalogue_lists_stays_inconclusive(full_engine, web):
    r = await full_engine.verify_citation(book("The Quiet Atlas of Lost Rivers", "Rao", "Mina", 2019))
    assert r.verdict == "inconclusive" and "Open Library" in r.reason and "not mean the book is fake" in r.reason


async def test_a_review_article_does_not_outrank_the_book_record(full_engine, web):
    web.cr_search = [crossref_item("10.5860/choice.50-5070", "Thinking, Fast and Slow", [("Kahneman", "Daniel")], "Choice Reviews Online", 2012)]
    web.openlibrary_docs = [openlibrary_doc("Thinking, Fast and Slow", ["Daniel Kahneman"], 2011)]
    r = await full_engine.verify_citation(book())
    assert r.verdict == "verified" and r.record.type == "book"
    assert r.record.doi is None and r.record.journal is None  # nothing from the review is copied into the book


async def test_open_library_is_not_asked_about_a_journal_article(full_engine, web):
    r = await full_engine.verify_citation(conference_paper())
    assert not called(web, "openlibrary.org") and "openlibrary" not in {s.source for s in r.sources}


# ----------------------------------------------------------------------------- matching and merging details
def test_a_book_title_gets_no_prefix_shortcut_but_may_drop_its_subtitle():
    def rec(title):
        return PaperRecord(title=title, authors=[Author(family="Cormen")], year=2009, type="book", sources=["openlibrary"])

    def cit(title, kind):
        return ParsedCitation(index=1, raw=title, title=title, authors=[Author(family="Cormen")], year=2009, kind=kind)

    longer = rec("Introduction to Algorithms for Beginners")
    assert compare(cit("Introduction to Algorithms", "article"), longer).title_sim == 0.9
    assert compare(cit("Introduction to Algorithms", "book"), longer).title_sim < 0.9
    assert compare(cit("Thinking, Fast and Slow", "book"), rec("Thinking, Fast and Slow: the groundbreaking bestseller")).title_sim >= 0.9
    assert compare(cit("Deep Learning: A practical approach", "book"), rec("Deep Learning")).title_sim < 0.7  # two-word main titles are not shortened


def test_a_dblp_venue_that_differs_is_missing_but_other_sources_still_mismatch():
    cit = ParsedCitation(index=1, raw="x", title=TITLE, journal="Advances in Neural Information Processing Systems", year=2017, kind="article")
    dblp = PaperRecord(title=TITLE, journal="CVPR", year=2017, sources=["dblp"])
    other = PaperRecord(title=TITLE, journal="CVPR", year=2017, sources=["crossref"])
    assert compare(cit, dblp).fields["journal"].status == "missing"
    assert compare(cit, other).fields["journal"].status == "mismatch"


def test_year_compatibility_and_a_secondary_lead_in_merge_all():
    dblp = PaperRecord(title=TITLE, year=2017, sources=["dblp"])
    late = PaperRecord(title=TITLE, doi="10.65215/late", year=2025, sources=["crossref"])
    same = PaperRecord(title=TITLE, doi="10.5555/orig", year=2017, sources=["crossref"])
    assert not year_compatible(dblp, late) and year_compatible(dblp, same) and year_compatible(dblp, PaperRecord(title=TITLE))
    merged = merge_all([dblp, late, same], lead=dblp)
    assert merged.doi == "10.5555/orig" and merged.year == 2017 and merged.sources[0] == "dblp"
    # without a secondary lead, CrossRef stays the base and an incompatible secondary record is left out
    merged = merge_all([same, PaperRecord(title=TITLE, year=2030, journal="Elsewhere", sources=["dblp"])])
    assert merged.journal is None and merged.year == 2017


def test_merge_all_of_nothing_or_of_one_record():
    only = PaperRecord(title=TITLE, year=2017, sources=["openalex"])
    assert merge_all([only]).title == TITLE
    assert merge_all([]) is None