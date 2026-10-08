"""Verification endpoints: Modes A, B and C, jobs, progress, report and log exports."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response, StreamingResponse

from .. import deps
from ..schemas import CountResponse, JobCreated, JobResult, JobStatus, LogResponse, TextBody, VerifyCitationsBody
from ..services import jobs, reports, transparency
from ..services.splitter import count_citations

router = APIRouter()
ALLOWED_EXT = {".pdf", ".docx", ".bib", ".ris"}


@router.post("/citations/count", response_model=CountResponse, tags=["verify"])
def count(body: TextBody) -> CountResponse:
    """Live 'citations detected' counter for the paste box."""
    return CountResponse(count=count_citations(body.text))


@router.post("/verify/citations", response_model=JobCreated, tags=["verify"])
async def verify_citations(body: VerifyCitationsBody, background: BackgroundTasks) -> JobCreated:
    """Mode A: verify pasted citations (any style)."""
    if not body.text.strip():
        raise HTTPException(400, "Paste at least one citation.")
    job_id = jobs.create_job("citations", {"chars": len(body.text)})
    background.add_task(jobs.run_citations_job, job_id, body.text, deps.engine, deps.grobid, deps.settings)
    return JobCreated(job_id=job_id)


@router.post("/verify/document", response_model=JobCreated, tags=["verify"])
async def verify_document(background: BackgroundTasks, file: UploadFile = File(...), relevance: bool = Form(False)) -> JobCreated:
    """Modes B and C: upload a PDF/DOCX (or a BibTeX/RIS file). `relevance=true` adds the relevance check."""
    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED_EXT:
        raise HTTPException(400, f"Unsupported file type '{ext or 'unknown'}'. Upload a PDF, DOCX, BibTeX (.bib) or RIS (.ris) file.")
    data = await file.read()
    if len(data) > deps.settings.max_upload_mb * 1024 * 1024:
        raise HTTPException(413, f"The file is larger than {deps.settings.max_upload_mb} MB.")
    if not data:
        raise HTTPException(400, "The uploaded file is empty.")
    mode = "document+relevance" if relevance and ext in (".pdf", ".docx") else ("document" if ext in (".pdf", ".docx") else "import")
    job_id = jobs.create_job(mode, {"filename": file.filename, "relevance": relevance})
    background.add_task(jobs.run_upload_job, job_id, file.filename or f"upload{ext}", data, relevance, deps.engine, deps.grobid, deps.settings)
    return JobCreated(job_id=job_id)


def _job_or_404(job_id: str) -> dict:
    job = jobs.get_job(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return job


def _status(job: dict, with_result: bool = True) -> JobStatus:
    return JobStatus(
        id=job["id"], mode=job["mode"], status=job["status"], stage=job["stage"], total=job["total"], completed=job["completed"], error=job["error"],
        result=JobResult.model_validate(job["result"]) if with_result and job["result"] else None,
    )


@router.get("/jobs/{job_id}", response_model=JobStatus, tags=["jobs"])
def get_job(job_id: str) -> JobStatus:
    return _status(_job_or_404(job_id))


@router.get("/jobs/{job_id}/stream", tags=["jobs"])
async def stream_job(job_id: str) -> StreamingResponse:
    """Server-Sent Events: live progress ('Checking citation 3 of 12…') until the job finishes."""
    _job_or_404(job_id)

    async def events():
        last = None
        while True:
            job = jobs.get_job(job_id)
            if job is None:
                break
            payload = {"id": job["id"], "status": job["status"], "stage": job["stage"], "total": job["total"], "completed": job["completed"], "error": job["error"]}
            if payload != last:
                yield f"event: progress\ndata: {json.dumps(payload)}\n\n"
                last = payload
            if job["status"] in ("done", "error"):
                name = "done" if job["status"] == "done" else "failed"  # "error" is reserved by EventSource for connection errors
                yield f"event: {name}\ndata: {json.dumps(payload)}\n\n"
                break
            await asyncio.sleep(0.5)

    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _done_result(job_id: str) -> JobResult:
    job = _job_or_404(job_id)
    if job["status"] != "done" or not job["result"]:
        raise HTTPException(409, "The job has not finished yet.")
    return JobResult.model_validate(job["result"])


@router.get("/jobs/{job_id}/export", tags=["jobs"])
def export_report(job_id: str, format: str = Query("csv", pattern="^(csv|pdf)$")) -> Response:
    """Download the verification report (verdicts, scores, per-source detail)."""
    jr = _done_result(job_id)
    if format == "pdf":
        return Response(reports.report_pdf(jr, job_id), media_type="application/pdf", headers={"Content-Disposition": 'attachment; filename="verification-report.pdf"'})
    return Response(reports.report_csv(jr), media_type="text/csv", headers={"Content-Disposition": 'attachment; filename="verification-report.csv"'})


@router.get("/jobs/{job_id}/log", tags=["jobs"])
def get_log(job_id: str, format: str = Query("json", pattern="^(json|csv|pdf)$")):
    """The transparency log for a job; `format=csv|pdf` downloads it (final hash printed on the export)."""
    _job_or_404(job_id)
    log = transparency.read_log(job_id)
    if format == "pdf":
        return Response(reports.log_pdf(log), media_type="application/pdf", headers={"Content-Disposition": 'attachment; filename="transparency-log.pdf"'})
    if format == "csv":
        return Response(reports.log_csv(log), media_type="text/csv", headers={"Content-Disposition": 'attachment; filename="transparency-log.csv"'})
    return LogResponse.model_validate(log)


@router.get("/jobs/{job_id}/log/verify", tags=["jobs"])
def verify_log(job_id: str) -> dict:
    """Recompute the hash chain from the stored rows and report whether it is intact."""
    _job_or_404(job_id)
    log = transparency.read_log(job_id)
    return {"job_id": job_id, "entries": len(log.entries), "chain_valid": log.chain_valid, "final_hash": log.final_hash, "first_invalid_seq": log.first_invalid_seq}
