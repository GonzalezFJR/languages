import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from app.config import settings
from app.lextor import languages
from app.services.storage import LOCK, read_json, write_json, slugify, safe_name

# code -> display name, used by templates and API validation
SUPPORTED_LANGUAGES = {l["code"]: l["name"] for l in languages.ui_list()}

TEXT_EXT = ".lextor"


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def get_project_path(project_id: str, user_dir: str = "public") -> Path:
    return settings.contents_path / safe_name(user_dir) / safe_name(project_id)


def load_metadata(project_id: str, user_dir: str = "public") -> Optional[dict]:
    try:
        meta_path = get_project_path(project_id, user_dir) / "metadata.json"
    except ValueError:
        return None
    return read_json(meta_path)


def save_metadata(project_id: str, data: dict, user_dir: str = "public"):
    write_json(get_project_path(project_id, user_dir) / "metadata.json", data)


def list_projects(user_dir: str = "public") -> list[dict]:
    base = settings.contents_path / user_dir
    if not base.exists():
        base.mkdir(parents=True, exist_ok=True)
        return []
    projects = []
    for folder in sorted(base.iterdir()):
        if folder.is_dir() and not folder.name.startswith(("_", ".")):
            meta = load_metadata(folder.name, user_dir)
            if meta:
                meta = dict(meta)
                meta["doc_count"] = _count_files(folder / "docs")
                meta["text_count"] = _count_files(folder / "translates", ext=TEXT_EXT)
                meta["xlan_count"] = meta["text_count"]  # backwards compat
                projects.append(meta)
    projects.sort(key=lambda p: p.get("updated_at") or p.get("created_at") or "", reverse=True)
    return projects


def _count_files(path: Path, ext: Optional[str] = None) -> int:
    if not path.exists():
        return 0
    files = [f for f in path.iterdir() if f.is_file() and f.name != "metadata.json" and not f.name.startswith(".")]
    if ext:
        files = [f for f in files if f.suffix == ext]
    return len(files)


def find_project_for_pair(text_lang: str, notes_lang: str, user_dir: str) -> Optional[dict]:
    for p in list_projects(user_dir):
        if p.get("target") == text_lang and p.get("base") == notes_lang:
            return p
    return None


def create_project(name: str, base: str, target: str, user_dir: str = "public") -> dict:
    """base = learner's language (notes), target = language being studied (texts)."""
    name = (name or "").strip()
    if not name:
        raise ValueError("El nombre del proyecto es obligatorio")
    with LOCK:
        slug = slugify(name, "proyecto")
        project_path = settings.contents_path / user_dir / slug
        i = 2
        while project_path.exists():
            slug_i = f"{slug}_{i}"
            project_path = settings.contents_path / user_dir / slug_i
            i += 1
        slug = project_path.name
        project_path.mkdir(parents=True, exist_ok=True)
        (project_path / "docs").mkdir(exist_ok=True)
        (project_path / "translates").mkdir(exist_ok=True)
        empty = {"categories": [], "files": {}, "available_tags": []}
        write_json(project_path / "docs" / "metadata.json", empty)
        write_json(project_path / "translates" / "metadata.json", empty)
        metadata = {
            "id": slug,
            "name": name,
            "base": base,
            "target": target,
            "created_at": now_iso(),
        }
        save_metadata(slug, metadata, user_dir)
    return metadata


def touch_project(project_id: str, user_dir: str) -> None:
    meta = load_metadata(project_id, user_dir)
    if meta:
        meta["updated_at"] = now_iso()
        save_metadata(project_id, meta, user_dir)


def delete_project(project_id: str, user_dir: str = "public"):
    project_path = get_project_path(project_id, user_dir)
    if not project_path.exists():
        raise ValueError(f"Proyecto no encontrado: '{project_id}'")
    shutil.rmtree(project_path)
