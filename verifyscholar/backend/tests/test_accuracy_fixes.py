"""Tests for the accuracy and speed fixes: caps after bonuses, duplicate records, 'not indexed', time budgets."""
import asyncio

from app.config import get_settings
from app.schemas import Author, FieldMatch, PaperRecord, ParsedCitation, SourceResult
from app.services import scoring
from app.services.adapters.base import AdapterResponse, SourceAdapter
from app.services.matching import compare
from app.services.merge import collapse_duplicates
from app.services.verifier import VerificationEngine
from tests.fakes import crossref_item, openalex_work

AUTH = [("Vaswani", "Ashish"), ("Shazeer", "Noam"), ("Parmar", "Niki"), ("Uszkoreit", "Jakob"), ("Jones", "Llion"), ("Gomez", "Aidan")]
TITLE = "Attention Is All You Need"


def attention(year=2017):
    return ParsedCitation(
        index=1, raw=f"Vaswani, A., et al. ({year}). Attention is all you need. Advances in Neural Information Processing Systems, 30.",
        title="Attention is all you need", authors=[Author(family=f, given=g) for f, g in AUTH[:3]],
        journal="Advances in Neural Information Processing Systems", year=year, volume="30", kind="conference",
    )


def bare_record(year=2025, doi="10.65215/r5bs2d54", title=TITLE, journal=None, type_="journal-article"):
    return PaperRecord(doi=doi, title=title, authors=[Author(family=f, given=g) for f, g in AUTH], journal=journal, year=year, type=type_, sources=["crossref"])


# ----------------------------------------------------------------------------- 1. caps are applied AFTER the bonuses
def _src(name, score, year_status, method="search"):
    fields = {"title": FieldMatch(status="match"), "authors": FieldMatch(status="match"), "year": FieldMatch(status=year_status)}
    return SourceResult(source=name, label=name, status="partial", method=method, score=score, fields=fields)


def test_bonuses_cannot_lift_a_hard_mismatch_to_verified():
    s = get_settings()
    sources = [_src("crossref", 79, "mismatch"), _src("openalex", 78, "mismatch"), SourceResult(source="doaj", label="DOAJ", kind="journal", status="match", score=100)]
    out = scoring.score_result(sources, [], s)
    assert out.verdict == "suspicious" and out.confidence == 79  # 79 + 5 (agreement) + 5 (DOAJ) would have been 89


def test_bonuses_still_apply_when_there_is_no_hard_mismatch():
    s = get_settings()
    out = scoring.score_result([_src("crossref", 77, "partial"), _src("openalex", 76, "partial")], [], s)
    assert out.verdict == "verified" and out.confidence == 82


def test_a_doi_that_points_elsewhere_stays_capped_at_35():
    s = get_settings()
    src = _src("crossref", 35, "mismatch", method="doi")
    src.doi_resolves_to = bare_record()
    assert scoring.source_cap(src) == 35


# ----------------------------------------------------------------------------- reason text never prints "None"
async def test_missing_journal_is_not_printed_in_the_reason(engine, web):
    web.cr_search = [crossref_item("10.65215/x", TITLE, AUTH, "", 2035)]
    web.cr_search[0]["container-title"] = []
    web.oa_search = []
    r = await engine.verify_citation(attention(2017))  # 18 years apart: a real year mismatch, not a late registration
    assert r.verdict == "suspicious"
    assert "None" not in r.reason and "unknown journal" not in r.reason


# ----------------------------------------------------------------------------- 2. later registration and duplicate records
def test_a_bare_record_registered_years_later_is_a_soft_year_difference():
    cmp = compare(attention(2017), bare_record(2025))
    assert cmp.fields["year"].status == "partial"


def test_a_preprint_record_dated_later_is_a_soft_year_difference():
    cmp = compare(attention(2017), bare_record(2020, journal="arXiv", type_="posted-content"))
    assert cmp.fields["year"].status == "partial"


def test_a_full_article_record_with_a_different_year_is_still_a_mismatch():
    full = bare_record(2020, journal="Some Journal")
    full.volume, full.pages = "12", "1-9"
    assert compare(attention(2017), full).fields["year"].status == "mismatch"


def test_a_gap_over_ten_years_or_a_different_author_list_is_still_a_mismatch():
    assert compare(attention(2005), bare_record(2025)).fields["year"].status == "mismatch"
    other = bare_record(2025)
    other.authors = [Author(family="Nobody", given="N")]
    assert compare(attention(2017), other).fields["year"].status == "mismatch"


def test_collapse_prefers_the_cited_year_then_the_earliest():
    later, original = bare_record(2025, "10.65215/a"), bare_record(2017, "10.5555/b", journal="NeurIPS")
    rec, n = collapse_duplicates(later, [original, later], 2017)
    assert n == 2 and rec.year == 2017 and rec.alt_years == [2025]
    rec, n = collapse_duplicates(later, [original], 2012)  # nothing matches the cited year: the original publication wins
    assert rec.year == 2017 and rec.alt_years == [2025]


def test_records_with_the_same_generic_title_but_no_authors_are_not_duplicates():
    a, b = PaperRecord(title="Introduction", year=2001), PaperRecord(title="Introduction", year=2020)
    assert collapse_duplicates(a, [b], 2001) == (a, 1)


async def test_attention_matches_a_late_copy_and_is_verified_with_a_note(engine, web):
    web.cr_search = [crossref_item("10.65215/r5bs2d54", TITLE, AUTH, "", 2025)]
    web.cr_search[0]["container-title"] = []
    web.oa_search = [openalex_work("10.65215/r5bs2d54", TITLE, [f"{g} {f}" for f, g in AUTH], "", 2025, "W1")]
    r = await engine.verify_citation(attention(2017))
    assert r.verdict == "verified" and r.confidence <= 100
    assert "None" not in r.reason and "year differs slightly" in r.reason


async def test_duplicates_in_one_source_use_the_copy_dated_the_cited_year(engine, web):
    web.cr_search = [
        crossref_item("10.65215/late", TITLE, AUTH, "Re-registered Press", 2025),
        crossref_item("10.5555/original", TITLE, AUTH, "Advances in Neural Information Processing Systems", 2017, volume="30"),
    ]
    r = await engine.verify_citation(attention(2017))
    cr = next(s for s in r.sources if s.source == "crossref")
    assert cr.record.year == 2017 and 2025 in cr.record.alt_years
    assert any("copies of the same work" in c["outcome"] for c in r.calls)


# ----------------------------------------------------------------------------- 3. "not indexed" instead of Ghost for a real, thinly indexed journal
def thin_citation(journal="Journal of Thin Index", year=2020):
    return ParsedCitation(
        index=1, raw=f"Rao, K. ({year}). Twisting limits of open-end rotor yarns. {journal}, 4(2), 11-19.", title="Twisting limits of open-end rotor yarns",
        authors=[Author(family="Rao", given="Kiran")], journal=journal, year=year, volume="4", issue="2", pages="11-19", kind="article",
    )


def known_journal(web, title="Journal of Thin Index", issn="1111-2222", year_count=3):
    web.cr_journals = [{"title": title, "ISSN": [issn], "publisher": "Small Press"}]
    web.journal_year_counts[(issn, 2020)] = year_count


async def test_real_but_thinly_indexed_journal_gives_not_indexed_instead_of_ghost(engine, web):
    known_journal(web, year_count=3)
    r = await engine.verify_citation(thin_citation())
    assert r.verdict == "inconclusive" and r.confidence is None
    assert any(f.code == scoring.COVERAGE_CODE for f in r.flags)
    assert r.reason.startswith("Not indexed:") and "only 3 articles from 2020" in r.reason


async def test_a_well_covered_journal_keeps_the_ghost_verdict(engine, web):
    known_journal(web, year_count=140)  # CrossRef knows this journal well, so the paper's absence IS evidence
    r = await engine.verify_citation(thin_citation())
    assert r.verdict == "ghost" and not any(f.code == scoring.COVERAGE_CODE for f in r.flags)


async def test_an_unknown_journal_stays_a_ghost(engine, web):
    r = await engine.verify_citation(thin_citation("Journal of Spun Yarns & Textiles"))
    assert r.verdict == "ghost"


async def test_a_near_miss_journal_name_stays_a_ghost(engine, web):
    known_journal(web, title="Journal of Thin Indices", year_count=2)  # the citation says "Index", not "Indices": a different name
    r = await engine.verify_citation(thin_citation())
    assert r.verdict == "ghost" and any(f.code == "journal_near_miss" for f in r.flags)


async def test_a_journal_listed_only_in_doaj_gives_not_indexed(engine, web):
    web.doaj_titles["journal of thin index"] = {"title": "Journal of Thin Index", "eissn": "3333-4444", "publisher": {"name": "Small Press"}}
    r = await engine.verify_citation(thin_citation())
    assert r.verdict == "inconclusive" and "listed in DOAJ" in r.reason


async def test_another_warning_sign_keeps_the_ghost_verdict(engine, web):
    known_journal(web, year_count=3)
    p = thin_citation()
    p.doi = "10.1234/1234567"  # placeholder-looking DOI (medium flag)
    r = await engine.verify_citation(p)
    assert r.verdict == "ghost"


async def test_not_indexed_survives_a_rescore(engine, web):
    known_journal(web, year_count=3)
    r = await engine.verify_citation(thin_citation())
    engine.rescore(r)
    assert r.verdict == "inconclusive" and r.reason.startswith("Not indexed:")


# ----------------------------------------------------------------------------- 4. time budgets
class SlowAdapter(SourceAdapter):
    name, label = "crossref", "CrossRef REST API"

    async def lookup_doi(self, doi):
        await asyncio.sleep(5)
        return AdapterResponse("not_found")

    async def search(self, citation, rows=10, year_window=False):
        await asyncio.sleep(5)
        return AdapterResponse("not_found")


async def test_a_source_that_exceeds_its_budget_is_unavailable_not_ghost(http, settings):
    from app.services.adapters.crossref import CrossRefAdapter
    from app.services.adapters.doaj import DoajAdapter
    from app.services.adapters.openalex import OpenAlexAdapter

    quick = settings.model_copy(update={"source_time_budget_seconds": 0.05})
    eng = VerificationEngine(CrossRefAdapter(http, quick), OpenAlexAdapter(http, quick), DoajAdapter(http, quick), quick)
    eng.crossref = SlowAdapter()  # type: ignore[assignment]
    eng.openalex = SlowAdapter()  # type: ignore[assignment]
    started = asyncio.get_event_loop().time()
    r = await eng.verify_citation(thin_citation())
    assert asyncio.get_event_loop().time() - started < 2
    assert r.verdict == "inconclusive" and any(s.status == "unavailable" for s in r.sources)
    assert any("time budget" in c["query"] for c in r.calls)    