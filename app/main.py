import logging
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exception_handlers import http_exception_handler
from starlette.exceptions import HTTPException as StarletteHTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from app.auth import get_user_content_dir, get_current_user
from app.config import settings
from app.mcp import http as mcp_http
from app.routers import pages, api_projects, api_library
from app.services import text_service

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    for d in ("public", "admin", "_system", "_shared"):
        (settings.contents_path / d).mkdir(parents=True, exist_ok=True)
    n = text_service.migrate_legacy_all()
    text_service.ensure_share_ids()
    if n:
        logging.getLogger("lextor").info("Migrated %d legacy .xlan texts to lextor/1", n)
    yield


app = FastAPI(title=settings.app_name, debug=settings.debug, docs_url="/api/docs", redoc_url=None,
              lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Mcp-Session-Id"],
)


class SafeStaticFiles(StaticFiles):
    """Serves /static but never anything inside the user-content directory (which historically lives
    under static/contents). Checked on the resolved filesystem path, so prefixes, root paths and ../
    tricks can't reach it."""

    def lookup_path(self, path: str):
        full_path, stat_result = super().lookup_path(path)
        if full_path:
            contents = settings.contents_path.resolve()
            resolved = Path(full_path).resolve()
            if resolved == contents or contents in resolved.parents:
                return "", None
        return full_path, stat_result


class UserScopeMiddleware(BaseHTTPMiddleware):
    """Injects user_dir, current_user and base_path into request.state; blocks raw access to user data."""

    async def dispatch(self, request: Request, call_next):
        root = request.scope.get("root_path", "") or ""
        path = request.url.path
        if path.startswith(root):
            path = path[len(root):]
        # User content lives under static/contents for backwards compatibility: never serve it raw
        # (SafeStaticFiles enforces the same rule on resolved paths).
        if path.startswith("/static/contents"):
            return PlainTextResponse("Not found", status_code=404)
        request.state.user_dir = get_user_content_dir(request)
        request.state.current_user = get_current_user(request)
        request.state.base_path = request.scope.get("root_path", "")
        return await call_next(request)


app.add_middleware(UserScopeMiddleware)


@app.exception_handler(StarletteHTTPException)
async def http_error(request: Request, exc: StarletteHTTPException):
    path = request.url.path
    wants_html = "text/html" in request.headers.get("accept", "")
    if path.startswith(("/api", "/mcp", "/static")) or not wants_html:
        return await http_exception_handler(request, exc)
    try:
        return pages.render(request, "error.html", pages._ctx(
            request, status_code=exc.status_code, message=exc.detail if isinstance(exc.detail, str) else ""),
            status_code=exc.status_code)
    except Exception:  # never fail while rendering an error
        return await http_exception_handler(request, exc)

app.mount("/static", SafeStaticFiles(directory="static"), name="static")

app.include_router(mcp_http.router)
app.include_router(api_projects.router)
app.include_router(api_library.router)
app.include_router(pages.router)
