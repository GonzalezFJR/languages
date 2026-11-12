"""Section metadata (docs / translates) and reference-document uploads."""

from pathlib import Path
from typing import Optional

from app.services.project_service import get_project_path, now_iso
from app.services.storage import LOCK, read_json, write_json, safe_name

ALLOWED_DOC_EXTENSIONS = {".pdf", ".txt", ".md", ".doc", ".docx", ".png", ".jpg", ".jpeg", ".webp"}
MAX_DOC_BYTES = 25 * 1024 * 1024


def _section_meta_path(project_id: str, section: str, user_dir: str) -> Path:
    if section not in ("docs", "translates"):
        raise ValueError("Sección no válida")
    return get_project_path(project_id, user_dir) / section / "metadata.json"


def load_section_metadata(project_id: str, section: str, user_dir: str = "public") -> dict:
    data = read_json(_section_meta_path(project_id, section, user_dir)) or {}
    data.setdefault("categories", [])
    data.setdefault("files", {})
    data.setdefault("available_tags", [])
    return data


def save_section_metadata(project_id: str, section: str, data: dict, user_dir: str = "public"):
    write_json(_section_meta_path(project_id, section, user_dir), data)


def list_docs(project_id: str, user_dir: str = "public") -> dict:
    return load_section_metadata(project_id, "docs", user_dir)


def list_translates(project_id: str, user_dir: str = "public") -> dict:
    return load_section_metadata(project_id, "translates", user_dir)


def update_file_meta(project_id: str, section: str, filename: str, updates: dict, user_dir: str = "public") -> dict:
    with LOCK:
        meta = load_section_metadata(project_id, section, user_dir)
        if filename not in meta["files"]:
            raise ValueError(f"Archivo no encontrado: {filename}")
        for key in ("display_name", "description", "tags"):
            if key in updates:
                meta["files"][filename][key] = updates[key]
        meta["files"][filename]["updated_at"] = now_iso()
        save_section_metadata(project_id, section, meta, user_dir)
    return meta["files"][filename]


def update_available_tags(project_id: str, section: str, tags: list, user_dir: str = "public") -> list:
    with LOCK:
        meta = load_section_metadata(project_id, section, user_dir)
        meta["available_tags"] = [str(t)[:40] for t in tags][:100]
        save_section_metadata(project_id, section, meta, user_dir)
    return meta["available_tags"]


def update_categories(project_id: str, section: str, categories: list, user_dir: str = "public") -> None:
    with LOCK:
        meta = load_section_metadata(project_id, section, user_dir)
        meta["categories"] = categories
        save_section_metadata(project_id, section, meta, user_dir)


def save_uploaded_doc(project_id: str, filename: str, content: bytes, display_name: str = "",
                      description: str = "", tags: Optional[list] = None, user_dir: str = "public") -> dict:
    filename = Path(filename or "").name
    suffix = Path(filename).suffix.lower()
    if suffix not in ALLOWED_DOC_EXTENSIONS:
        raise ValueError(f"Extensión no permitida: {suffix or '(ninguna)'}")
    if len(content) > MAX_DOC_BYTES:
        raise ValueError("El archivo supera 25 MB")
    import re
    stem = re.sub(r"[^\w\-]+", "_", Path(filename).stem).strip("_") or "documento"
    docs_path = get_project_path(project_id, user_dir) / "docs"
    docs_path.mkdir(parents=True, exist_ok=True)
    with LOCK:
        safe = f"{stem}{suffix}"
        counter = 2
        while (docs_path / safe).exists():
            safe = f"{stem}_{counter}{suffix}"
            counter += 1
        (docs_path / safe).write_bytes(content)
        meta = load_section_metadata(project_id, "docs", user_dir)
        meta["files"][safe] = {
            "name": safe,
            "display_name": display_name or stem.replace("_", " "),
            "description": description,
            "tags": tags or [],
            "uploaded_at": now_iso(),
        }
        save_section_metadata(project_id, "docs", meta, user_dir)
    return meta["files"][safe]


def delete_doc(project_id: str, filename: str, user_dir: str = "public"):
    _delete_from_section(project_id, "docs", filename, user_dir)


def _delete_from_section(project_id: str, section: str, filename: str, user_dir: str):
    safe_name(filename)
    with LOCK:
        target = get_project_path(project_id, user_dir) / section / filename
        if target.exists():
            target.unlink()
        meta = load_section_metadata(project_id, section, user_dir)
        meta["files"].pop(filename, None)
        meta["categories"] = remove_file_from_categories(meta["categories"], filename)
        save_section_metadata(project_id, section, meta, user_dir)


def remove_file_from_categories(categories: list, filename: str) -> list:
    result = []
    for cat in categories:
        cat["items"] = [i for i in cat.get("items", []) if i != filename]
        cat["subcategories"] = remove_file_from_categories(cat.get("subcategories", []), filename)
        result.append(cat)
    return result


def get_doc_path(project_id: str, filename: str, user_dir: str = "public") -> Optional[Path]:
    try:
        p = get_project_path(project_id, user_dir) / "docs" / safe_name(filename)
    except ValueError:
        return None
    return p if p.exists() else None
