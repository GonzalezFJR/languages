from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

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
        source_language=meta["base"],
        target_language=meta["target"],
    )
    return {
        "ok": True,
        "filename": result["filename"],
        "segments_created": sum(
            len(block.get("segments", [])) for block in result["xlan"]["content"]
        ),
    }
