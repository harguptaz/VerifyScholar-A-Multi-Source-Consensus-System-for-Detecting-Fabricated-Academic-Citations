"""The Ghost explanation only says "not found" when nothing was found."""
from app.schemas import FieldMatch, PaperRecord, ParsedCitation, SourceResult
from app.services import scoring


def parsed():
    return ParsedCitation(index=1, raw="Doe (2020). Some cited title. Journal.", title="Some cited title", doi="10.1/abc", year=2020)


def test_ghost_with_a_weak_match_names_the_record_and_the_differences():
    rec = PaperRecord(title="A different paper entirely", journal="Other Journal", year=2012, doi="10.1/abc")
    fields = {"title": FieldMatch(submitted="Some cited title", found="A different paper entirely", similarity=0.2, status="mismatch")}
    cr = SourceResult(source="crossref", label="CrossRef", status="partial", method="doi", score=20, fields=fields, record=rec)
    oa = SourceResult(source="openalex", label="OpenAlex", status="no_record", score=0)
    text = scoring.build_reason(parsed(), "ghost", [cr, oa], [], rec, "found", cr)
    assert "closest record found" in text and "A different paper entirely" in text and "title (you cited" in text
    assert "could not be found" not in text  # the DOI WAS found


def test_ghost_with_no_record_still_says_nothing_was_found():
    cr = SourceResult(source="crossref", label="CrossRef", status="no_record", score=0)
    oa = SourceResult(source="openalex", label="OpenAlex", status="no_record", score=0)
    text = scoring.build_reason(parsed(), "ghost", [cr, oa], [], None, "not_found", None)
    assert "could not be found" in text and "CrossRef or OpenAlex" in text
