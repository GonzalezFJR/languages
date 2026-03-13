import json
from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import Optional

from app.services.project_service import load_metadata
from app.services.document_service import (
    list_docs,
    list_translates,
    save_uploaded_doc,
    delete_doc,
    delete_translate,
    update_section_metadata,
    update_file_meta,
    update_available_tags,
    get_doc_path,
    get_translate_path,
)
from app.services.xlan_service import update_block_note, update_segment_note, update_xlan_file_meta

router = APIRouter(prefix="/api/projects", tags=["documents"])


@router.get("/{project_id}/docs")
async def get_docs(project_id: str):
    if not load_metadata(project_id):
        raise HTTPException(404, "Proyecto no encontrado")
    return list_docs(project_id)


@router.post("/{project_id}/docs")
async def upload_doc(
    project_id: str,
    file: UploadFile = File(...),
    display_name: str = Form(""),
    description: str = Form(""),
):
    if not load_metadata(project_id):
        raise HTTPException(404, "Proyecto no encontrado")
    content = await file.read()
    try:
        info = save_uploaded_doc(project_id, file.filename, content, display_name, description)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return info


@router.delete("/{project_id}/docs/{filename}")
async def remove_doc(project_id: str, filename: str):
    if not load_metadata(project_id):
        raise HTTPException(404, "Proyecto no encontrado")
    delete_doc(project_id, filename)
    return {"ok": True}


@router.get("/{project_id}/docs/file/{filename:path}")
async def serve_doc(project_id: str, filename: str):
    path = get_doc_path(project_id, filename)
    if not path:
        raise HTTPException(404, "Archivo no encontrado")
    return FileResponse(str(path))


@router.get("/{project_id}/translates")
async def get_translates(project_id: str):
    if not load_metadata(project_id):
        raise HTTPException(404, "Proyecto no encontrado")
    return list_translates(project_id)


@router.delete("/{project_id}/translates/{filename}")
async def remove_translate(project_id: str, filename: str):
    if not load_metadata(project_id):
        raise HTTPException(404, "Proyecto no encontrado")
    delete_translate(project_id, filename)
    return {"ok": True}


@router.get("/{project_id}/translates/file/{filename:path}")
async def serve_xlan(project_id: str, filename: str):
    path = get_translate_path(project_id, filename)
    if not path:
        raise HTTPException(404, "Archivo no encontrado")
    return FileResponse(str(path))


@router.put("/{project_id}/docs/metadata")
async def update_docs_metadata(project_id: str, body: dict):
    if not load_metadata(project_id):
        raise HTTPException(404, "Proyecto no encontrado")
    update_section_metadata(project_id, "docs", body)
    return {"ok": True}


@router.put("/{project_id}/translates/metadata")
async def update_translates_metadata(project_id: str, body: dict):
    if not load_metadata(project_id):
        raise HTTPException(404, "Proyecto no encontrado")
    update_section_metadata(project_id, "translates", body)
    return {"ok": True}


# ── File-level metadata ──────────────────────────────────────────

class FileMetaUpdate(BaseModel):
    display_name: Optional[str] = None
    description: Optional[str] = None
    tags: Optional[list] = None


@router.patch("/{project_id}/docs/{filename}/meta")
async def patch_doc_meta(project_id: str, filename: str, body: FileMetaUpdate):
    if not load_metadata(project_id):
        raise HTTPException(404, "Proyecto no encontrado")
    try:
        info = update_file_meta(project_id, "docs", filename, body.model_dump(exclude_none=True))
    except ValueError as e:
        raise HTTPException(404, str(e))
    return info


@router.patch("/{project_id}/translates/{filename}/meta")
async def patch_xlan_meta(project_id: str, filename: str, body: FileMetaUpdate):
    if not load_metadata(project_id):
        raise HTTPException(404, "Proyecto no encontrado")
    try:
        info = update_file_meta(project_id, "translates", filename, body.model_dump(exclude_none=True))
    except ValueError as e:
        raise HTTPException(404, str(e))
    return info


@router.put("/{project_id}/docs/tags")
async def put_docs_tags(project_id: str, body: dict):
    if not load_metadata(project_id):
        raise HTTPException(404, "Proyecto no encontrado")
    return {"available_tags": update_available_tags(project_id, "docs", body.get("available_tags", []))}


@router.put("/{project_id}/translates/tags")
async def put_translates_tags(project_id: str, body: dict):
    if not load_metadata(project_id):
        raise HTTPException(404, "Proyecto no encontrado")
    return {"available_tags": update_available_tags(project_id, "translates", body.get("available_tags", []))}


# ── Block annotations ────────────────────────────────────────────

class BlockNoteBody(BaseModel):
    highlight_color: Optional[str] = None
    highlight_alpha: float = 0.35
    bold: bool = False
    italic: bool = False
    underline: bool = False
    underline_color: Optional[str] = None
    text_color: Optional[str] = None
    comment: str = ""


@router.put("/{project_id}/translates/{filename}/note/{block_index}")
async def put_block_note(project_id: str, filename: str, block_index: int, body: BlockNoteBody):
    if not load_metadata(project_id):
        raise HTTPException(404, "Proyecto no encontrado")
    try:
        note = update_block_note(project_id, filename, block_index, body.model_dump())
    except ValueError as e:
        raise HTTPException(400, str(e))
    return note


@router.delete("/{project_id}/translates/{filename}/note/{block_index}")
async def delete_block_note(project_id: str, filename: str, block_index: int):
    if not load_metadata(project_id):
        raise HTTPException(404, "Proyecto no encontrado")
    try:
        update_block_note(project_id, filename, block_index, None)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


# ── Segment annotations ──────────────────────────────────────────

@router.put("/{project_id}/translates/{filename}/note/{block_index}/seg/{seg_id}")
async def put_segment_note(project_id: str, filename: str, block_index: int, seg_id: str, body: BlockNoteBody):
    if not load_metadata(project_id):
        raise HTTPException(404, "Proyecto no encontrado")
    try:
        note = update_segment_note(project_id, filename, block_index, seg_id, body.model_dump())
    except ValueError as e:
        raise HTTPException(400, str(e))
    return note


@router.delete("/{project_id}/translates/{filename}/note/{block_index}/seg/{seg_id}")
async def delete_segment_note(project_id: str, filename: str, block_index: int, seg_id: str):
    if not load_metadata(project_id):
        raise HTTPException(404, "Proyecto no encontrado")
    try:
        update_segment_note(project_id, filename, block_index, seg_id, None)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}
