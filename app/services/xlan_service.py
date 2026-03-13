import json
from pathlib import Path
from datetime import datetime
from typing import Optional

from app.config import settings
from app.services.project_service import get_project_path
from app.services.document_service import load_section_metadata, save_section_metadata


def load_xlan(project_id: str, filename: str) -> Optional[dict]:
    xlan_path = get_project_path(project_id) / "translates" / filename
    if not xlan_path.exists():
        return None
    with open(xlan_path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_xlan(project_id: str, filename: str, data: dict):
    xlan_path = get_project_path(project_id) / "translates" / filename
    with open(xlan_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def register_xlan_in_metadata(project_id: str, filename: str, display_name: str, description: str, tags: list = None):
    meta = load_section_metadata(project_id, "translates")
    meta["files"][filename] = {
        "name": filename,
        "display_name": display_name,
        "description": description,
        "tags": tags or [],
        "created_at": datetime.utcnow().isoformat(),
    }
    save_section_metadata(project_id, "translates", meta)


def update_block_note(project_id: str, filename: str, block_index: int, note: dict) -> dict:
    xlan = load_xlan(project_id, filename)
    if not xlan:
        raise ValueError(f"Archivo no encontrado: {filename}")
    blocks = xlan.get("content", [])
    if block_index < 0 or block_index >= len(blocks):
        raise ValueError(f"Índice de bloque fuera de rango: {block_index}")
    if note is None:
        blocks[block_index].pop("user_note", None)
    else:
        blocks[block_index]["user_note"] = note
    xlan["content"] = blocks
    save_xlan(project_id, filename, xlan)
    return blocks[block_index].get("user_note", {})


def update_segment_note(project_id: str, filename: str, block_index: int, seg_id: str, note: Optional[dict]) -> dict:
    xlan = load_xlan(project_id, filename)
    if not xlan:
        raise ValueError(f"Archivo no encontrado: {filename}")
    blocks = xlan.get("content", [])
    if block_index < 0 or block_index >= len(blocks):
        raise ValueError(f"Índice de bloque fuera de rango: {block_index}")
    segs = blocks[block_index].get("segments", [])
    found = False
    for seg in segs:
        if seg.get("id") == seg_id:
            if note is None:
                seg.pop("user_note", None)
            else:
                seg["user_note"] = note
            found = True
            break
    if not found:
        raise ValueError(f"Segmento no encontrado: {seg_id}")
    xlan["content"] = blocks
    save_xlan(project_id, filename, xlan)
    target = next((s for s in segs if s.get("id") == seg_id), {})
    return target.get("user_note", {})


def update_xlan_file_meta(project_id: str, filename: str, updates: dict) -> dict:
    from app.services.document_service import update_file_meta
    return update_file_meta(project_id, "translates", filename, updates)


def pipeline_text_to_xlan(
    project_id: str,
    raw_text: str,
    title: str,
    description: str,
    text_language: str,
    notes_language: str,
) -> dict:
    """
    Pipeline placeholder: converts raw text into .xlan format.
    text_language: the language of the input text (the language being studied).
    notes_language: the language for translations/explanations (the student's native language).
    In production this would call an LLM / translation API.
    Currently produces a draft .xlan where each sentence is a single
    untranslated segment, ready for manual editing.
    """
    import re

    paragraphs = [p.strip() for p in raw_text.split("\n\n") if p.strip()]
    content = []
    seg_counter = 0

    for para in paragraphs:
        sentences = re.split(r"(?<=[.!?])\s+", para.strip())
        segments = []
        for sentence in sentences:
            if not sentence:
                continue
            seg_counter += 1
            segments.append({
                "id": f"seg_{seg_counter}",
                "text": sentence,
                "translation": "",
                "info": "[pendiente de revisión]",
                "styles": [],
            })
        if segments:
            content.append({"type": "paragraph", "segments": segments})

    slug = _slugify(title)
    filename = f"{slug}.xlan"

    xlan_data = {
        "meta": {
            "title": title,
            "description": description,
            "text_language": text_language,
            "notes_language": notes_language,
            "created_at": datetime.utcnow().isoformat(),
        },
        "content": content,
    }

    translates_path = get_project_path(project_id) / "translates"
    dest = translates_path / filename
    counter = 1
    while dest.exists():
        filename = f"{slug}_{counter}.xlan"
        dest = translates_path / filename
        counter += 1

    save_xlan(project_id, filename, xlan_data)
    register_xlan_in_metadata(project_id, filename, title, description)

    return {"filename": filename, "xlan": xlan_data}


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
    return text.strip("_")
