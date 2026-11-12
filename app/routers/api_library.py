"""REST API for texts, reference documents, annotations, validation and MCP tokens."""

from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from app.auth import require_user
from app.lextor import document as lx
from app.services import text_service as ts
from app.services import token_service
from app.services.document_service import (
    list_docs, save_uploaded_doc, delete_doc, update_file_meta, update_available_tags, update_categories,
    get_doc_path,
)
from app.services.project_service import load_metadata

router = APIRouter(prefix="/api", tags=["library"])


def _project(project_id: str, user_dir: str) -> dict:
    meta = load_metadata(project_id, user_dir)
    if not meta:
        raise HTTPException(404, "Proyecto no encontrado")
    return meta


class FileMetaUpdate(BaseModel):
    display_name: Optional[str] = None
    description: Optional[str] = None
    tags: Optional[list] = None


class TagsBody(BaseModel):
    available_tags: List[str]


class CategoriesBody(BaseModel):
    categories: list


class AnnotationBody(BaseModel):
    color: Optional[str] = None
    comment: Optional[str] = None


class BlockTypeBody(BaseModel):
    type: str
    level: int = 1


# ── texts ────────────────────────────────────────────────────────────────

@router.get("/projects/{project_id}/texts")
async def get_texts(request: Request, project_id: str):
    _project(project_id, request.state.user_dir)
    return ts.list_texts(project_id, request.state.user_dir)


@router.get("/projects/{project_id}/texts/{filename}")
async def get_text(request: Request, project_id: str, filename: str, download: bool = False):
    _project(project_id, request.state.user_dir)
    doc = ts.load_text(project_id, filename, request.state.user_dir)
    if not doc:
        raise HTTPException(404, "Texto no encontrado")
    headers = {"Content-Disposition": f'attachment; filename="{filename}"'} if download else {}
    return JSONResponse(doc, headers=headers)


@router.post("/projects/{project_id}/texts/import")
async def import_text(project_id: str, file: UploadFile = File(...), display_name: str = Form(""),
                      user_dir: str = Depends(require_user)):
    _project(project_id, user_dir)
    raw = await file.read()
    if len(raw) > 5 * 1024 * 1024:
        raise HTTPException(400, "Archivo demasiado grande (máx. 5 MB)")
    try:
        filename, entry, rep = ts.import_file(project_id, raw, user_dir, display_name)
    except ts.TextError as e:
        body = {"detail": str(e)}
        if e.report:
            body["report"] = e.report.to_dict()
        return JSONResponse(body, status_code=422)
    return {"ok": True, "filename": filename, "entry": entry, "report": rep.to_dict()}


@router.patch("/projects/{project_id}/texts/{filename}/meta")
async def patch_text_meta(project_id: str, filename: str, body: FileMetaUpdate, user_dir: str = Depends(require_user)):
    _project(project_id, user_dir)
    try:
        return update_file_meta(project_id, "translates", filename, body.model_dump(exclude_none=True), user_dir)
    except ValueError as e:
        raise HTTPException(404, str(e))


@router.delete("/projects/{project_id}/texts/{filename}")
async def delete_text(project_id: str, filename: str, user_dir: str = Depends(require_user)):
    _project(project_id, user_dir)
    try:
        ts.delete_text(project_id, filename, user_dir)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


@router.put("/projects/{project_id}/texts/{filename}/annotations/{chunk_id}")
async def put_annotation(project_id: str, filename: str, chunk_id: str, body: AnnotationBody,
                         user_dir: str = Depends(require_user)):
    _project(project_id, user_dir)
    try:
        return ts.set_annotation(project_id, filename, chunk_id, body.model_dump(exclude_none=True), user_dir)
    except ts.TextError as e:
        raise HTTPException(400, str(e))


@router.delete("/projects/{project_id}/texts/{filename}/annotations/{chunk_id}")
async def delete_annotation(project_id: str, filename: str, chunk_id: str, user_dir: str = Depends(require_user)):
    _project(project_id, user_dir)
    try:
        ts.set_annotation(project_id, filename, chunk_id, None, user_dir)
    except ts.TextError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


@router.put("/projects/{project_id}/texts/{filename}/blocks/{block_index}")
async def put_block_type(project_id: str, filename: str, block_index: int, body: BlockTypeBody,
                         user_dir: str = Depends(require_user)):
    _project(project_id, user_dir)
    try:
        ts.set_block_type(project_id, filename, block_index, body.type, body.level, user_dir)
    except ts.TextError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


# ── section organisation (texts + docs share the same metadata shape) ─────

def _section(kind: str) -> str:
    if kind not in ("texts", "docs"):
        raise HTTPException(404)
    return "translates" if kind == "texts" else "docs"


@router.put("/projects/{project_id}/{kind}/organize/tags")
async def put_tags(project_id: str, kind: str, body: TagsBody, user_dir: str = Depends(require_user)):
    _project(project_id, user_dir)
    return {"available_tags": update_available_tags(project_id, _section(kind), body.available_tags, user_dir)}


@router.put("/projects/{project_id}/{kind}/organize/categories")
async def put_categories(project_id: str, kind: str, body: CategoriesBody, user_dir: str = Depends(require_user)):
    _project(project_id, user_dir)
    update_categories(project_id, _section(kind), body.categories, user_dir)
    return {"ok": True}


# ── reference documents ──────────────────────────────────────────────────

@router.get("/projects/{project_id}/docs")
async def get_docs(request: Request, project_id: str):
    _project(project_id, request.state.user_dir)
    return list_docs(project_id, request.state.user_dir)


@router.post("/projects/{project_id}/docs")
async def upload_doc(project_id: str, file: UploadFile = File(...), display_name: str = Form(""),
                     description: str = Form(""), user_dir: str = Depends(require_user)):
    _project(project_id, user_dir)
    content = await file.read()
    try:
        return save_uploaded_doc(project_id, file.filename, content, display_name, description, user_dir=user_dir)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/projects/{project_id}/docs/{filename}/file")
async def serve_doc(request: Request, project_id: str, filename: str):
    path = get_doc_path(project_id, filename, request.state.user_dir)
    if not path:
        raise HTTPException(404, "Archivo no encontrado")
    return FileResponse(str(path))


@router.patch("/projects/{project_id}/docs/{filename}/meta")
async def patch_doc_meta(project_id: str, filename: str, body: FileMetaUpdate, user_dir: str = Depends(require_user)):
    _project(project_id, user_dir)
    try:
        return update_file_meta(project_id, "docs", filename, body.model_dump(exclude_none=True), user_dir)
    except ValueError as e:
        raise HTTPException(404, str(e))


@router.delete("/projects/{project_id}/docs/{filename}")
async def remove_doc(project_id: str, filename: str, user_dir: str = Depends(require_user)):
    _project(project_id, user_dir)
    try:
        delete_doc(project_id, filename, user_dir)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


# ── validation playground ────────────────────────────────────────────────

@router.post("/validate")
async def validate(request: Request):
    raw = await request.body()
    rep = lx.validate_document(raw)
    return rep.to_dict()


# ── MCP connector token ──────────────────────────────────────────────────

@router.get("/mcp/token")
async def get_token(user_dir: str = Depends(require_user)):
    return token_service.get_or_create(user_dir)


@router.post("/mcp/token/rotate")
async def rotate_token(user_dir: str = Depends(require_user)):
    for t in token_service.list_tokens(user_dir):
        token_service.revoke(user_dir, t["token"])
    return token_service.create(user_dir)
