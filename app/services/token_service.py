"""Personal MCP access tokens. A token maps an MCP connection to a user's content space.

Tokens are long random strings embedded in the connector URL (/mcp/u/<token>) or sent as
`Authorization: Bearer <token>`. They are stored in contents/_system/tokens.json, which is never
served over HTTP.
"""

from __future__ import annotations

import secrets
from typing import Optional

from app.services.project_service import now_iso
from app.services.storage import LOCK, read_json, write_json, system_dir


def _path():
    return system_dir() / "tokens.json"


def _load() -> dict:
    return read_json(_path(), {}) or {}


def list_tokens(user_dir: str) -> list[dict]:
    return [{"token": t, **info} for t, info in _load().items() if info.get("user_dir") == user_dir]


def get_or_create(user_dir: str, label: str = "Personal") -> dict:
    existing = list_tokens(user_dir)
    if existing:
        return existing[0]
    return create(user_dir, label)


def create(user_dir: str, label: str = "Personal") -> dict:
    with LOCK:
        data = _load()
        token = "lx_" + secrets.token_urlsafe(24)
        data[token] = {"user_dir": user_dir, "label": label, "created_at": now_iso(), "last_used_at": None,
                       "last_client": None}
        write_json(_path(), data)
    return {"token": token, **data[token]}


def revoke(user_dir: str, token: str) -> bool:
    with LOCK:
        data = _load()
        if data.get(token, {}).get("user_dir") != user_dir:
            return False
        data.pop(token)
        write_json(_path(), data)
    return True


def resolve(token: Optional[str]) -> Optional[str]:
    """Return the user_dir for a token, or None."""
    if not token:
        return None
    info = _load().get(token)
    return info.get("user_dir") if info else None


def touch(token: str, client: Optional[str]) -> None:
    with LOCK:
        data = _load()
        if token in data:
            data[token]["last_used_at"] = now_iso()
            if client:
                data[token]["last_client"] = client[:80]
            write_json(_path(), data)
