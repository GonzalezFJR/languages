from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.services.project_service import list_projects, load_metadata, SUPPORTED_LANGUAGES
from app.services.document_service import list_docs, list_translates
from app.services.xlan_service import load_xlan

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/", response_class=HTMLResponse)
async def landing(request: Request):
    return templates.TemplateResponse("landing.html", {"request": request})


@router.get("/home", response_class=HTMLResponse)
async def home(request: Request):
    projects = list_projects()
    return templates.TemplateResponse("home.html", {
        "request": request,
        "projects": projects,
        "languages": SUPPORTED_LANGUAGES,
    })


@router.get("/project/{project_id}", response_class=HTMLResponse)
async def project_view(request: Request, project_id: str):
    meta = load_metadata(project_id)
    if not meta:
        raise HTTPException(status_code=404, detail="Proyecto no encontrado")

    docs_meta = list_docs(project_id)
    translates_meta = list_translates(project_id)

    return templates.TemplateResponse("project.html", {
        "request": request,
        "project": meta,
        "docs_meta": docs_meta,
        "translates_meta": translates_meta,
        "languages": SUPPORTED_LANGUAGES,
    })


@router.get("/viewer/{project_id}/doc/{filename:path}", response_class=HTMLResponse)
async def view_doc(request: Request, project_id: str, filename: str):
    meta = load_metadata(project_id)
    if not meta:
        raise HTTPException(status_code=404, detail="Proyecto no encontrado")

    docs_meta = list_docs(project_id)
    translates_meta = list_translates(project_id)

    file_info = docs_meta.get("files", {}).get(filename)
    if not file_info:
        raise HTTPException(status_code=404, detail="Documento no encontrado")

    doc_content = ""
    if filename.lower().endswith((".txt", ".md")):
        from app.services.document_service import get_doc_path
        p = get_doc_path(project_id, filename)
        if p:
            with open(p, "r", encoding="utf-8", errors="replace") as f:
                doc_content = f.read()

    return templates.TemplateResponse("viewer.html", {
        "request": request,
        "project": meta,
        "docs_meta": docs_meta,
        "translates_meta": translates_meta,
        "languages": SUPPORTED_LANGUAGES,
        "view_type": "doc",
        "filename": filename,
        "file_info": file_info,
        "doc_content": doc_content,
        "xlan_data": None,
    })


@router.get("/viewer/{project_id}/xlan/{filename:path}", response_class=HTMLResponse)
async def view_xlan(request: Request, project_id: str, filename: str):
    meta = load_metadata(project_id)
    if not meta:
        raise HTTPException(status_code=404, detail="Proyecto no encontrado")

    docs_meta = list_docs(project_id)
    translates_meta = list_translates(project_id)

    xlan_data = load_xlan(project_id, filename)
    if not xlan_data:
        raise HTTPException(status_code=404, detail="Archivo .xlan no encontrado")

    file_info = translates_meta.get("files", {}).get(filename, {})

    return templates.TemplateResponse("viewer.html", {
        "request": request,
        "project": meta,
        "docs_meta": docs_meta,
        "translates_meta": translates_meta,
        "languages": SUPPORTED_LANGUAGES,
        "view_type": "xlan",
        "filename": filename,
        "file_info": file_info,
        "doc_content": "",
        "xlan_data": xlan_data,
    })
