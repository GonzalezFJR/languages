import json
import shutil
from pathlib import Path
from datetime import datetime
from typing import Optional

from app.config import settings

SUPPORTED_LANGUAGES = {
    "es": "Español",
    "en": "English",
    "de": "Deutsch",
    "fr": "Français",
    "it": "Italiano",
    "pt": "Português",
    "ru": "Русский",
    "zh": "中文",
    "ja": "日本語",
    "ar": "العربية",
}


def get_project_path(project_id: str, user_dir: str = "public") -> Path:
    return settings.contents_path / user_dir / project_id


def load_metadata(project_id: str, user_dir: str = "public") -> Optional[dict]:
    meta_path = get_project_path(project_id, user_dir) / "metadata.json"
    if not meta_path.exists():
        return None
    with open(meta_path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_metadata(project_id: str, data: dict, user_dir: str = "public"):
    meta_path = get_project_path(project_id, user_dir) / "metadata.json"
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def list_projects(user_dir: str = "public") -> list[dict]:
    base = settings.contents_path / user_dir
    if not base.exists():
        base.mkdir(parents=True, exist_ok=True)
        return []
    projects = []
    for folder in sorted(base.iterdir()):
        if folder.is_dir():
            meta = load_metadata(folder.name, user_dir)
            if meta:
                doc_count = _count_files(folder / "docs")
                xlan_count = _count_files(folder / "translates", ext=".xlan")
                meta["doc_count"] = doc_count
                meta["xlan_count"] = xlan_count
                projects.append(meta)
    return projects


def _count_files(path: Path, ext: Optional[str] = None) -> int:
    if not path.exists():
        return 0
    if ext:
        return len([f for f in path.iterdir() if f.is_file() and f.suffix == ext])
    return len([f for f in path.iterdir() if f.is_file()])


def create_project(name: str, base: str, target: str, user_dir: str = "public") -> dict:
    slug = _slugify(name)
    project_path = settings.contents_path / user_dir / slug

    if project_path.exists():
        raise ValueError(f"Ya existe un proyecto con ese nombre: '{slug}'")

    project_path.mkdir(parents=True, exist_ok=True)
    (project_path / "docs").mkdir(exist_ok=True)
    (project_path / "translates").mkdir(exist_ok=True)

    docs_meta = {"categories": [], "files": {}}
    translates_meta = {"categories": [], "files": {}}

    with open(project_path / "docs" / "metadata.json", "w", encoding="utf-8") as f:
        json.dump(docs_meta, f, ensure_ascii=False, indent=2)
    with open(project_path / "translates" / "metadata.json", "w", encoding="utf-8") as f:
        json.dump(translates_meta, f, ensure_ascii=False, indent=2)

    metadata = {
        "id": slug,
        "name": name,
        "base": base,
        "target": target,
        "created_at": datetime.utcnow().isoformat(),
    }
    save_metadata(slug, metadata, user_dir)
    return metadata


def delete_project(project_id: str, user_dir: str = "public"):
    project_path = get_project_path(project_id, user_dir)
    if not project_path.exists():
        raise ValueError(f"Proyecto no encontrado: '{project_id}'")
    shutil.rmtree(project_path)


def _slugify(text: str) -> str:
    import re
    text = text.lower().strip()
    text = re.sub(r"[áàâä]", "a", text)
    text = re.sub(r"[éèêë]", "e", text)
    text = re.sub(r"[íìîï]", "i", text)
    text = re.sub(r"[óòôö]", "o", text)
    text = re.sub(r"[úùûü]", "u", text)
    text = re.sub(r"[ñ]", "n", text)
    text = re.sub(r"[^a-z0-9]+", "_", text)
    text = text.strip("_")
    return text
