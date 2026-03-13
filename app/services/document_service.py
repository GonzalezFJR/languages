import json
import shutil
from pathlib import Path
from datetime import datetime
from typing import Optional

from app.config import settings
from app.services.project_service import get_project_path


ALLOWED_DOC_EXTENSIONS = {".pdf", ".txt", ".doc", ".docx", ".md"}


def load_section_metadata(project_id: str, section: str) -> dict:
    meta_path = get_project_path(project_id) / section / "metadata.json"
    if not meta_path.exists():
        return {"categories": [], "files": {}}
    with open(meta_path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_section_metadata(project_id: str, section: str, data: dict):
    meta_path = get_project_path(project_id) / section / "metadata.json"
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def list_docs(project_id: str) -> dict:
    return load_section_metadata(project_id, "docs")


def list_translates(project_id: str) -> dict:
    return load_section_metadata(project_id, "translates")


def update_file_meta(project_id: str, section: str, filename: str, updates: dict) -> dict:
    meta = load_section_metadata(project_id, section)
    if filename not in meta.get("files", {}):
        raise ValueError(f"Archivo no encontrado: {filename}")
    for key in ("display_name", "description", "tags"):
        if key in updates:
            meta["files"][filename][key] = updates[key]
    save_section_metadata(project_id, section, meta)
    return meta["files"][filename]


def update_available_tags(project_id: str, section: str, tags: list) -> list:
    meta = load_section_metadata(project_id, section)
    meta["available_tags"] = tags
    save_section_metadata(project_id, section, meta)
    return tags


def save_uploaded_doc(project_id: str, filename: str, content: bytes, display_name: str = "", description: str = "", tags: Optional[list] = None) -> dict:
    suffix = Path(filename).suffix.lower()
    if suffix not in ALLOWED_DOC_EXTENSIONS:
        raise ValueError(f"Extensión no permitida: {suffix}")

    docs_path = get_project_path(project_id) / "docs"
    dest = docs_path / filename

    safe_name = filename
    counter = 1
    while dest.exists():
        stem = Path(filename).stem
        safe_name = f"{stem}_{counter}{suffix}"
        dest = docs_path / safe_name
        counter += 1

    with open(dest, "wb") as f:
        f.write(content)

    meta = load_section_metadata(project_id, "docs")
    meta["files"][safe_name] = {
        "name": safe_name,
        "display_name": display_name or Path(safe_name).stem,
        "description": description,
        "tags": tags or [],
        "uploaded_at": datetime.utcnow().isoformat(),
    }
    save_section_metadata(project_id, "docs", meta)
    return meta["files"][safe_name]


def delete_doc(project_id: str, filename: str):
    docs_path = get_project_path(project_id) / "docs"
    target = docs_path / filename
    if target.exists():
        target.unlink()
    meta = load_section_metadata(project_id, "docs")
    meta["files"].pop(filename, None)
    meta["categories"] = _remove_file_from_categories(meta["categories"], filename)
    save_section_metadata(project_id, "docs", meta)


def delete_translate(project_id: str, filename: str):
    translates_path = get_project_path(project_id) / "translates"
    target = translates_path / filename
    if target.exists():
        target.unlink()
    meta = load_section_metadata(project_id, "translates")
    meta["files"].pop(filename, None)
    meta["categories"] = _remove_file_from_categories(meta["categories"], filename)
    save_section_metadata(project_id, "translates", meta)


def update_section_metadata(project_id: str, section: str, data: dict):
    save_section_metadata(project_id, section, data)


def _remove_file_from_categories(categories: list, filename: str) -> list:
    result = []
    for cat in categories:
        cat["items"] = [i for i in cat.get("items", []) if i != filename]
        cat["subcategories"] = _remove_file_from_categories(cat.get("subcategories", []), filename)
        result.append(cat)
    return result


def get_doc_path(project_id: str, filename: str) -> Optional[Path]:
    p = get_project_path(project_id) / "docs" / filename
    return p if p.exists() else None


def get_translate_path(project_id: str, filename: str) -> Optional[Path]:
    p = get_project_path(project_id) / "translates" / filename
    return p if p.exists() else None
