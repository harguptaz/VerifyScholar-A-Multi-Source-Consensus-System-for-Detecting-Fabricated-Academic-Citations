from sqlalchemy import select

from app.database import LogRow, db
from app.schemas import CitationResult, ParsedCitation
from app.services import transparency


def result(index, verdict, calls):
    p = ParsedCitation(index=index, raw="r")
    return CitationResult(index=index, raw="r", parsed=p, verdict=verdict, calls=calls)


def calls(i, n=2):
    return [{"timestamp": f"2026-10-07T10:00:0{k}+00:00", "citation_index": i, "source": "CrossRef REST API", "query": f"GET /works/10.1/{i}{k}", "outcome": "Match found"} for k in range(n)]


def test_chain_is_valid_and_ordered_by_citation():
    n = transparency.write_job_log("job1", [result(2, "ghost", calls(2)), result(1, "verified", calls(1))])
    assert n == 4
    log = transparency.read_log("job1")
    assert log.chain_valid and log.final_hash == log.entries[-1].entry_hash
    assert [e.citation_index for e in log.entries] == [1, 1, 2, 2]
    assert [e.verdict for e in log.entries] == ["verified", "verified", "ghost", "ghost"]
    assert log.entries[0].prev_hash == transparency.GENESIS and log.entries[1].prev_hash == log.entries[0].entry_hash


def test_editing_an_old_row_breaks_the_chain():
    transparency.write_job_log("job2", [result(1, "verified", calls(1, 3))])
    with db.session() as s:
        row = s.scalars(select(LogRow).where(LogRow.job_id == "job2", LogRow.seq == 2)).one()
        row.outcome = "No record"  # someone rewrites history
    log = transparency.read_log("job2")
    assert not log.chain_valid and log.first_invalid_seq == 2


def test_deleting_a_row_breaks_the_chain():
    transparency.write_job_log("job3", [result(1, "verified", calls(1, 3))])
    with db.session() as s:
        s.delete(s.scalars(select(LogRow).where(LogRow.job_id == "job3", LogRow.seq == 2)).one())
    assert not transparency.read_log("job3").chain_valid


def test_jobs_have_independent_chains():
    transparency.write_job_log("a", [result(1, "verified", calls(1))])
    transparency.write_job_log("b", [result(1, "ghost", calls(1))])
    assert transparency.read_log("a").chain_valid and transparency.read_log("b").chain_valid
    assert transparency.read_log("a").final_hash != transparency.read_log("b").final_hash
