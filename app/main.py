from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from pathlib import Path

from app.config import settings
from app.auth import get_user_content_dir, get_current_user
from app.routers import pages, api_projects, api_documents, api_pipeline, api_pipeline_agent, api_ocr

app = FastAPI(
    title=settings.app_name,
    debug=settings.debug,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class UserScopeMiddleware(BaseHTTPMiddleware):
    """Injects user_dir, current_user and base_path into request.state."""
    async def dispatch(self, request: Request, call_next):
        request.state.user_dir = get_user_content_dir(request)
        request.state.current_user = get_current_user(request)
        request.state.base_path = request.scope.get("root_path", "")
        response = await call_next(request)
        return response


app.add_middleware(UserScopeMiddleware)

Path("static/contents/public").mkdir(parents=True, exist_ok=True)
Path("static/contents/admin").mkdir(parents=True, exist_ok=True)

app.mount("/static", StaticFiles(directory="static"), name="static")

app.include_router(pages.router)
app.include_router(api_projects.router)
app.include_router(api_documents.router)
app.include_router(api_pipeline.router)
app.include_router(api_pipeline_agent.router)
app.include_router(api_ocr.router)
