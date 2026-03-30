import json
from fastapi import APIRouter, HTTPException, UploadFile, File, Form, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import Optional, List

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
from app.services.xlan_service import update_block_note, update_segment_note, update_xlan_file_meta, load_xlan, update_linebreaks, save_xlan, register_xlan_in_metadata

router = APIRouter(prefix="/api/projects", tags=["documents"])


@router.get("/{project_id}/docs")
async def get_docs(request: Request, project_id: str):
    ud = request.state.user_dir
    if not load_metadata(project_id, ud):
        raise HTTPException(404, "Proyecto no encontrado")
    return list_docs(project_id, ud)


@router.post("/{project_id}/docs")
async def upload_doc(
    request: Request,
    project_id: str,
    file: UploadFile = File(...),
    display_name: str = Form(""),
    description: str = Form(""),
):
    ud = request.state.user_dir
    if not load_metadata(project_id, ud):
        raise HTTPException(404, "Proyecto no encontrado")
    content = await file.read()
    try:
        info = save_uploaded_doc(project_id, file.filename, content, display_name, description, user_dir=ud)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return info


@router.delete("/{project_id}/docs/{filename}")
async def remove_doc(request: Request, project_id: str, filename: str):
    ud = request.state.user_dir
    if not load_metadata(project_id, ud):
        raise HTTPException(404, "Proyecto no encontrado")
    delete_doc(project_id, filename, ud)
    return {"ok": True}


@router.get("/{project_id}/docs/file/{filename:path}")
async def serve_doc(request: Request, project_id: str, filename: str):
    ud = request.state.user_dir
    path = get_doc_path(project_id, filename, ud)
    if not path:
        raise HTTPException(404, "Archivo no encontrado")
    return FileResponse(str(path))


@router.get("/{project_id}/translates")
async def get_translates(request: Request, project_id: str):
    ud = request.state.user_dir
    if not load_metadata(project_id, ud):
        raise HTTPException(404, "Proyecto no encontrado")
    return list_translates(project_id, ud)


@router.delete("/{project_id}/translates/{filename}")
async def remove_translate(request: Request, project_id: str, filename: str):
    ud = request.state.user_dir
    if not load_metadata(project_id, ud):
        raise HTTPException(404, "Proyecto no encontrado")
    delete_translate(project_id, filename, ud)
    return {"ok": True}


@router.get("/{project_id}/translates/file/{filename:path}")
async def serve_xlan(request: Request, project_id: str, filename: str):
    ud = request.state.user_dir
    path = get_translate_path(project_id, filename, ud)
    if not path:
        raise HTTPException(404, "Archivo no encontrado")
    return FileResponse(str(path))


@router.put("/{project_id}/docs/metadata")
async def update_docs_metadata(request: Request, project_id: str, body: dict):
    ud = request.state.user_dir
    if not load_metadata(project_id, ud):
        raise HTTPException(404, "Proyecto no encontrado")
    update_section_metadata(project_id, "docs", body, ud)
    return {"ok": True}


@router.put("/{project_id}/translates/metadata")
async def update_translates_metadata(request: Request, project_id: str, body: dict):
    ud = request.state.user_dir
    if not load_metadata(project_id, ud):
        raise HTTPException(404, "Proyecto no encontrado")
    update_section_metadata(project_id, "translates", body, ud)
    return {"ok": True}


# ── File-level metadata ──────────────────────────────────────────

class FileMetaUpdate(BaseModel):
    display_name: Optional[str] = None
    description: Optional[str] = None
    tags: Optional[list] = None


@router.patch("/{project_id}/docs/{filename}/meta")
async def patch_doc_meta(request: Request, project_id: str, filename: str, body: FileMetaUpdate):
    ud = request.state.user_dir
    if not load_metadata(project_id, ud):
        raise HTTPException(404, "Proyecto no encontrado")
    try:
        info = update_file_meta(project_id, "docs", filename, body.model_dump(exclude_none=True), ud)
    except ValueError as e:
        raise HTTPException(404, str(e))
    return info


@router.patch("/{project_id}/translates/{filename}/meta")
async def patch_xlan_meta(request: Request, project_id: str, filename: str, body: FileMetaUpdate):
    ud = request.state.user_dir
    if not load_metadata(project_id, ud):
        raise HTTPException(404, "Proyecto no encontrado")
    try:
        info = update_file_meta(project_id, "translates", filename, body.model_dump(exclude_none=True), ud)
    except ValueError as e:
        raise HTTPException(404, str(e))
    return info


@router.put("/{project_id}/docs/tags")
async def put_docs_tags(request: Request, project_id: str, body: dict):
    ud = request.state.user_dir
    if not load_metadata(project_id, ud):
        raise HTTPException(404, "Proyecto no encontrado")
    return {"available_tags": update_available_tags(project_id, "docs", body.get("available_tags", []), ud)}


@router.put("/{project_id}/translates/tags")
async def put_translates_tags(request: Request, project_id: str, body: dict):
    ud = request.state.user_dir
    if not load_metadata(project_id, ud):
        raise HTTPException(404, "Proyecto no encontrado")
    return {"available_tags": update_available_tags(project_id, "translates", body.get("available_tags", []), ud)}


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
async def put_block_note(request: Request, project_id: str, filename: str, block_index: int, body: BlockNoteBody):
    ud = request.state.user_dir
    if not load_metadata(project_id, ud):
        raise HTTPException(404, "Proyecto no encontrado")
    try:
        note = update_block_note(project_id, filename, block_index, body.model_dump(), ud)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return note


@router.delete("/{project_id}/translates/{filename}/note/{block_index}")
async def delete_block_note(request: Request, project_id: str, filename: str, block_index: int):
    ud = request.state.user_dir
    if not load_metadata(project_id, ud):
        raise HTTPException(404, "Proyecto no encontrado")
    try:
        update_block_note(project_id, filename, block_index, None, ud)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


# ── Segment annotations ──────────────────────────────────────────

@router.put("/{project_id}/translates/{filename}/note/{block_index}/seg/{seg_id}")
async def put_segment_note(request: Request, project_id: str, filename: str, block_index: int, seg_id: str, body: BlockNoteBody):
    ud = request.state.user_dir
    if not load_metadata(project_id, ud):
        raise HTTPException(404, "Proyecto no encontrado")
    try:
        note = update_segment_note(project_id, filename, block_index, seg_id, body.model_dump(), ud)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return note


@router.delete("/{project_id}/translates/{filename}/note/{block_index}/seg/{seg_id}")
async def delete_segment_note(request: Request, project_id: str, filename: str, block_index: int, seg_id: str):
    ud = request.state.user_dir
    if not load_metadata(project_id, ud):
        raise HTTPException(404, "Proyecto no encontrado")
    try:
        update_segment_note(project_id, filename, block_index, seg_id, None, ud)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


@router.get("/{project_id}/translates/{filename}/parts")
async def get_xlan_parts(request: Request, project_id: str, filename: str):
    """Return the list of part files for a multi-part xlan."""
    ud = request.state.user_dir
    if not load_metadata(project_id, ud):
        raise HTTPException(404, "Proyecto no encontrado")
    meta = list_translates(project_id, ud)
    entry = meta.get("files", {}).get(filename, {})
    parts = entry.get("parts", [filename])
    return {"parts": parts}


@router.get("/{project_id}/translates/{filename}/content")
async def get_xlan_content(request: Request, project_id: str, filename: str):
    """Return just the content array of an xlan file (for lazy-loading parts)."""
    ud = request.state.user_dir
    xlan = load_xlan(project_id, filename, ud)
    if not xlan:
        raise HTTPException(404, "Archivo no encontrado")
    return {"content": xlan.get("content", []), "meta": xlan.get("meta", {})}


# ── Line break editing ────────────────────────────────────────────

class LineBreakChange(BaseModel):
    block_index: int
    seg_id: str
    newline_count: int

class LineBreakBody(BaseModel):
    changes: List[LineBreakChange]

@router.put("/{project_id}/translates/{filename}/linebreaks")
async def put_linebreaks(request: Request, project_id: str, filename: str, body: LineBreakBody):
    ud = request.state.user_dir
    if not load_metadata(project_id, ud):
        raise HTTPException(404, "Proyecto no encontrado")
    try:
        update_linebreaks(project_id, filename, [c.model_dump() for c in body.changes], ud)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


# ── XLAN direct upload ──────────────────────────────────────────────

@router.post("/{project_id}/translates/upload-xlan")
async def upload_xlan_file(
    request: Request,
    project_id: str,
    file: UploadFile = File(...),
    display_name: str = Form(""),
    description: str = Form(""),
):
    ud = request.state.user_dir
    if not load_metadata(project_id, ud):
        raise HTTPException(404, "Proyecto no encontrado")

    content = await file.read()
    try:
        data = json.loads(content)
    except (json.JSONDecodeError, ValueError) as e:
        raise HTTPException(400, f"JSON inválido: {e}")

    if not isinstance(data.get("meta"), dict):
        raise HTTPException(400, 'Falta el campo "meta"')
    if not isinstance(data.get("content"), list):
        raise HTTPException(400, 'Falta el campo "content" (debe ser un array)')

    # Use original filename, sanitized
    import re
    raw_name = file.filename or "upload.xlan"
    if not raw_name.endswith(".xlan"):
        raw_name += ".xlan"
    safe_name = re.sub(r'[^\w.\-]', '_', raw_name)

    # Check for duplicates
    existing = list_translates(project_id, ud)
    if safe_name in existing.get("files", {}):
        base = safe_name.rsplit('.', 1)[0]
        i = 2
        while f"{base}_{i}.xlan" in existing.get("files", {}):
            i += 1
        safe_name = f"{base}_{i}.xlan"

    save_xlan(project_id, safe_name, data, ud)
    register_xlan_in_metadata(
        project_id, safe_name,
        display_name=display_name or data.get("meta", {}).get("title", safe_name),
        description=description or data.get("meta", {}).get("description", ""),
        user_dir=ud,
    )
    return {"ok": True, "filename": safe_name}
