"""Lextor texts inside projects: create, append, load, annotate, migrate, share."""

from __future__ import annotations

import logging
import secrets
from pathlib import Path
from typing import Optional

from app.config import settings
from app.lextor import document as lx
from app.lextor.legacy import xlan_to_lextor
from app.services import project_service as ps
from app.services.document_service import (
    load_section_metadata, save_section_metadata, remove_file_from_categories,
)
from app.services.storage import LOCK, read_json, write_json, slugify, safe_name, system_dir

log = logging.getLogger("lextor.texts")
EXT = ps.TEXT_EXT


class TextError(ValueError):
    def __init__(self, message: str, report: lx.Report | None = None):
        super().__init__(message)
        self.report = report


# ────────────────────────────────────────────────────────────── shares ──
# Every saved text gets an unguessable share id so it can be opened without logging in
# (links returned by the MCP, embeds). Anonymous MCP texts live in contents/_shared/.

def _shares_path() -> Path:
    return system_dir() / "shares.json"


def _shared_dir() -> Path:
    p = settings.contents_path / "_shared"
    p.mkdir(parents=True, exist_ok=True)
    return p


def new_share(user_dir: Optional[str], project_id: Optional[str], filename: Optional[str]) -> str:
    with LOCK:
        shares = read_json(_shares_path(), {}) or {}
        sid = secrets.token_urlsafe(9).replace("-", "x").replace("_", "y")
        while sid in shares:
            sid = secrets.token_urlsafe(9).replace("-", "x").replace("_", "y")
        shares[sid] = {"user_dir": user_dir, "project": project_id, "file": filename, "created_at": ps.now_iso()}
        write_json(_shares_path(), shares)
    return sid


def _register_share(sid: str, user_dir: str, project_id: str, filename: str) -> None:
    with LOCK:
        shares = read_json(_shares_path(), {}) or {}
        shares[sid] = {"user_dir": user_dir, "project": project_id, "file": filename, "created_at": ps.now_iso()}
        write_json(_shares_path(), shares)


def resolve_share(share_id: str) -> Optional[dict]:
    shares = read_json(_shares_path(), {}) or {}
    entry = shares.get(share_id)
    if not entry:
        return None
    return {"share_id": share_id, **entry}


def load_shared(share_id: str) -> Optional[tuple[dict, dict]]:
    """Return (document, share_entry) for a share id."""
    entry = resolve_share(share_id)
    if not entry:
        return None
    if entry.get("user_dir") is None:
        doc = read_json(_shared_dir() / f"{share_id}{EXT}")
    else:
        doc = load_text(entry["project"], entry["file"], entry["user_dir"])
    return (doc, entry) if doc else None


def save_anonymous(doc: dict) -> str:
    sid = new_share(None, None, None)
    write_json(_shared_dir() / f"{sid}{EXT}", doc)
    return sid


def update_anonymous(share_id: str, doc: dict) -> None:
    write_json(_shared_dir() / f"{safe_name(share_id)}{EXT}", doc)


# ─────────────────────────────────────────────────────────────── texts ──

def _translates_dir(project_id: str, user_dir: str) -> Path:
    return ps.get_project_path(project_id, user_dir) / "translates"


def text_path(project_id: str, filename: str, user_dir: str) -> Path:
    return _translates_dir(project_id, user_dir) / safe_name(filename)


def load_text(project_id: str, filename: str, user_dir: str = "public") -> Optional[dict]:
    try:
        p = text_path(project_id, filename, user_dir)
    except ValueError:
        return None
    data = read_json(p)
    if data is None:
        return None
    if "blocks" not in data and "content" in data:  # legacy file not yet migrated
        data = xlan_to_lextor(data)
    return data


def save_text(project_id: str, filename: str, doc: dict, user_dir: str) -> None:
    write_json(text_path(project_id, filename, user_dir), doc)


def list_texts(project_id: str, user_dir: str) -> dict:
    return load_section_metadata(project_id, "translates", user_dir)


def _register(project_id: str, filename: str, doc: dict, user_dir: str, source: str, tags=None) -> dict:
    meta = load_section_metadata(project_id, "translates", user_dir)
    entry = meta["files"].get(filename, {})
    stats = lx.compute_stats(doc)
    entry.update({
        "name": filename,
        "display_name": entry.get("display_name") or doc["meta"].get("title", filename),
        "description": entry.get("description") or doc["meta"].get("summary", ""),
        "tags": entry.get("tags") or list(tags or []),
        "level": doc["meta"].get("level"),
        "stats": stats,
        "source": entry.get("source") or source,
        "created_at": entry.get("created_at") or ps.now_iso(),
        "updated_at": ps.now_iso(),
    })
    if not entry.get("share_id"):
        entry["share_id"] = new_share(user_dir, project_id, filename)
    meta["files"][filename] = entry
    save_section_metadata(project_id, "translates", meta, user_dir)
    ps.touch_project(project_id, user_dir)
    return entry


def create_text(project_id: str, data, user_dir: str, *, source: str = "mcp", tags=None) -> tuple[str, dict, lx.Report]:
    project = ps.load_metadata(project_id, user_dir)
    if not project:
        raise TextError(f"Proyecto no encontrado: {project_id}")
    defaults = {"text_lang": project.get("target"), "notes_lang": project.get("base")}
    rep = lx.validate_document(data, defaults=defaults)
    if not rep.ok:
        raise TextError("El documento no supera la validación", rep)
    doc = rep.doc
    if doc["meta"]["text_lang"] != project.get("target") or doc["meta"]["notes_lang"] != project.get("base"):
        rep.warn("languages", "W_PROJECT_LANGS", "meta",
                 f"El proyecto es {project.get('target')}→{project.get('base')} pero el texto es "
                 f"{doc['meta']['text_lang']}→{doc['meta']['notes_lang']}")
    now = ps.now_iso()
    doc["meta"].setdefault("created_at", now)
    doc["meta"]["updated_at"] = now
    with LOCK:
        tdir = _translates_dir(project_id, user_dir)
        tdir.mkdir(parents=True, exist_ok=True)
        base = (slugify(doc["meta"].get("title", ""), "") or slugify(doc["meta"].get("title_translation", ""), "")
                or "texto")
        filename = f"{base}{EXT}"
        i = 2
        while (tdir / filename).exists():
            filename = f"{base}_{i}{EXT}"
            i += 1
        save_text(project_id, filename, doc, user_dir)
        entry = _register(project_id, filename, doc, user_dir, source, tags)
    return filename, entry, rep


def append_blocks(project_id: str, filename: str, blocks, user_dir: str) -> tuple[dict, dict, lx.Report]:
    with LOCK:
        doc = load_text(project_id, filename, user_dir)
        if not doc:
            raise TextError(f"Texto no encontrado: {filename}")
        rep = lx.Report()
        blocks = lx.parse_input(blocks) if isinstance(blocks, (str, bytes)) else blocks
        if isinstance(blocks, dict) and "blocks" in blocks:
            blocks = blocks["blocks"]
        new_blocks = lx.validate_blocks(blocks, doc["meta"]["text_lang"], doc["meta"]["notes_lang"], rep)
        if not rep.ok:
            raise TextError("Los bloques no superan la validación", rep)
        doc["blocks"].extend(new_blocks)
        doc["meta"]["updated_at"] = ps.now_iso()
        rep.doc = {"format": lx.FORMAT_VERSION, "meta": doc["meta"], "blocks": new_blocks}
        rep.stats = lx.compute_stats(doc)
        save_text(project_id, filename, doc, user_dir)
        entry = _register(project_id, filename, doc, user_dir, "mcp")
    return doc, entry, rep


def replace_text(project_id: str, filename: str, data, user_dir: str) -> tuple[dict, lx.Report]:
    """Replace a text's content (keeps annotations whose ids still exist)."""
    with LOCK:
        old = load_text(project_id, filename, user_dir)
        if not old:
            raise TextError(f"Texto no encontrado: {filename}")
        rep = lx.validate_document(data, defaults={"text_lang": old["meta"]["text_lang"],
                                                   "notes_lang": old["meta"]["notes_lang"]})
        if not rep.ok:
            raise TextError("El documento no supera la validación", rep)
        doc = rep.doc
        doc["meta"]["created_at"] = old["meta"].get("created_at", ps.now_iso())
        doc["meta"]["updated_at"] = ps.now_iso()
        ids = {cid for cid, *_ in lx.iter_chunks(doc)}
        ann = {k: v for k, v in (old.get("annotations") or {}).items() if k in ids}
        if ann:
            doc["annotations"] = ann
        save_text(project_id, filename, doc, user_dir)
        _register(project_id, filename, doc, user_dir, "mcp")
    return doc, rep


def delete_text(project_id: str, filename: str, user_dir: str) -> None:
    safe_name(filename)
    with LOCK:
        p = text_path(project_id, filename, user_dir)
        if p.exists():
            p.unlink()
        meta = load_section_metadata(project_id, "translates", user_dir)
        entry = meta["files"].pop(filename, None) or {}
        meta["categories"] = remove_file_from_categories(meta["categories"], filename)
        save_section_metadata(project_id, "translates", meta, user_dir)
        if entry.get("share_id"):
            shares = read_json(_shares_path(), {}) or {}
            shares.pop(entry["share_id"], None)
            write_json(_shares_path(), shares)


def set_annotation(project_id: str, filename: str, chunk_id: str, ann: Optional[dict], user_dir: str) -> dict:
    with LOCK:
        doc = load_text(project_id, filename, user_dir)
        if not doc:
            raise TextError("Texto no encontrado")
        ids = {cid for cid, *_ in lx.iter_chunks(doc)}
        if chunk_id not in ids:
            raise TextError("Fragmento no encontrado")
        anns = doc.setdefault("annotations", {})
        clean = {}
        if ann:
            if ann.get("color"):
                clean["color"] = str(ann["color"])[:20]
            if ann.get("comment"):
                clean["comment"] = str(ann["comment"])[:2000]
            if ann.get("saved"):
                clean["saved"] = True
        if clean:
            anns[chunk_id] = clean
        else:
            anns.pop(chunk_id, None)
        if not anns:
            doc.pop("annotations", None)
        save_text(project_id, filename, doc, user_dir)
    return clean


def set_block_type(project_id: str, filename: str, block_index: int, btype: str, level: int, user_dir: str) -> None:
    """Light structural editing from the reader: change a block's type."""
    if btype not in lx.BLOCK_TYPES:
        raise TextError("Tipo de bloque no válido")
    with LOCK:
        doc = load_text(project_id, filename, user_dir)
        if not doc or not (0 <= block_index < len(doc["blocks"])):
            raise TextError("Bloque no encontrado")
        b = doc["blocks"][block_index]
        b["type"] = btype
        if btype == "heading":
            b["level"] = max(1, min(3, int(level or 1)))
        else:
            b.pop("level", None)
        save_text(project_id, filename, doc, user_dir)


def import_file(project_id: str, raw: bytes, user_dir: str, display_name: str = "") -> tuple[str, dict, lx.Report]:
    filename, entry, rep = create_text(project_id, raw, user_dir, source="upload")
    if display_name:
        from app.services.document_service import update_file_meta
        entry = update_file_meta(project_id, "translates", filename, {"display_name": display_name}, user_dir)
    return filename, entry, rep


# ──────────────────────────────────────────────────────────── migration ──

def migrate_legacy_all() -> int:
    """Convert every legacy .xlan (incl. multi-part) into a single .lextor. Idempotent.
    Originals are kept in translates/_legacy/."""
    root = settings.contents_path
    count = 0
    if not root.exists():
        return 0
    for user_dir in root.iterdir():
        if not user_dir.is_dir() or user_dir.name.startswith(("_", ".")) or user_dir.name == "instructions":
            continue
        for project in user_dir.iterdir():
            tdir = project / "translates"
            if not tdir.is_dir() or not any(tdir.glob("*.xlan")):
                continue
            try:
                count += _migrate_project(user_dir.name, project.name, tdir)
            except Exception:  # never block startup
                log.exception("Migration failed for %s/%s", user_dir.name, project.name)
    return count


def _migrate_project(user_dir: str, project_id: str, tdir: Path) -> int:
    meta = load_section_metadata(project_id, "translates", user_dir)
    legacy_dir = tdir / "_legacy"
    legacy_dir.mkdir(exist_ok=True)
    renames: dict[str, str] = {}
    part_files = set()
    for entry in meta["files"].values():
        parts = entry.get("parts") or []
        part_files.update(p for p in parts[1:])
    n = 0
    for f in sorted(tdir.glob("*.xlan")):
        if f.name in part_files:
            continue
        entry = meta["files"].get(f.name, {})
        parts = entry.get("parts") or [f.name]
        merged = None
        for pf in parts:
            data = read_json(tdir / pf)
            if not data:
                continue
            if merged is None:
                merged = data
            else:
                merged.setdefault("content", []).extend(data.get("content", []))
        if merged is None:
            continue
        doc = xlan_to_lextor(merged)
        rep = lx.validate_document(doc)
        if not rep.ok:
            log.warning("Legacy %s has errors after conversion: %s", f, rep.summary())
            continue
        if doc.get("annotations"):
            rep.doc["annotations"] = doc["annotations"]
        new_name = f.stem + EXT
        write_json(tdir / new_name, rep.doc)
        renames[f.name] = new_name
        new_entry = {k: v for k, v in entry.items() if k != "parts"}
        new_entry["name"] = new_name
        new_entry["source"] = "migrated"
        new_entry["stats"] = lx.compute_stats(rep.doc)
        new_entry.setdefault("display_name", rep.doc["meta"]["title"])
        new_entry.setdefault("created_at", ps.now_iso())
        meta["files"].pop(f.name, None)
        meta["files"][new_name] = new_entry
        for pf in parts:
            if (tdir / pf).exists():
                (tdir / pf).rename(legacy_dir / pf)
        n += 1
    for pf in part_files:
        meta["files"].pop(pf, None)
        if (tdir / pf).exists():
            (tdir / pf).rename(legacy_dir / pf)

    def _rename(cats):
        for c in cats:
            c["items"] = [renames.get(i, i) for i in c.get("items", []) if i not in part_files]
            _rename(c.get("subcategories", []))
    _rename(meta["categories"])
    for name, entry in meta["files"].items():
        if not entry.get("share_id"):
            entry["share_id"] = new_share(user_dir, project_id, name)
    save_section_metadata(project_id, "translates", meta, user_dir)
    return n


def ensure_share_ids() -> None:
    """Make sure every text has a share id (texts created before shares existed)."""
    root = settings.contents_path
    if not root.exists():
        return
    for user_dir in root.iterdir():
        if not user_dir.is_dir() or user_dir.name.startswith(("_", ".")):
            continue
        for project in user_dir.iterdir():
            mp = project / "translates" / "metadata.json"
            if not mp.exists():
                continue
            meta = load_section_metadata(project.name, "translates", user_dir.name)
            changed = False
            for name, entry in meta["files"].items():
                sid = entry.get("share_id")
                if not sid:
                    entry["share_id"] = new_share(user_dir.name, project.name, name)
                    changed = True
                elif resolve_share(sid) is None:  # id versioned in metadata but index is fresh
                    _register_share(sid, user_dir.name, project.name, name)
            if changed:
                save_section_metadata(project.name, "translates", meta, user_dir.name)
