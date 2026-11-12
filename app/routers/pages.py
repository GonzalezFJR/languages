from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

import json
from pathlib import Path

from app.icons import PATHS as ICON_PATHS, icon
from app.auth import verify_credentials, create_session_token, COOKIE_NAME, MAX_AGE
from app.lextor import languages
from app.lextor.guide import build_guide, example_document
from app.mcp.http import public_base_url
from app.mcp.tools import TOOLS
from app.services import text_service as ts
from app.services import token_service
from app.services.document_service import list_docs
from app.services.project_service import list_projects, load_metadata, SUPPORTED_LANGUAGES

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")
templates.env.globals["lang_name"] = languages.name
templates.env.globals["lang_native"] = lambda c: languages.get(c).native
templates.env.globals["icon"] = icon
templates.env.globals["icons_json"] = json.dumps(ICON_PATHS)
templates.env.globals["lang_rtl"] = lambda c: languages.get(c).rtl


def _ctx(request: Request, **kw) -> dict:
    return {"request": request, "current_user": request.state.current_user,
            "bp": request.state.base_path or "", "languages": SUPPORTED_LANGUAGES,
            "lang_list": languages.ui_list(), **kw}


def render(request: Request, name: str, ctx: dict, status_code: int = 200):
    return templates.TemplateResponse(request, name, ctx, status_code=status_code)


def _redirect(request: Request, path: str) -> RedirectResponse:
    # Absolute URL: a reverse proxy with `proxy_redirect / /prefix/;` would otherwise prefix it twice.
    return RedirectResponse(public_base_url(request) + path, status_code=302)


@router.get("/", response_class=HTMLResponse)
async def landing(request: Request):
    ja = json.loads((Path(__file__).resolve().parent.parent / "lextor" / "examples" / "ja-es.json").read_text("utf-8"))
    return render(request, "landing.html", _ctx(request, demo_docs={"fr": example_document(), "ja": ja}))


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, next: str = ""):
    if request.state.current_user:
        return _redirect(request, next if next.startswith("/") else "/library")
    return render(request, "login.html", _ctx(request, error=None, next=next))


@router.post("/login")
async def login_submit(request: Request, username: str = Form(...), password: str = Form(...), next: str = Form("")):
    if verify_credentials(username, password):
        response = _redirect(request, next if next.startswith("/") and not next.startswith("//") else "/library")
        response.set_cookie(COOKIE_NAME, create_session_token(username), max_age=MAX_AGE, httponly=True,
                            samesite="lax", secure=request.url.scheme == "https")
        return response
    return render(request, "login.html", _ctx(request, error="Usuario o contraseña incorrectos.",
                                                         next=next), status_code=401)


@router.get("/logout")
async def logout(request: Request):
    response = _redirect(request, "/")
    response.delete_cookie(COOKIE_NAME)
    return response


@router.get("/home")
async def home_legacy(request: Request):
    return _redirect(request, "/library")


@router.get("/library", response_class=HTMLResponse)
async def library(request: Request):
    projects = list_projects(request.state.user_dir)
    return render(request, "library.html", _ctx(request, projects=projects))


@router.get("/project/{project_id}", response_class=HTMLResponse)
async def project_view(request: Request, project_id: str):
    ud = request.state.user_dir
    meta = load_metadata(project_id, ud)
    if not meta:
        raise HTTPException(status_code=404, detail="Proyecto no encontrado")
    return render(request, "project.html", _ctx(
        request, project=meta, texts_meta=ts.list_texts(project_id, ud), docs_meta=list_docs(project_id, ud)))


@router.get("/read/{project_id}/{filename}", response_class=HTMLResponse)
async def read_text(request: Request, project_id: str, filename: str):
    ud = request.state.user_dir
    meta = load_metadata(project_id, ud)
    doc = ts.load_text(project_id, filename, ud) if meta else None
    if not doc:
        raise HTTPException(status_code=404, detail="Texto no encontrado")
    tmeta = ts.list_texts(project_id, ud)
    entry = tmeta["files"].get(filename, {})
    siblings = [{"file": f, "title": e.get("display_name", f)} for f, e in tmeta["files"].items()]
    return render(request, "reader.html", _ctx(
        request, project=meta, doc=doc, filename=filename, entry=entry, siblings=siblings,
        editable=bool(request.state.current_user), share_id=entry.get("share_id"), shared=False))


@router.get("/t/{share_id}", response_class=HTMLResponse)
async def shared_text(request: Request, share_id: str):
    found = ts.load_shared(share_id)
    if not found:
        raise HTTPException(status_code=404, detail="Texto no encontrado")
    doc, entry = found
    return render(request, "reader.html", _ctx(
        request, project=None, doc=doc, filename=None, entry={}, siblings=[], editable=False,
        share_id=share_id, shared=True))


@router.get("/embed/{share_id}", response_class=HTMLResponse)
async def embed_text(request: Request, share_id: str):
    found = ts.load_shared(share_id)
    if not found:
        raise HTTPException(status_code=404, detail="Texto no encontrado")
    resp = render(request, "embed.html", _ctx(request, doc=found[0], share_id=share_id))
    resp.headers["Content-Security-Policy"] = "frame-ancestors *"
    return resp


@router.get("/doc/{project_id}/{filename}", response_class=HTMLResponse)
async def view_doc(request: Request, project_id: str, filename: str):
    ud = request.state.user_dir
    meta = load_metadata(project_id, ud)
    if not meta:
        raise HTTPException(status_code=404, detail="Proyecto no encontrado")
    info = list_docs(project_id, ud)["files"].get(filename)
    if not info:
        raise HTTPException(status_code=404, detail="Documento no encontrado")
    content = ""
    if filename.lower().endswith((".txt", ".md")):
        from app.services.document_service import get_doc_path
        p = get_doc_path(project_id, filename, ud)
        if p:
            content = p.read_text(encoding="utf-8", errors="replace")
    return render(request, "doc.html", _ctx(request, project=meta, filename=filename, info=info,
                                                       content=content))


# Legacy URLs from the previous version
@router.get("/viewer/{project_id}/xlan/{filename}")
async def legacy_viewer_xlan(request: Request, project_id: str, filename: str):
    if filename.endswith(".xlan"):
        filename = filename[:-5] + ".lextor"
    return _redirect(request, f"/read/{project_id}/{filename}")


@router.get("/viewer/{project_id}/doc/{filename}")
async def legacy_viewer_doc(request: Request, project_id: str, filename: str):
    return _redirect(request, f"/doc/{project_id}/{filename}")


@router.get("/connect", response_class=HTMLResponse)
async def connect(request: Request):
    base = public_base_url(request)
    personal = None
    if request.state.current_user and request.state.user_dir != "public":
        tokens = token_service.list_tokens(request.state.user_dir)
        personal = tokens[0] if tokens else token_service.create(request.state.user_dir)
    return render(request, "connect.html", _ctx(
        request, base_url=base, token=None, personal=personal, tools=TOOLS,
        mcp_url=f"{base}/mcp", anon_url=f"{base}/mcp/public"))


@router.get("/format", response_class=HTMLResponse)
async def format_page(request: Request):
    return render(request, "format.html", _ctx(
        request, guide_md=build_guide(), example_json=json.dumps(example_document(), ensure_ascii=False, indent=1)))
