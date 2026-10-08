"""Clean bibliography export (F35) and the session export list (F36)."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from .. import deps
from ..schemas import BibliographyExportBody, CitationResult, JobResult
from ..services import formatters, jobs

router = APIRouter(prefix="/bibliography", tags=["bibliography"])


def bundle_from_job(jr: JobResult, include_suspicious: bool) -> formatters.ExportBundle:
    bundle = formatters.ExportBundle()
    for r in sorted(jr.results, key=lambda x: x.index):
        rec = r.record
        if r.verdict == "verified" and rec:
            bundle.entries.append(formatters.ExportEntry(rec, "verified", r.index))
        elif r.verdict == "suspicious" and rec and include_suspicious:
            bundle.entries.append(formatters.ExportEntry(rec, "review", r.index))
        else:
            why = r.reason if r.verdict != "suspicious" else "Left out because 'include suspicious' was turned off. " + r.reason
            bundle.removed.append(formatters.RemovedEntry(r.index, r.raw, r.verdict, why))
    return bundle


@router.post("/export")
async def export(body: BibliographyExportBody) -> Response:
    """Export the corrected reference list: the job's verified citations plus anything in the session export list."""
    bundle = formatters.ExportBundle()
    if body.job_id:
        job = jobs.get_job(body.job_id)
        if not job:
            raise HTTPException(404, "Job not found")
        if job["status"] != "done" or not job["result"]:
            raise HTTPException(409, "The job has not finished yet.")
        bundle = bundle_from_job(JobResult.model_validate(job["result"]), body.include_suspicious)
    if not bundle.entries and not body.extra_papers and not bundle.removed:
        raise HTTPException(400, "Nothing to export: provide a finished job_id and/or extra_papers.")

    seen = {e.record.doi.lower() for e in bundle.entries if e.record.doi}
    for p in body.extra_papers:
        if p.doi and p.doi.lower() in seen:
            continue
        if p.doi:
            seen.add(p.doi.lower())
        bundle.entries.append(formatters.ExportEntry(p, "extra"))

    content, media, filename = await formatters.build_export(bundle, body.format, body.style, deps.http, deps.settings)
    return Response(content, media_type=f"{media}; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="{filename}"'})
