"""
api_pipeline_agent.py — LLM-powered .xlan generation endpoints.

POST /api/projects/{pid}/pipeline/agent-start        — start from raw text
POST /api/projects/{pid}/pipeline/agent-start-file   — start from uploaded file
GET  /api/projects/{pid}/pipeline/agent-progress/{job_id} — SSE progress stream
"""

from __future__ import annotations

import asyncio
import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import AsyncGenerator

from fastapi import APIRouter, HTTPException, UploadFile, File, Form, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.services.project_service import load_metadata
from app.services.text_extractor import extract_text
from app.services.pipeline_agent import XlanSession, run_xlan_agent

router = APIRouter(prefix="/api/projects", tags=["pipeline-agent"])

# ── In-memory job store ──────────────────────────────────────────
JOBS: dict[str, dict] = {}
_executor = ThreadPoolExecutor(max_workers=4)


# ── Request schema ───────────────────────────────────────────────

class StartTextRequest(BaseModel):
    title: str
    description: str = ""
    raw_text: str
    source_type: str = "text"        # "text" | "ocr"
    extend_base: str | None = None   # base .xlan filename to extend


# ── Endpoints ────────────────────────────────────────────────────

@router.post("/{project_id}/pipeline/agent-start")
async def agent_start_text(request: Request, project_id: str, body: StartTextRequest):
    """Start an agent job from pasted text."""
    ud = request.state.user_dir
    meta = load_metadata(project_id, ud)
    if not meta:
        raise HTTPException(404, "Proyecto no encontrado")
    if not body.raw_text.strip():
        raise HTTPException(400, "El texto no puede estar vacío")

    return await _enqueue_job(
        project_id=project_id,
        meta=meta,
        raw_text=body.raw_text.strip(),
        title=body.title or "Sin título",
        description=body.description,
        user_dir=ud,
        source_type=body.source_type,
        extend_base=body.extend_base,
    )


@router.post("/{project_id}/pipeline/agent-start-file")
async def agent_start_file(
    request: Request,
    project_id: str,
    file: UploadFile = File(...),
    title: str = Form(""),
    description: str = Form(""),
    extend_base: str = Form(""),
):
    """Start an agent job from an uploaded file (PDF, DOCX, TXT, MD)."""
    ud = request.state.user_dir
    meta = load_metadata(project_id, ud)
    if not meta:
        raise HTTPException(404, "Proyecto no encontrado")

    content = await file.read()
    if not content:
        raise HTTPException(400, "El archivo está vacío")

    try:
        raw_text = extract_text(file.filename or "upload.txt", content)
    except ImportError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(400, f"Error al leer el archivo: {e}")

    if not raw_text.strip():
        raise HTTPException(400, "No se pudo extraer texto del archivo")

    used_title = title.strip() or (file.filename or "").rsplit(".", 1)[0] or "Sin título"

    return await _enqueue_job(
        project_id=project_id,
        meta=meta,
        raw_text=raw_text.strip(),
        title=used_title,
        description=description,
        user_dir=ud,
        source_type="file",
        extend_base=extend_base.strip() or None,
    )


@router.get("/{project_id}/pipeline/agent-progress/{job_id}")
async def agent_progress(project_id: str, job_id: str):
    """Server-Sent Events stream for a running agent job."""
    if job_id not in JOBS:
        raise HTTPException(404, "Job no encontrado")

    queue: asyncio.Queue = JOBS[job_id]["queue"]

    async def event_stream() -> AsyncGenerator[str, None]:
        while True:
            try:
                msg = await asyncio.wait_for(queue.get(), timeout=90.0)
                yield f"data: {json.dumps(msg, ensure_ascii=False)}\n\n"
                if msg.get("type") in ("done", "error"):
                    break
            except asyncio.TimeoutError:
                yield 'data: {"type":"heartbeat"}\n\n'

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


# ── Internal helpers ─────────────────────────────────────────────

async def _enqueue_job(
    project_id: str,
    meta: dict,
    raw_text: str,
    title: str,
    description: str,
    user_dir: str = "public",
    source_type: str = "text",
    extend_base: str | None = None,
) -> dict:
    job_id = str(uuid.uuid4())
    queue: asyncio.Queue = asyncio.Queue()
    loop = asyncio.get_event_loop()

    JOBS[job_id] = {"status": "running", "queue": queue}

    session = XlanSession(
        project_id=project_id,
        title=title,
        description=description,
        text_language=meta.get("target", "en"),
        notes_language=meta.get("base", "es"),
        user_dir=user_dir,
        source_type=source_type,
        extend_base=extend_base,
    )

    def _send(msg: dict) -> None:
        asyncio.run_coroutine_threadsafe(queue.put(msg), loop)

    def _run() -> None:
        try:
            _send({"type": "info", "msg": f"Text: {len(raw_text)} characters"})
            session.on_progress = lambda m: _send({"type": "progress", "msg": m})
            filename = run_xlan_agent(session, raw_text)
            _send({
                "type": "done",
                "filename": filename,
                "blocks": len(session.content),
                "segments": session.seg_counter,
            })
        except Exception as exc:
            _send({"type": "error", "msg": str(exc)})
        finally:
            JOBS[job_id]["status"] = "done"

    loop.run_in_executor(_executor, _run)
    return {"job_id": job_id, "chars": len(raw_text)}
