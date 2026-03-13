from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from pydantic import BaseModel
from typing import Optional

from app.services.project_service import load_metadata
from app.services.xlan_service import pipeline_text_to_xlan

router = APIRouter(prefix="/api/projects", tags=["pipeline"])


class TranslateRequest(BaseModel):
    title: str
    description: str = ""
    raw_text: str


@router.post("/{project_id}/pipeline/translate")
async def run_pipeline(project_id: str, body: TranslateRequest):
    meta = load_metadata(project_id)
    if not meta:
        raise HTTPException(404, "Proyecto no encontrado")

    result = pipeline_text_to_xlan(
        project_id=project_id,
        raw_text=body.raw_text,
        title=body.title,
        description=body.description,
        text_language=meta["target"],
        notes_language=meta["base"],
    )
    return {
        "ok": True,
        "filename": result["filename"],
        "segments_created": sum(
            len(block.get("segments", [])) for block in result["xlan"]["content"]
        ),
    }


@router.post("/{project_id}/pipeline/translate-file")
async def run_pipeline_from_file(
    project_id: str,
    file: UploadFile = File(...),
    title: str = Form(""),
    description: str = Form(""),
):
    meta = load_metadata(project_id)
    if not meta:
        raise HTTPException(404, "Proyecto no encontrado")

    content = await file.read()
    try:
        raw_text = content.decode("utf-8", errors="replace")
    except Exception:
        raise HTTPException(400, "No se pudo leer el archivo como texto")

    used_title = title.strip() or file.filename.rsplit(".", 1)[0]

    result = pipeline_text_to_xlan(
        project_id=project_id,
        raw_text=raw_text,
        title=used_title,
        description=description,
        text_language=meta["target"],
        notes_language=meta["base"],
    )
    return {
        "ok": True,
        "filename": result["filename"],
        "segments_created": sum(
            len(block.get("segments", [])) for block in result["xlan"]["content"]
        ),
    }
