"""DOCX -> PDF conversion with headless LibreOffice, so that the PDF pipeline (GROBID) can be reused."""
from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
from pathlib import Path


class ConversionError(Exception):
    pass


def libreoffice_binary() -> str | None:
    return shutil.which("soffice") or shutil.which("libreoffice")


async def docx_to_pdf(data: bytes, filename: str = "document.docx") -> bytes:
    binary = libreoffice_binary()
    if not binary:
        raise ConversionError(
            "DOCX support needs LibreOffice (the 'soffice' command) on the server. Install it, use the Docker "
            "setup (which includes it), or upload the document as a PDF instead."
        )
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / (Path(filename).stem + ".docx")
        src.write_bytes(data)
        profile = Path(tmp) / "lo-profile"  # private profile avoids lock/permission clashes between runs
        proc = await asyncio.create_subprocess_exec(
            binary, f"-env:UserInstallation=file://{profile}", "--headless", "--convert-to", "pdf", "--outdir", tmp, str(src),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        try:
            _, err = await asyncio.wait_for(proc.communicate(), timeout=120)
        except asyncio.TimeoutError as exc:
            proc.kill()
            raise ConversionError("LibreOffice timed out while converting the DOCX file") from exc
        out = src.with_suffix(".pdf")
        if proc.returncode != 0 or not out.exists():
            raise ConversionError(f"LibreOffice could not convert the file: {err.decode(errors='ignore')[:200]}")
        return out.read_bytes()
