from fastapi import APIRouter, Request, HTTPException, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.services.project_service import list_projects, load_metadata, SUPPORTED_LANGUAGES
from app.services.document_service import list_docs, list_translates
from app.services.xlan_service import load_xlan
from app.auth import verify_credentials, create_session_token, COOKIE_NAME

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/", response_class=HTMLResponse)
async def landing(request: Request):
    return templates.TemplateResponse("landing.html", {
        "request": request,
        "current_user": request.state.current_user,
    })


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if request.state.current_user:
        return RedirectResponse("/home", status_code=302)
    return templates.TemplateResponse("login.html", {"request": request, "error": None})


@router.post("/login")
async def login_submit(request: Request, username: str = Form(...), password: str = Form(...)):
    if verify_credentials(username, password):
        response = RedirectResponse("/home", status_code=302)
        token = create_session_token(username)
        response.set_cookie(COOKIE_NAME, token, max_age=60*60*24*30, httponly=True, samesite="lax")
        return response
    return templates.TemplateResponse("login.html", {
        "request": request,
        "error": "Usuario o contraseña incorrectos.",
    })


@router.get("/logout")
async def logout(request: Request):
    response = RedirectResponse("/", status_code=302)
    response.delete_cookie(COOKIE_NAME)
    return response


@router.get("/home", response_class=HTMLResponse)
async def home(request: Request):
    user_dir = request.state.user_dir
    projects = list_projects(user_dir)
    return templates.TemplateResponse("home.html", {
        "request": request,
        "projects": projects,
        "languages": SUPPORTED_LANGUAGES,
        "current_user": request.state.current_user,
    })


@router.get("/project/{project_id}", response_class=HTMLResponse)
async def project_view(request: Request, project_id: str):
    user_dir = request.state.user_dir
    meta = load_metadata(project_id, user_dir)
    if not meta:
        raise HTTPException(status_code=404, detail="Proyecto no encontrado")

    docs_meta = list_docs(project_id, user_dir)
    translates_meta = list_translates(project_id, user_dir)

    return templates.TemplateResponse("project.html", {
        "request": request,
        "project": meta,
        "docs_meta": docs_meta,
        "translates_meta": translates_meta,
        "languages": SUPPORTED_LANGUAGES,
        "current_user": request.state.current_user,
    })


@router.get("/viewer/{project_id}/doc/{filename:path}", response_class=HTMLResponse)
async def view_doc(request: Request, project_id: str, filename: str):
    user_dir = request.state.user_dir
    meta = load_metadata(project_id, user_dir)
    if not meta:
        raise HTTPException(status_code=404, detail="Proyecto no encontrado")

    docs_meta = list_docs(project_id, user_dir)
    translates_meta = list_translates(project_id, user_dir)

    file_info = docs_meta.get("files", {}).get(filename)
    if not file_info:
        raise HTTPException(status_code=404, detail="Documento no encontrado")

    doc_content = ""
    if filename.lower().endswith((".txt", ".md")):
        from app.services.document_service import get_doc_path
        p = get_doc_path(project_id, filename, user_dir)
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
        "current_user": request.state.current_user,
    })


@router.get("/viewer/{project_id}/xlan/{filename:path}", response_class=HTMLResponse)
async def view_xlan(request: Request, project_id: str, filename: str):
    user_dir = request.state.user_dir
    meta = load_metadata(project_id, user_dir)
    if not meta:
        raise HTTPException(status_code=404, detail="Proyecto no encontrado")

    docs_meta = list_docs(project_id, user_dir)
    translates_meta = list_translates(project_id, user_dir)

    xlan_data = load_xlan(project_id, filename, user_dir)
    if not xlan_data:
        raise HTTPException(status_code=404, detail="Archivo no encontrado")

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
        "current_user": request.state.current_user,
    })
