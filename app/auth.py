"""Simple cookie-based authentication for the admin user."""

from typing import Optional

from fastapi import HTTPException, Request
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

from app.config import settings

_serializer = URLSafeTimedSerializer(settings.secret_key)

COOKIE_NAME = "lextor_session"
NAUX_COOKIE_NAME = "naux_session"
MAX_AGE = 60 * 60 * 24 * 30  # 30 days


def verify_credentials(username: str, password: str) -> bool:
    import hmac
    return (hmac.compare_digest(username, settings.admin_user)
            and hmac.compare_digest(password, settings.admin_password))


def create_session_token(username: str) -> str:
    return _serializer.dumps({"user": username})


def get_current_user(request: Request) -> Optional[str]:
    """Return the username from the session cookie (lextor or naux), or None."""
    for cookie_name in (COOKIE_NAME, NAUX_COOKIE_NAME):
        token = request.cookies.get(cookie_name)
        if not token:
            continue
        try:
            data = _serializer.loads(token, max_age=MAX_AGE)
            user = data.get("user")
            if user:
                return user
        except (BadSignature, SignatureExpired):
            continue
    return None


def get_user_content_dir(request: Request) -> str:
    """Logged-in admin → 'admin'; anonymous → 'public' (read-only demo space)."""
    user = get_current_user(request)
    if user == settings.admin_user:
        return "admin"
    return "public"


def require_user(request: Request) -> str:
    """Dependency for write endpoints: the public demo space is read-only."""
    if not request.state.current_user:
        raise HTTPException(401, "Inicia sesión para modificar contenido")
    return request.state.user_dir
