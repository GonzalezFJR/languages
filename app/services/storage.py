"""Small helpers for safe JSON storage on the local filesystem."""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import unicodedata
from pathlib import Path
from typing import Any

from app.config import settings

LOCK = threading.RLock()


def contents_root() -> Path:
    return settings.contents_path


def system_dir() -> Path:
    p = contents_root() / "_system"
    p.mkdir(parents=True, exist_ok=True)
    return p


def read_json(path: Path, default: Any = None) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except json.JSONDecodeError:
        return default


def write_json(path: Path, data: Any) -> None:
    """Atomic write (temp file + rename) so a crash never leaves a half-written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        # mkstemp creates 0600 files: keep content readable (backups, git), secrets private.
        os.chmod(tmp, 0o600 if path.parent.name == "_system" else 0o644)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def slugify(text: str, fallback: str = "texto") -> str:
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^a-zA-Z0-9]+", "_", text.lower()).strip("_")
    return text[:60] or fallback


_SAFE_NAME = re.compile(r"^[\w.\-]+$")


def safe_name(name: str) -> str:
    """Reject path traversal in user-provided file / project names."""
    if not name or not _SAFE_NAME.match(name) or name.startswith("."):
        raise ValueError(f"Nombre no válido: {name!r}")
    return name
