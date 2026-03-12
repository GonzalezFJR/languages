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
    get_doc_path,
    get_translate_path,
)

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
