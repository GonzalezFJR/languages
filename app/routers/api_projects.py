from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.services.project_service import (
    create_project,
    list_projects,
    load_metadata,
    delete_project,
    save_metadata,
    SUPPORTED_LANGUAGES,
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


@router.get("")
async def get_projects(request: Request):
    return list_projects(request.state.user_dir)


@router.post("")
async def post_create_project(request: Request, body: CreateProjectRequest):
    if body.base not in SUPPORTED_LANGUAGES:
        raise HTTPException(400, f"Idioma base no soportado: {body.base}")
    if body.target not in SUPPORTED_LANGUAGES:
        raise HTTPException(400, f"Idioma objetivo no soportado: {body.target}")
    if body.base == body.target:
        raise HTTPException(400, "El idioma base y el objetivo deben ser distintos")
    try:
        meta = create_project(body.name, body.base, body.target, request.state.user_dir)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return meta


@router.get("/{project_id}")
async def get_project(request: Request, project_id: str):
    meta = load_metadata(project_id, request.state.user_dir)
    if not meta:
        raise HTTPException(404, "Proyecto no encontrado")
    return meta


@router.patch("/{project_id}")
async def patch_project(request: Request, project_id: str, body: UpdateProjectRequest):
    user_dir = request.state.user_dir
    meta = load_metadata(project_id, user_dir)
    if not meta:
        raise HTTPException(404, "Proyecto no encontrado")
    if body.name:
        meta["name"] = body.name
    if body.base:
        if body.base not in SUPPORTED_LANGUAGES:
            raise HTTPException(400, f"Idioma no soportado: {body.base}")
        meta["base"] = body.base
    if body.target:
        if body.target not in SUPPORTED_LANGUAGES:
            raise HTTPException(400, f"Idioma no soportado: {body.target}")
        meta["target"] = body.target
    save_metadata(project_id, meta, user_dir)
    return meta


@router.delete("/{project_id}")
async def delete_project_endpoint(request: Request, project_id: str):
    try:
        delete_project(project_id, request.state.user_dir)
    except ValueError as e:
        raise HTTPException(404, str(e))
    return {"ok": True}
