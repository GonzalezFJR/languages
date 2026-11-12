from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.auth import require_user
from app.services.project_service import (
    create_project, list_projects, load_metadata, delete_project, save_metadata, SUPPORTED_LANGUAGES,
)

router = APIRouter(prefix="/api/projects", tags=["projects"])


class CreateProjectRequest(BaseModel):
    name: str
    base: str = "es"
    target: str = "en"


class UpdateProjectRequest(BaseModel):
    name: str | None = None
    base: str | None = None
    target: str | None = None


def _check_langs(base: str | None, target: str | None):
    for code in (base, target):
        if code is not None and code not in SUPPORTED_LANGUAGES:
            raise HTTPException(400, f"Idioma no soportado: {code}")
    if base and target and base == target:
        raise HTTPException(400, "El idioma de estudio y tu idioma deben ser distintos")


@router.get("")
async def get_projects(request: Request):
    return list_projects(request.state.user_dir)


@router.post("")
async def post_create_project(body: CreateProjectRequest, user_dir: str = Depends(require_user)):
    _check_langs(body.base, body.target)
    try:
        return create_project(body.name, body.base, body.target, user_dir)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/{project_id}")
async def get_project(request: Request, project_id: str):
    meta = load_metadata(project_id, request.state.user_dir)
    if not meta:
        raise HTTPException(404, "Proyecto no encontrado")
    return meta


@router.patch("/{project_id}")
async def patch_project(project_id: str, body: UpdateProjectRequest, user_dir: str = Depends(require_user)):
    meta = load_metadata(project_id, user_dir)
    if not meta:
        raise HTTPException(404, "Proyecto no encontrado")
    _check_langs(body.base or meta["base"], body.target or meta["target"])
    if body.name and body.name.strip():
        meta["name"] = body.name.strip()
    if body.base:
        meta["base"] = body.base
    if body.target:
        meta["target"] = body.target
    save_metadata(project_id, meta, user_dir)
    return meta


@router.delete("/{project_id}")
async def delete_project_endpoint(project_id: str, user_dir: str = Depends(require_user)):
    try:
        delete_project(project_id, user_dir)
    except ValueError as e:
        raise HTTPException(404, str(e))
    return {"ok": True}
