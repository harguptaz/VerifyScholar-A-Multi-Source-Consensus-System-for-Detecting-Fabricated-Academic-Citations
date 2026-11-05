"""ORCID: an authorship check that can only add confidence (served by the fake web)."""
import pytest

from app.schemas import Author, ParsedCitation, Researcher
from app.services.adapters.crossref import CrossRefAdapter
from app.services.adapters.doaj import DoajAdapter
from app.services.adapters.openalex import OpenAlexAdapter
from app.services.adapters.orcid import OrcidAdapter, person_from_result
from app.services.matching import author_is_researcher, match_researchers
from app.services.verifier import VerificationEngine
from tests.fakes import DEEP_LEARNING_DOI, ORCID_CLIENT_ID, ORCID_CLIENT_SECRET, orcid_person, seed_deep_learning

LECUN = orcid_person("0000-0001-0000-0001", "Yann", "LeCun")
HINTON = orcid_person("0000-0001-0000-0003", "Geoffrey", "Hinton")
STRANGER = orcid_person("0000-0009-0000-0009", "Priya", "Raman")


@pytest.fixture
def orcid_settings(settings):
    return settings.model_copy(update={"orcid_client_id": ORCID_CLIENT_ID, "orcid_client_secret": ORCID_CLIENT_SECRET})


def build(http, s, orcid=True):
    return VerificationEngine(
        CrossRefAdapter(http, s), OpenAlexAdapter(http, s), DoajAdapter(http, s), s,
        orcid=OrcidAdapter(http, s) if orcid else None,
    )


@pytest.fixture
def with_orcid(http, orcid_settings):
    return build(http, orcid_settings)


@pytest.fixture
def without_orcid(http, settings):
    return build(http, settings, orcid=False)


def deep_learning(authors=(("LeCun", "Y."), ("Bengio", "Y."), ("Hinton", "G.")), year=2015, journal="Nature", **kw):
    return ParsedCitation(
        index=1, raw="Deep learning (Nature)", doi=DEEP_LEARNING_DOI, title="Deep learning", authors=[Author(family=f, given=g) for f, g in authors],
        journal=journal, year=year, **kw,
    )


def orcid_row(result):
    return next(s for s in result.sources if s.source == "orcid")


def no_orcid_row(result):
    return all(s.source != "orcid" for s in result.sources)


def orcid_calls(web):
    return [c for c in web.calls if "orcid.org" in c]


# ----------------------------------------------------------------------------- reading answers and matching names
def test_reads_an_expanded_search_entry():
    p = person_from_result(orcid_person("0000-0002-1825-0097", "Josiah", "Carberry", credit="J. S. Carberry"))
    assert p.orcid == "0000-0002-1825-0097" and p.family == "Carberry" and p.name == "J. S. Carberry"
    assert p.url == "https://orcid.org/0000-0002-1825-0097"
    assert person_from_result({"given-names": "No", "family-names": "Id"}) is None


def test_a_cited_author_matches_by_surname_and_initial():
    yann = Researcher(orcid="x", given="Yann", family="LeCun")
    assert author_is_researcher(Author(family="LeCun", given="Y."), yann)
    assert author_is_researcher(Author(family="Lecun"), yann)  # no initial cited: the surname decides
    assert not author_is_researcher(Author(family="LeCun", given="M."), yann)  # a different first initial is a different person
    assert not author_is_researcher(Author(family="Bengio", given="Y."), yann)
    # the credit name counts too ("published as")
    assert author_is_researcher(Author(family="Smith", given="A."), Researcher(orcid="y", given="Anna", family="Smith-Jones", name="Anna Smith"))


def test_match_researchers_marks_the_cited_authors():
    people = [Researcher(orcid="1", given="Yann", family="LeCun"), Researcher(orcid="2", given="Priya", family="Raman")]
    confirmed = match_researchers([Author(family="LeCun", given="Y."), Author(family="Hinton", given="G.")], people)
    assert [a.family for a in confirmed] == ["LeCun"]
    assert [p.matched for p in people] == [True, False]


# ----------------------------------------------------------------------------- access token
async def test_the_token_is_fetched_once_and_sent_as_a_bearer_token(http, orcid_settings, web):
    web.orcid_claims[DEEP_LEARNING_DOI] = [LECUN]
    orcid = OrcidAdapter(http, orcid_settings)
    first = await orcid.claimants(DEEP_LEARNING_DOI)
    second = await orcid.person_exists("Yann", "LeCun")
    assert first.status == "ok" and [p.family for p in first.people] == ["LeCun"]
    assert second.status == "not_found"
    assert web.orcid_tokens_issued == 1


async def test_a_refused_token_is_replaced_once(http, orcid_settings, web):
    web.orcid_claims[DEEP_LEARNING_DOI] = [LECUN]
    orcid = OrcidAdapter(http, orcid_settings)
    await orcid.token()
    web.orcid_valid_tokens.clear()  # ORCID revoked it
    res = await orcid.claimants(DEEP_LEARNING_DOI)
    assert res.status == "ok" and web.orcid_tokens_issued == 2


async def test_wrong_credentials_make_orcid_unavailable_with_a_clear_reason(http, settings, web):
    bad = settings.model_copy(update={"orcid_client_id": ORCID_CLIENT_ID, "orcid_client_secret": "wrong"})
    res = await OrcidAdapter(http, bad).claimants(DEEP_LEARNING_DOI)
    assert res.status == "unavailable" and "ORCID_CLIENT_SECRET" in (res.error or "")
    check = await OrcidAdapter(http, bad).check()
    assert check["ok"] is False and "ORCID_CLIENT_ID" in check["detail"]


# ----------------------------------------------------------------------------- the authorship check
async def test_confirmed_authors_add_the_bonus_and_explain_it(with_orcid, without_orcid, web, orcid_settings):
    seed_deep_learning(web)
    web.orcid_claims[DEEP_LEARNING_DOI] = [STRANGER, LECUN, HINTON]
    # one cited co-author is wrong and no journal is given, so the score is below 100 and the bonus is visible
    p = deep_learning(authors=(("LeCun", "Y."), ("Kumar", "R."), ("Hinton", "G.")), journal=None)
    base = await without_orcid.verify_citation(p)
    r = await with_orcid.verify_citation(p)

    row = orcid_row(r)
    assert row.kind == "author" and row.status == "match" and row.method == "doi"
    assert row.note == "2 of 3 cited authors list this paper on their ORCID record"
    assert [x.family for x in row.researchers] == ["LeCun", "Hinton", "Raman"]  # the cited authors first
    assert [x.matched for x in row.researchers] == [True, True, False]
    assert base.confidence <= 100 - orcid_settings.orcid_bonus
    assert r.confidence == base.confidence + orcid_settings.orcid_bonus
    assert "ORCID confirms the authorship: Yann LeCun and Geoffrey Hinton list this paper" in r.reason
    assert any(c["source"] == "ORCID Public API" and "doi-self" in c["query"] for c in r.calls)
    # the secret never reaches the transparency log
    assert ORCID_CLIENT_SECRET not in str(r.calls) and "token-1" not in str(r.calls)


async def test_no_listing_shows_no_row_and_never_lowers_the_score(with_orcid, without_orcid, web):
    seed_deep_learning(web)  # nobody lists the paper on ORCID
    p = deep_learning(authors=(("LeCun", "Y."), ("Kumar", "R.")), journal=None)
    base, r = await without_orcid.verify_citation(p), await with_orcid.verify_citation(p)
    assert no_orcid_row(r) and "ORCID" not in r.reason
    assert (r.verdict, r.confidence) == (base.verdict, base.confidence)
    # the query is still in the transparency log
    assert any(c["source"] == "ORCID Public API" and c["outcome"] == "No record" for c in r.calls)


async def test_claimants_who_are_not_cited_are_shown_but_not_held_against_it(with_orcid, without_orcid, web):
    seed_deep_learning(web)
    web.orcid_claims[DEEP_LEARNING_DOI] = [LECUN]
    p = deep_learning(authors=(("Kumar", "R."), ("Sharma", "A.")), year=2018)  # real paper, wrong authors
    base, r = await without_orcid.verify_citation(p), await with_orcid.verify_citation(p)
    row = orcid_row(r)
    assert row.status == "partial" and row.note == "1 researcher lists this paper on ORCID; none of them is a cited author"
    assert [x.name for x in row.researchers] == ["Yann LeCun"]
    assert (r.verdict, r.confidence) == (base.verdict, base.confidence)
    assert "On ORCID, this paper is listed by Yann LeCun, none of whom is a cited author." in r.reason


async def test_orcid_down_shows_no_row_and_changes_nothing(with_orcid, without_orcid, web):
    seed_deep_learning(web)
    web.down.add("pub.orcid.org")
    p = deep_learning(authors=(("LeCun", "Y."), ("Kumar", "R.")), journal=None)
    base, r = await without_orcid.verify_citation(p), await with_orcid.verify_citation(p)
    assert no_orcid_row(r)
    assert (r.verdict, r.confidence, r.reason) == (base.verdict, base.confidence, base.reason)
    assert any(c["source"] == "ORCID Public API" and c["outcome"].startswith("Source unavailable") for c in r.calls)


async def test_the_bonus_cannot_lift_a_citation_over_a_hard_mismatch(with_orcid, without_orcid, web):
    seed_deep_learning(web)
    web.orcid_claims[DEEP_LEARNING_DOI] = [LECUN, HINTON]
    p = deep_learning(year=2009, volume="521", pages="436-444")  # a year that clearly contradicts the record: capped at 79
    base, r = await without_orcid.verify_citation(p), await with_orcid.verify_citation(p)
    assert orcid_row(r).status == "match"
    assert base.verdict == r.verdict == "suspicious"
    assert r.confidence == base.confidence == 79


async def test_not_asked_when_nothing_was_found_or_no_authors_were_cited(with_orcid, web):
    r = await with_orcid.verify_citation(ParsedCitation(index=1, raw="x", title="A paper that does not exist anywhere at all", authors=[Author(family="Nobody", given="N.")], year=2020))
    assert no_orcid_row(r) and r.verdict == "ghost"
    seed_deep_learning(web)
    r = await with_orcid.verify_citation(ParsedCitation(index=2, raw="x", doi=DEEP_LEARNING_DOI, title="Deep learning", journal="Nature", year=2015))
    assert no_orcid_row(r)
    assert not [c for c in orcid_calls(web) if "doi-self" in c]


async def test_not_configured_means_no_orcid_at_all(http, settings, web):
    assert not settings.orcid_configured
    seed_deep_learning(web)
    engine = VerificationEngine(CrossRefAdapter(http, settings), OpenAlexAdapter(http, settings), DoajAdapter(http, settings), settings,
                                orcid=OrcidAdapter(http, settings))
    r = await engine.verify_citation(deep_learning())
    assert engine.orcid is None and no_orcid_row(r)
    assert not orcid_calls(web)


# ----------------------------------------------------------------------------- the "unknown author" rule
async def test_an_author_known_to_orcid_is_not_flagged_as_unknown(with_orcid, web):
    web.orcid_people = [orcid_person("0000-0005-0000-0005", "Nonexistent", "Writer")]  # OpenAlex (fake) doesn't know a "Nonexistent" person
    p = ParsedCitation(index=1, raw="x", title="An invented study of nothing in particular", authors=[Author(family="Writer", given="Nonexistent")], journal="Imaginary Journal", year=2020)
    r = await with_orcid.verify_citation(p)
    assert not any(f.code == "author_not_found" for f in r.flags)


async def test_an_author_unknown_to_both_registries_is_flagged(with_orcid, web):
    p = ParsedCitation(index=1, raw="x", title="An invented study of nothing in particular", authors=[Author(family="Writer", given="Nonexistent")], journal="Imaginary Journal", year=2020)
    r = await with_orcid.verify_citation(p)
    flag = next(f for f in r.flags if f.code == "author_not_found")
    assert flag.message == "No researcher named Nonexistent Writer was found in OpenAlex or ORCID"
