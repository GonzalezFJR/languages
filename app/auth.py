"""Simple cookie-based authentication for admin user."""

from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from fastapi import Request
from typing import Optional

from app.config import settings

_serializer = URLSafeTimedSerializer(settings.secret_key)

COOKIE_NAME = "lextor_session"
NAUX_COOKIE_NAME = "naux_session"
MAX_AGE = 60 * 60 * 24 * 30  # 30 days


def verify_credentials(username: str, password: str) -> bool:
    return username == settings.admin_user and password == settings.admin_password


def create_session_token(username: str) -> str:
    return _serializer.dumps({"user": username})


def get_current_user(request: Request) -> Optional[str]:
    """Return the username from session cookie (lextor or naux), or None."""
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
    """Return the content subdirectory for the current user.
    Logged-in admin  → 'admin'
    Anonymous        → 'public'
    """
    user = get_current_user(request)
    if user == settings.admin_user:
        return "admin"
    return "public"
