"""Simple cookie-based authentication for admin user."""

from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from fastapi import Request
from typing import Optional

from app.config import settings

_serializer = URLSafeTimedSerializer(settings.secret_key)

COOKIE_NAME = "lextor_session"
MAX_AGE = 60 * 60 * 24 * 30  # 30 days


def verify_credentials(username: str, password: str) -> bool:
    return username == settings.admin_user and password == settings.admin_password


def create_session_token(username: str) -> str:
    return _serializer.dumps({"user": username})


def get_current_user(request: Request) -> Optional[str]:
    """Return the username from the session cookie, or None if not logged in."""
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    try:
        data = _serializer.loads(token, max_age=MAX_AGE)
        return data.get("user")
    except (BadSignature, SignatureExpired):
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
