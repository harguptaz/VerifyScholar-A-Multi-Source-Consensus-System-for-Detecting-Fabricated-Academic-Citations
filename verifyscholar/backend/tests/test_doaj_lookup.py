"""The DOAJ journal check tries every ISSN and the journal name before it reports 'Not listed'."""
from app.schemas import Author, PaperRecord, ParsedCitation
from app.services.adapters.doaj import normalize_issn
from app.services.http_client import RequestContext
from tests.fakes import crossref_item, openalex_work

PLOS_MEDICINE = {"title": "PLOS Medicine", "eissn": "1549-1676", "pissn": "1549-1277", "publisher": {"name": "Public Library of Science (PLoS)"}}


def record(issn=None, journal="PLoS Medicine"):
    return PaperRecord(title="Why most published research findings are false", journal=journal, issn=issn or [], year=2005, sources=["crossref"])


def cited(journal="PLoS Medicine", issn=None):
    return ParsedCitation(index=1, raw="Ioannidis (2005). Why most published research findings are false.", title="Why most published research findings are false",
                          authors=[Author(family="Ioannidis", given="J. P. A.")], journal=journal, year=2005, kind="article", issn=issn)


def test_normalize_issn_accepts_the_usual_spellings_and_rejects_the_rest():
    assert normalize_issn("1549-1676") == "1549-1676"
    assert normalize_issn("15491676") == "1549-1676"
    assert normalize_issn(" ISSN 1549-1676 ") == "1549-1676"
    assert normalize_issn("0378-595x") == "0378-595X"
    assert normalize_issn("1549-16") is None and normalize_issn("") is None and normalize_issn(None) is None and normalize_issn("PLoS Medicine") is None


async def test_the_second_issn_is_tried_when_the_first_is_not_in_doaj(engine, web):
    web.doaj_issns["1549-1676"] = PLOS_MEDICINE
    res = await engine._journal_source(record(issn=["1111-2222", "1549-1676"]), cited(), RequestContext())
    assert res.status == "match" and res.method == "issn"


async def test_the_journal_name_is_tried_when_no_issn_is_in_doaj(engine, web):
    web.doaj_titles["plos medicine"] = PLOS_MEDICINE
    ctx = RequestContext()
    res = await engine._journal_source(record(issn=["1111-2222"]), cited(), ctx)
    assert res.status == "match" and res.method == "title" and "PLOS Medicine" in (res.note or "")
    assert [c["outcome"] for c in ctx.calls] == ["No record", "Match found"]  # the transparency log shows both attempts


async def test_an_issn_without_its_hyphen_is_still_found(engine, web):
    web.doaj_issns["1549-1676"] = PLOS_MEDICINE
    res = await engine._journal_source(record(issn=["15491676"]), cited(), RequestContext())
    assert res.status == "match"


async def test_the_issn_in_the_citation_is_used_when_the_record_has_none(engine, web):
    web.doaj_issns["1549-1676"] = PLOS_MEDICINE
    res = await engine._journal_source(record(), cited(journal=None, issn="1549-1676"), RequestContext())
    assert res.status == "match"


async def test_the_journal_name_as_cited_is_the_last_resort(engine, web):
    web.doaj_titles["plos medicine"] = PLOS_MEDICINE
    res = await engine._journal_source(record(journal="Public Library of Science Medicine"), cited(journal="PLOS Medicine"), RequestContext())
    assert res.status == "match" and res.method == "title"


async def test_not_listed_only_after_every_attempt_has_missed(engine, web):
    ctx = RequestContext()
    res = await engine._journal_source(record(issn=["1111-2222", "3333-4444", "5555-6666"]), cited(journal="Some Closed Journal"), ctx)
    assert res.status == "no_record" and "Not listed" in (res.note or "")
    assert len(ctx.calls) == 4 and all(c["outcome"] == "No record" for c in ctx.calls)  # two ISSNs, then the record's name and the cited name


async def test_a_doaj_outage_is_unavailable_not_not_listed(engine, web):
    web.down = {"doaj.org"}
    res = await engine._journal_source(record(issn=["1549-1676"]), cited(), RequestContext())
    assert res.status == "unavailable"


async def test_no_journal_information_is_skipped(engine, web):
    res = await engine._journal_source(PaperRecord(title="x", sources=["crossref"]), cited(journal=None), RequestContext())
    assert res.status == "skipped" and not web.calls


async def test_a_verified_citation_still_gets_a_doaj_match_from_the_journal_name(engine, web):
    doi = "10.1371/journal.pmed.0020124"
    web.crossref[doi] = crossref_item(doi, "Why most published research findings are false", [("Ioannidis", "John P. A.")], "PLoS Medicine", 2005,
                                      volume="2", issue="8", page="e124", issn=["9999-0000"])
    web.openalex[doi] = openalex_work(doi, "Why most published research findings are false", ["John P. A. Ioannidis"], "PLoS Medicine", 2005, "W1", volume="2")
    web.doaj_titles["plos medicine"] = PLOS_MEDICINE
    p = cited()
    p.doi, p.volume, p.pages = doi, "2", "e124"
    r = await engine.verify_citation(p)
    doaj = next(s for s in r.sources if s.source == "doaj")
    assert r.verdict == "verified" and doaj.status == "match"