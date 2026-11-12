"""Lextor MCP tools: definitions + implementations (transport-agnostic)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, Optional

from app.lextor import document as lx
from app.lextor import languages
from app.lextor.guide import build_guide, example_document
from app.services import project_service as ps
from app.services import text_service as ts

WIDGET_URI = "ui://lextor/reader-v3.html"
MAX_WIDGET_DOC_CHARS = 140_000


@dataclass
class CallContext:
    user_dir: Optional[str]      # None = anonymous connection (no personal token)
    base_url: str                # absolute public base URL, no trailing slash

    @property
    def anonymous(self) -> bool:
        return self.user_dir is None


class ToolError(Exception):
    """Error shown to the model as a tool result with isError=true."""


# ─────────────────────────────────────────────────────────── schemas ──

_DOC_SCHEMA = {
    "type": "object",
    "description": "A lextor/1 document. Call lextor_get_format_guide first for the exact rules.",
    "properties": {
        "meta": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "title_translation": {"type": "string"},
                "text_lang": {"type": "string", "description": "ISO 639-1 code of the text being studied"},
                "notes_lang": {"type": "string", "description": "ISO 639-1 code of the learner's language"},
                "level": {"type": "string", "enum": list(lx.LEVELS)},
                "summary": {"type": "string"},
                "author": {"type": "string"},
                "source": {"type": "string"},
            },
            "required": ["title", "text_lang", "notes_lang"],
        },
        "blocks": {"type": "array", "items": {"$ref": "#/$defs/block"}},
    },
    "required": ["meta", "blocks"],
}

_BLOCK_DEFS = {
    "block": {
        "type": "object",
        "properties": {
            "type": {"type": "string", "enum": list(lx.BLOCK_TYPES)},
            "level": {"type": "integer", "minimum": 1, "maximum": 3},
            "sentences": {"type": "array", "items": {"$ref": "#/$defs/sentence"}},
        },
        "required": ["type", "sentences"],
    },
    "sentence": {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "Exact original sentence or verse line, no line breaks"},
            "translation": {"type": "string", "description": "Natural translation of the whole sentence"},
            "note": {"type": "string"},
            "chunks": {"type": "array", "items": {"$ref": "#/$defs/chunk"}},
        },
        "required": ["text", "translation", "chunks"],
    },
    "chunk": {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "Exact substring of the sentence text"},
            "gloss": {"type": "string", "description": "Contextual translation (one option)"},
            "note": {"type": "string", "description": "Short teaching note in notes_lang"},
            "translit": {"type": "string"},
            "reading": {"type": "string"},
            "lemma": {"type": "string"},
            "grammar": {"type": "string"},
            "literal": {"type": "string"},
            "group": {"type": "string"},
        },
        "required": ["text", "gloss"],
    },
}

_TEXT_ID = {"type": "string",
            "description": "Text id as returned by lextor_create_text / lextor_list_texts ('project/file.lextor' or 'share:ID')"}

_UI_META = {
    "ui": {"resourceUri": WIDGET_URI, "visibility": ["model", "app"]},
    "ui/resourceUri": WIDGET_URI,
    "openai/outputTemplate": WIDGET_URI,
    "openai/widgetAccessible": True,
}


def _ui_meta(invoking: str, invoked: str) -> dict:
    return {**_UI_META, "openai/toolInvocation/invoking": invoking, "openai/toolInvocation/invoked": invoked}


_RO = {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}
_WRITE = {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False}

_TEXT_OUTPUT = {
    "type": "object",
    "properties": {
        "message": {"type": "string"},
        "text_id": {"type": "string"},
        "title": {"type": "string"},
        "url": {"type": "string"},
        "stats": {"type": "object"},
    },
}

TOOLS: list[dict] = [
    {
        "name": "lextor_get_format_guide",
        "title": "Guía del formato Lextor",
        "description": ("Returns the exact lextor/1 JSON format, segmentation and note-writing rules, transliteration "
                        "rules for the language and a complete example. Call it once before creating texts."),
        "inputSchema": {"type": "object", "properties": {
            "text_lang": {"type": "string", "description": "Language of the text (ISO 639-1), e.g. 'ja'"},
            "notes_lang": {"type": "string", "description": "Learner's language (ISO 639-1), e.g. 'es'"},
            "level": {"type": "string", "enum": list(lx.LEVELS)},
        }},
        "annotations": {**_RO, "title": "Format guide"},
    },
    {
        "name": "lextor_validate",
        "title": "Validar documento Lextor",
        "description": ("Runs the Lextor test suite on a document WITHOUT saving it: structure, exact chunk alignment, "
                        "coverage, granularity, translations, notes, transliteration. Returns pass/fail per test with "
                        "exact JSON paths and fix hints."),
        "inputSchema": {"type": "object", "properties": {"document": _DOC_SCHEMA}, "required": ["document"],
                        "$defs": _BLOCK_DEFS},
        "annotations": {**_RO, "title": "Validate"},
    },
    {
        "name": "lextor_create_text",
        "title": "Crear texto bilingüe",
        "description": ("Validates and saves a lextor/1 document as an interactive bilingual text, then shows it in "
                        "the Lextor reader. If validation fails nothing is saved and the errors are returned: fix them "
                        "and call again. For long texts send the first ~30 sentences here and continue with "
                        "lextor_append_blocks. project_id is optional: a project for the language pair is chosen or "
                        "created automatically."),
        "inputSchema": {"type": "object", "properties": {
            "document": _DOC_SCHEMA,
            "project_id": {"type": "string", "description": "Optional project id from lextor_list_projects"},
            "tags": {"type": "array", "items": {"type": "string"}},
        }, "required": ["document"], "$defs": _BLOCK_DEFS},
        "outputSchema": _TEXT_OUTPUT,
        "annotations": {**_WRITE, "title": "Create text"},
        "_meta": _ui_meta("Creando el texto bilingüe…", "Texto listo"),
    },
    {
        "name": "lextor_append_blocks",
        "title": "Añadir bloques a un texto",
        "description": ("Appends more blocks to an existing text (continuation of long texts). Blocks are validated "
                        "exactly like in lextor_create_text. When the whole text is done, call lextor_show_text."),
        "inputSchema": {"type": "object", "properties": {
            "text_id": _TEXT_ID,
            "blocks": {"type": "array", "items": {"$ref": "#/$defs/block"}},
        }, "required": ["text_id", "blocks"], "$defs": _BLOCK_DEFS},
        "outputSchema": _TEXT_OUTPUT,
        "annotations": {**_WRITE, "title": "Append blocks"},
    },
    {
        "name": "lextor_show_text",
        "title": "Mostrar texto en el lector",
        "description": "Displays a saved Lextor text in the interactive bilingual reader and returns its link.",
        "inputSchema": {"type": "object", "properties": {"text_id": _TEXT_ID}, "required": ["text_id"]},
        "outputSchema": _TEXT_OUTPUT,
        "annotations": {**_RO, "title": "Show text"},
        "_meta": _ui_meta("Abriendo el lector…", "Lector abierto"),
    },
    {
        "name": "lextor_update_text",
        "title": "Reemplazar un texto",
        "description": ("Replaces the full content of an existing text with a corrected lextor/1 document "
                        "(e.g. after the user asks for changes). Get the current version with lextor_get_text."),
        "inputSchema": {"type": "object", "properties": {"text_id": _TEXT_ID, "document": _DOC_SCHEMA},
                        "required": ["text_id", "document"], "$defs": _BLOCK_DEFS},
        "outputSchema": _TEXT_OUTPUT,
        "annotations": {**_WRITE, "destructiveHint": True, "idempotentHint": True, "title": "Update text"},
        "_meta": _ui_meta("Actualizando el texto…", "Texto actualizado"),
    },
    {
        "name": "lextor_get_text",
        "title": "Leer un texto",
        "description": "Returns a saved text as lextor/1 JSON (to review, correct or continue it) or as plain text.",
        "inputSchema": {"type": "object", "properties": {
            "text_id": _TEXT_ID,
            "as": {"type": "string", "enum": ["json", "plain"], "default": "json"},
        }, "required": ["text_id"]},
        "annotations": {**_RO, "title": "Get text"},
    },
    {
        "name": "lextor_list_projects",
        "title": "Listar proyectos",
        "description": "Lists the user's Lextor projects (each has a studied language and a learner language).",
        "inputSchema": {"type": "object", "properties": {}},
        "annotations": {**_RO, "title": "List projects"},
    },
    {
        "name": "lextor_list_texts",
        "title": "Listar textos de un proyecto",
        "description": "Lists the texts in a project with their ids, titles, levels and sizes.",
        "inputSchema": {"type": "object", "properties": {"project_id": {"type": "string"}},
                        "required": ["project_id"]},
        "annotations": {**_RO, "title": "List texts"},
    },
    {
        "name": "lextor_create_project",
        "title": "Crear proyecto",
        "description": "Creates a project for a language pair.",
        "inputSchema": {"type": "object", "properties": {
            "name": {"type": "string", "description": "e.g. 'Japonés — lecturas'"},
            "text_lang": {"type": "string", "description": "Language studied (ISO 639-1)"},
            "notes_lang": {"type": "string", "description": "Learner's language (ISO 639-1)"},
        }, "required": ["name", "text_lang", "notes_lang"]},
        "annotations": {**_WRITE, "title": "Create project"},
    },
]

TOOLS_BY_NAME = {t["name"]: t for t in TOOLS}


# ────────────────────────────────────────────────────────── helpers ──

def _text_result(text: str, structured: Optional[dict] = None, meta: Optional[dict] = None,
                 is_error: bool = False) -> dict:
    res: dict = {"content": [{"type": "text", "text": text}], "isError": is_error}
    if structured is not None:
        res["structuredContent"] = structured
    if meta:
        res["_meta"] = meta
    return res


def _require_user(ctx: CallContext) -> str:
    if ctx.anonymous:
        raise ToolError(
            "This Lextor connection has no personal token, so it cannot access a library. "
            f"The user can get a personal connector URL at {ctx.base_url}/connect . "
            "You can still create texts: lextor_create_text works anonymously and returns a share link.")
    return ctx.user_dir  # type: ignore[return-value]


def _links(ctx: CallContext, share_id: Optional[str], project_id: Optional[str] = None,
           filename: Optional[str] = None) -> dict:
    links = {}
    if share_id:
        links["url"] = f"{ctx.base_url}/t/{share_id}"
        links["embed_url"] = f"{ctx.base_url}/embed/{share_id}"
    if project_id and filename:
        links["library_url"] = f"{ctx.base_url}/read/{project_id}/{filename}"
    return links


def _parse_text_id(text_id: str) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """Return (share_id, project_id, filename)."""
    text_id = (text_id or "").strip()
    if text_id.startswith("share:"):
        return text_id[6:], None, None
    if "/" in text_id:
        project_id, filename = text_id.split("/", 1)
        return None, project_id, filename
    raise ToolError(f"text_id no válido: {text_id!r}. Usa el id devuelto por lextor_create_text o lextor_list_texts.")


def _load_by_id(ctx: CallContext, text_id: str) -> tuple[dict, Optional[str], Optional[str], Optional[str]]:
    share_id, project_id, filename = _parse_text_id(text_id)
    if share_id:
        found = ts.load_shared(share_id)
        if not found:
            raise ToolError(f"Texto no encontrado: {text_id}")
        doc, entry = found
        if entry.get("user_dir") is not None:
            if entry.get("user_dir") != ctx.user_dir:
                return doc, share_id, None, None  # read-only view of someone else's shared text
            return doc, share_id, entry["project"], entry["file"]
        return doc, share_id, None, None
    user_dir = _require_user(ctx)
    doc = ts.load_text(project_id, filename, user_dir)
    if not doc:
        raise ToolError(f"Texto no encontrado: {text_id}")
    entry = ts.list_texts(project_id, user_dir)["files"].get(filename, {})
    return doc, entry.get("share_id"), project_id, filename


def _reader_payload(ctx: CallContext, doc: dict, text_id: str, links: dict, message: str,
                    report: Optional[lx.Report] = None) -> dict:
    stats = lx.compute_stats(doc)
    structured = {"message": message, "text_id": text_id, "title": doc["meta"].get("title", ""),
                  **({"url": links["url"]} if links.get("url") else {}), "stats": stats}
    meta: dict = {"lextor/links": links, "lextor/text_id": text_id}
    if len(json.dumps(doc, ensure_ascii=False)) <= MAX_WIDGET_DOC_CHARS:
        meta["lextor/document"] = doc
    text = message
    if links.get("url"):
        text += f"\nEnlace para el usuario: {links['url']}"
    if report is not None and (report.warnings or report.fixes):
        text += "\n\n" + report.as_text(limit=15)
    text += ("\n\n(The interactive reader is displayed to the user if the client supports MCP Apps; "
             "otherwise share the link. Don't paste the whole text back into the chat.)")
    return _text_result(text, structured, meta)


def _resolve_project(ctx: CallContext, user_dir: str, project_id: Optional[str], doc_in: Any) -> str:
    if project_id:
        if not ps.load_metadata(project_id, user_dir):
            raise ToolError(f"Proyecto no encontrado: {project_id}. Usa lextor_list_projects.")
        return project_id
    try:
        data = lx.parse_input(doc_in)
        meta = data.get("meta", {}) if isinstance(data, dict) else {}
    except Exception:
        meta = {}
    tl = (meta.get("text_lang") or meta.get("text_language") or "").lower()
    nl = (meta.get("notes_lang") or meta.get("notes_language") or "").lower()
    if not (languages.is_valid_code(tl) and languages.is_valid_code(nl)):
        raise ToolError("Indica meta.text_lang y meta.notes_lang (códigos ISO 639-1) o un project_id.")
    found = ps.find_project_for_pair(tl, nl, user_dir)
    if found:
        return found["id"]
    name = f"{languages.name(tl)} (notas en {languages.name(nl).lower()})"
    return ps.create_project(name, nl, tl, user_dir)["id"]


# ──────────────────────────────────────────────────────── implementations ──

def t_get_format_guide(ctx: CallContext, args: dict) -> dict:
    guide = build_guide(args.get("text_lang"), args.get("notes_lang"), args.get("level"))
    return _text_result(guide)


def t_validate(ctx: CallContext, args: dict) -> dict:
    rep = lx.validate_document(args.get("document"))
    return _text_result(rep.as_text(limit=60), rep.to_dict(), is_error=False)


def t_create_text(ctx: CallContext, args: dict) -> dict:
    doc_in = args.get("document")
    if doc_in is None:
        raise ToolError("Falta `document`.")
    if ctx.anonymous:
        rep = lx.validate_document(doc_in)
        if not rep.ok:
            return _text_result("No se ha guardado nada. Corrige estos errores y vuelve a llamar:\n\n" +
                                rep.as_text(limit=60), rep.to_dict(), is_error=True)
        doc = rep.doc
        doc["meta"]["created_at"] = doc["meta"]["updated_at"] = ps.now_iso()
        sid = ts.save_anonymous(doc)
        links = _links(ctx, sid)
        return _reader_payload(ctx, doc, f"share:{sid}", links,
                               f"Texto «{doc['meta']['title']}» creado (sin biblioteca personal: enlace público).",
                               rep)
    user_dir = ctx.user_dir
    project_id = _resolve_project(ctx, user_dir, args.get("project_id"), doc_in)
    try:
        filename, entry, rep = ts.create_text(project_id, doc_in, user_dir, tags=args.get("tags"))
    except ts.TextError as e:
        if e.report:
            return _text_result(f"No se ha guardado nada. Corrige estos errores y vuelve a llamar:\n\n"
                                f"{e.report.as_text(limit=60)}", e.report.to_dict(), is_error=True)
        raise ToolError(str(e))
    doc = ts.load_text(project_id, filename, user_dir)
    links = _links(ctx, entry.get("share_id"), project_id, filename)
    text_id = f"{project_id}/{filename}"
    return _reader_payload(ctx, doc, text_id, links,
                           f"Texto «{doc['meta']['title']}» guardado en el proyecto «{project_id}» (text_id: {text_id}).",
                           rep)


def t_append_blocks(ctx: CallContext, args: dict) -> dict:
    share_id, project_id, filename = _parse_text_id(args.get("text_id", ""))
    blocks = args.get("blocks")
    if share_id:
        found = ts.load_shared(share_id)
        if not found or found[1].get("user_dir") is not None:
            if found and found[1].get("user_dir") == ctx.user_dir and not ctx.anonymous:
                project_id, filename = found[1]["project"], found[1]["file"]
            else:
                raise ToolError("Solo se pueden ampliar textos propios.")
        else:
            doc = found[0]
            rep = lx.Report()
            if isinstance(blocks, (str, bytes)):
                blocks = lx.parse_input(blocks)
            new_blocks = lx.validate_blocks(blocks, doc["meta"]["text_lang"], doc["meta"]["notes_lang"], rep)
            if not rep.ok:
                return _text_result("No se ha añadido nada. Corrige:\n\n" + rep.as_text(limit=60),
                                    rep.to_dict(), is_error=True)
            doc["blocks"].extend(new_blocks)
            doc["meta"]["updated_at"] = ps.now_iso()
            ts.update_anonymous(share_id, doc)
            stats = lx.compute_stats(doc)
            return _text_result(
                f"Añadidos {len(new_blocks)} bloques. Total: {stats['sentences']} frases. "
                f"Cuando termines llama a lextor_show_text con text_id share:{share_id}."
                + ("\n\n" + rep.as_text(15) if rep.warnings else ""),
                {"message": "ok", "text_id": f"share:{share_id}", "stats": stats})
    user_dir = _require_user(ctx)
    try:
        doc, entry, rep = ts.append_blocks(project_id, filename, blocks, user_dir)
    except ts.TextError as e:
        if e.report:
            return _text_result("No se ha añadido nada. Corrige:\n\n" + e.report.as_text(limit=60),
                                e.report.to_dict(), is_error=True)
        raise ToolError(str(e))
    stats = lx.compute_stats(doc)
    text_id = f"{project_id}/{filename}"
    return _text_result(
        f"Añadidos {len(rep.doc['blocks'])} bloques. Total: {stats['blocks']} bloques, {stats['sentences']} frases. "
        f"Cuando termines llama a lextor_show_text con text_id {text_id}."
        + ("\n\n" + rep.as_text(15) if rep.warnings else ""),
        {"message": "ok", "text_id": text_id, "stats": stats})


def t_show_text(ctx: CallContext, args: dict) -> dict:
    doc, share_id, project_id, filename = _load_by_id(ctx, args.get("text_id", ""))
    links = _links(ctx, share_id, project_id, filename)
    return _reader_payload(ctx, doc, args["text_id"], links, f"Mostrando «{doc['meta'].get('title', '')}».")


def t_update_text(ctx: CallContext, args: dict) -> dict:
    share_id, project_id, filename = _parse_text_id(args.get("text_id", ""))
    if share_id:
        found = ts.load_shared(share_id)
        if not found:
            raise ToolError("Texto no encontrado")
        if found[1].get("user_dir") is None:
            rep = lx.validate_document(args.get("document"))
            if not rep.ok:
                return _text_result("No se ha cambiado nada. Corrige:\n\n" + rep.as_text(60), rep.to_dict(),
                                    is_error=True)
            ts.update_anonymous(share_id, rep.doc)
            return _reader_payload(ctx, rep.doc, args["text_id"], _links(ctx, share_id), "Texto actualizado.", rep)
        if found[1].get("user_dir") != ctx.user_dir:
            raise ToolError("Solo se pueden modificar textos propios.")
        project_id, filename = found[1]["project"], found[1]["file"]
    user_dir = _require_user(ctx)
    try:
        doc, rep = ts.replace_text(project_id, filename, args.get("document"), user_dir)
    except ts.TextError as e:
        if e.report:
            return _text_result("No se ha cambiado nada. Corrige:\n\n" + e.report.as_text(60), e.report.to_dict(),
                                is_error=True)
        raise ToolError(str(e))
    entry = ts.list_texts(project_id, user_dir)["files"].get(filename, {})
    return _reader_payload(ctx, doc, f"{project_id}/{filename}",
                           _links(ctx, entry.get("share_id"), project_id, filename), "Texto actualizado.", rep)


def t_get_text(ctx: CallContext, args: dict) -> dict:
    doc, *_ = _load_by_id(ctx, args.get("text_id", ""))
    if args.get("as") == "plain":
        return _text_result(lx.plain_text(doc))
    clean = {k: v for k, v in doc.items() if k != "annotations"}
    return _text_result(json.dumps(clean, ensure_ascii=False))


def t_list_projects(ctx: CallContext, args: dict) -> dict:
    user_dir = _require_user(ctx)
    items = [{"project_id": p["id"], "name": p["name"], "text_lang": p.get("target"), "notes_lang": p.get("base"),
              "texts": p.get("text_count", 0)} for p in ps.list_projects(user_dir)]
    if not items:
        text = "No hay proyectos todavía. lextor_create_text creará uno automáticamente."
    else:
        text = "\n".join(f"- {i['project_id']}: {i['name']} ({i['text_lang']}→{i['notes_lang']}, {i['texts']} textos)"
                         for i in items)
    return _text_result(text, {"projects": items})


def t_list_texts(ctx: CallContext, args: dict) -> dict:
    user_dir = _require_user(ctx)
    pid = args.get("project_id", "")
    if not ps.load_metadata(pid, user_dir):
        raise ToolError(f"Proyecto no encontrado: {pid}")
    files = ts.list_texts(pid, user_dir)["files"]
    items = [{"text_id": f"{pid}/{name}", "title": e.get("display_name", name), "level": e.get("level"),
              "sentences": (e.get("stats") or {}).get("sentences"), "updated_at": e.get("updated_at")}
             for name, e in files.items()]
    text = "\n".join(f"- {i['text_id']}: {i['title']}" + (f" [{i['level']}]" if i['level'] else "")
                     for i in items) or "El proyecto no tiene textos."
    return _text_result(text, {"texts": items})


def t_create_project(ctx: CallContext, args: dict) -> dict:
    user_dir = _require_user(ctx)
    tl, nl = (args.get("text_lang") or "").lower(), (args.get("notes_lang") or "").lower()
    if not (languages.is_valid_code(tl) and languages.is_valid_code(nl)) or tl == nl:
        raise ToolError("text_lang y notes_lang deben ser códigos ISO 639-1 distintos")
    p = ps.create_project(args.get("name") or languages.name(tl), nl, tl, user_dir)
    return _text_result(f"Proyecto creado: {p['id']}", {"project_id": p["id"], "name": p["name"]})


IMPLEMENTATIONS: dict[str, Callable[[CallContext, dict], dict]] = {
    "lextor_get_format_guide": t_get_format_guide,
    "lextor_validate": t_validate,
    "lextor_create_text": t_create_text,
    "lextor_append_blocks": t_append_blocks,
    "lextor_show_text": t_show_text,
    "lextor_update_text": t_update_text,
    "lextor_get_text": t_get_text,
    "lextor_list_projects": t_list_projects,
    "lextor_list_texts": t_list_texts,
    "lextor_create_project": t_create_project,
}


WRITE_TOOLS = {"lextor_create_text", "lextor_append_blocks", "lextor_update_text", "lextor_create_project"}


def call_tool(ctx: CallContext, name: str, args: dict) -> dict:
    fn = IMPLEMENTATIONS.get(name)
    if fn is None:
        raise KeyError(name)
    from app.config import settings
    if ctx.anonymous and name in WRITE_TOOLS and not settings.mcp_allow_anonymous:
        return _text_result(
            "Este servidor Lextor solo acepta conexiones personales. El usuario debe añadir su URL "
            f"personal del conector (la obtiene en {ctx.base_url}/connect) en lugar de {ctx.base_url}/mcp.",
            is_error=True)
    try:
        return fn(ctx, args or {})
    except ToolError as e:
        return _text_result(str(e), is_error=True)
    except (ValueError, json.JSONDecodeError) as e:
        return _text_result(f"Error: {e}", is_error=True)


# ──────────────────────────────────────────────────────── prompts ──

PROMPTS = [
    {
        "name": "bilingual_text",
        "title": "Crear texto bilingüe",
        "description": "Turn a text into an interactive Lextor reader with translations and notes.",
        "arguments": [
            {"name": "text", "description": "The text to study (paste it)", "required": True},
            {"name": "text_lang", "description": "Language of the text (e.g. fr)", "required": False},
            {"name": "notes_lang", "description": "Your language for translations and notes (e.g. es)",
             "required": False},
            {"name": "level", "description": "Your level: A1–C2", "required": False},
        ],
    },
    {
        "name": "graded_reader",
        "title": "Escribir una lectura graduada",
        "description": "Write an original text on a topic at your level and open it in Lextor.",
        "arguments": [
            {"name": "topic", "description": "What the text should be about", "required": True},
            {"name": "text_lang", "description": "Language you are learning (e.g. ja)", "required": True},
            {"name": "notes_lang", "description": "Your language (e.g. es)", "required": False},
            {"name": "level", "description": "A1–C2", "required": False},
        ],
    },
]


def get_prompt(name: str, args: dict) -> dict:
    args = args or {}
    lvl = f" Mi nivel es {args['level']}." if args.get("level") else ""
    langs = ""
    if args.get("text_lang"):
        langs += f" El texto está en «{args['text_lang']}»."
    if args.get("notes_lang"):
        langs += f" Quiero las traducciones y notas en «{args['notes_lang']}»."
    if name == "bilingual_text":
        text = (f"Convierte este texto en un texto bilingüe interactivo de Lextor.{langs}{lvl} "
                "Primero consulta lextor_get_format_guide, después guárdalo con lextor_create_text "
                "y dame el enlace.\n\n---\n" + (args.get("text") or ""))
    elif name == "graded_reader":
        text = (f"Escribe un texto original sobre «{args.get('topic', '')}» en «{args.get('text_lang', '')}»"
                f"{' con notas en «' + args['notes_lang'] + '»' if args.get('notes_lang') else ''}.{lvl} "
                "Adapta vocabulario y gramática al nivel (unas 150–300 palabras). "
                "Consulta lextor_get_format_guide y guárdalo con lextor_create_text.")
    else:
        raise KeyError(name)
    return {"description": next(p["description"] for p in PROMPTS if p["name"] == name),
            "messages": [{"role": "user", "content": {"type": "text", "text": text}}]}


def example_resource() -> str:
    return json.dumps(example_document(), ensure_ascii=False, indent=1)
