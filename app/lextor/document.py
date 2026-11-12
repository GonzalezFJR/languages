"""
Lextor document format ("lextor/1"): normalization + validation.

A Lextor document is plain JSON:

{
  "format": "lextor/1",
  "meta":   {"title", "title_translation"?, "text_lang", "notes_lang", "level"?, "summary"?, "author"?, "source"?},
  "blocks": [
    {"type": "heading" | "paragraph" | "verse", "level"?: 1-3,
     "sentences": [
        {"text": "<exact original sentence/line>",
         "translation": "<natural translation>",
         "note"?: "<sentence-level note>",
         "chunks": [
            {"text": "<exact substring of sentence.text>",
             "gloss": "<contextual translation>",
             "note"?: "...", "translit"?: "...", "reading"?: "...",
             "lemma"?: "...", "grammar"?: "...", "literal"?: "...", "group"?: "g1"}
         ]}
     ]}
  ],
  "annotations"?: {"b0.s1.c2": {"color": "yellow", "comment": "..."}}   # user data, never written by models
}

Design rules (enforced here, explained to models in guide.md):
  * Models never write whitespace or line breaks between pieces. Chunks are exact substrings of their
    sentence, in order; spacing is reconstructed from the sentence text.
  * Line structure is expressed with blocks: a `verse` block renders each sentence on its own line.
  * IDs are positional and computed (b{block}.s{sentence}.c{chunk}).
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

from app.lextor import languages

FORMAT_VERSION = "lextor/1"

BLOCK_TYPES = ("heading", "paragraph", "verse")
_BLOCK_ALIASES = {
    "title": "heading", "header": "heading", "h1": "heading", "h2": "heading", "h3": "heading",
    "p": "paragraph", "prose": "paragraph", "quote": "paragraph", "dialogue": "paragraph", "dialog": "paragraph",
    "poem": "verse", "poetry": "verse", "lines": "verse", "stanza": "verse", "lyrics": "verse", "list": "verse",
}
LEVELS = ("A1", "A2", "B1", "B2", "C1", "C2")
META_KEYS = ("title", "title_translation", "text_lang", "notes_lang", "level", "summary", "author", "source",
             "created_at", "updated_at")
CHUNK_OPTIONAL = ("note", "translit", "reading", "lemma", "grammar", "literal", "group")

MAX_CHUNK_WORDS = 6
MAX_CHUNK_CHARS_UNSPACED = 8
MAX_SENTENCE_WORDS = 45
MAX_BLOCKS = 3000
MAX_TOTAL_CHARS = 400_000

CHECKS: dict[str, str] = {
    "schema": "La estructura JSON y los campos obligatorios son correctos",
    "languages": "Los códigos de idioma son válidos y distintos",
    "no_line_breaks": "Ningún campo contiene saltos de línea ni tabuladores (la estructura va en bloques)",
    "chunk_alignment": "Cada fragmento es una subcadena exacta de su frase, en orden y sin solaparse",
    "coverage": "Todas las palabras de cada frase pertenecen a algún fragmento",
    "granularity": "Los fragmentos son unidades mínimas de sentido (≤6 palabras) y las frases largas están divididas",
    "translations": "Cada frase tiene traducción natural y cada fragmento una glosa única (sin alternativas «a / b»)",
    "notes": "La mayoría de fragmentos llevan una nota didáctica",
    "transliteration": "Los textos en escritura no latina incluyen transliteración",
    "plain_text": "Sin Markdown ni HTML dentro de los campos",
    "groups": "Los grupos (`group`) enlazan al menos dos fragmentos de la misma frase",
}

_MARKUP_RE = re.compile(r"(\*\*|__|`|</?[a-zA-Z][^>]{0,40}>|^#{1,6}\s)")
_ALT_RE = re.compile(r"\S\s+/\s+\S")
_WORD_RE = re.compile(r"[^\W_]+(?:['’\-][^\W_]+)*", re.UNICODE)


# ─────────────────────────────────────────────────────────────── report ──

@dataclass
class Issue:
    code: str
    check: str
    path: str
    message: str
    hint: str = ""

    def to_dict(self) -> dict:
        d = {"code": self.code, "check": self.check, "path": self.path, "message": self.message}
        if self.hint:
            d["hint"] = self.hint
        return d


@dataclass
class Report:
    doc: dict | None = None
    errors: list[Issue] = field(default_factory=list)
    warnings: list[Issue] = field(default_factory=list)
    fixes: list[Issue] = field(default_factory=list)
    stats: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.errors

    # helpers used by the validator
    def error(self, check, code, path, message, hint=""):
        self.errors.append(Issue(code, check, path, message, hint))

    def warn(self, check, code, path, message, hint=""):
        self.warnings.append(Issue(code, check, path, message, hint))

    def fix(self, check, code, path, message):
        self.fixes.append(Issue(code, check, path, message))

    def checks(self) -> list[dict]:
        out = []
        for name, desc in CHECKS.items():
            e = sum(1 for i in self.errors if i.check == name)
            w = sum(1 for i in self.warnings if i.check == name)
            f = sum(1 for i in self.fixes if i.check == name)
            status = "fail" if e else ("warn" if w else "pass")
            out.append({"name": name, "description": desc, "status": status,
                        "errors": e, "warnings": w, "auto_fixed": f})
        return out

    def to_dict(self, include_doc: bool = False) -> dict:
        d = {
            "ok": self.ok,
            "summary": self.summary(),
            "checks": self.checks(),
            "errors": [i.to_dict() for i in self.errors],
            "warnings": [i.to_dict() for i in self.warnings],
            "auto_fixed": [i.to_dict() for i in self.fixes],
            "stats": self.stats,
        }
        if include_doc:
            d["document"] = self.doc
        return d

    def summary(self) -> str:
        checks = self.checks()
        passed = sum(1 for c in checks if c["status"] == "pass")
        verdict = "OK" if self.ok else "FALLA"
        return (f"{verdict}: {passed}/{len(checks)} tests superados, {len(self.errors)} errores, "
                f"{len(self.warnings)} avisos, {len(self.fixes)} correcciones automáticas")

    def as_text(self, limit: int = 40) -> str:
        """Compact, model-friendly rendering of the report."""
        lines = [f"Validación Lextor — {self.summary()}"]
        for c in self.checks():
            mark = {"pass": "✓", "warn": "!", "fail": "✗"}[c["status"]]
            lines.append(f"  {mark} {c['name']}: {c['description']}")
        budget = limit
        for title, items in (("ERRORES (hay que corregirlos)", self.errors),
                             ("AVISOS (mejoran la calidad)", self.warnings),
                             ("CORREGIDO AUTOMÁTICAMENTE", self.fixes)):
            if not items:
                continue
            lines.append(f"\n{title}:")
            show = items[:max(budget, 3 if title.startswith("ERRORES") else 0)]
            for i in show:
                hint = f" → {i.hint}" if i.hint else ""
                lines.append(f"  [{i.code}] {i.path}: {i.message}{hint}")
            if len(items) > len(show):
                lines.append(f"  … y {len(items) - len(show)} más")
            budget = max(0, budget - len(show))
        if self.stats:
            s = self.stats
            lines.append(
                f"\nEstadísticas: {s.get('blocks', 0)} bloques, {s.get('sentences', 0)} frases, "
                f"{s.get('chunks', 0)} fragmentos, {s.get('words', 0)} palabras, "
                f"{s.get('avg_words_per_chunk', 0)} palabras/fragmento, "
                f"{s.get('notes_pct', 0)}% con nota")
        return "\n".join(lines)


# ───────────────────────────────────────────────────────────── helpers ──

def _is_word_char(ch: str) -> bool:
    return unicodedata.category(ch)[0] in "LNM"


def _word_count(text: str, spaced: bool) -> int:
    if spaced:
        return len(_WORD_RE.findall(text))
    return sum(1 for ch in text if _is_word_char(ch))


def _has_non_latin_letters(text: str) -> bool:
    for ch in text:
        if unicodedata.category(ch).startswith("L"):
            try:
                if "LATIN" not in unicodedata.name(ch):
                    return True
            except ValueError:
                return True
    return False


_CHAR_FOLD = {
    "’": "'", "‘": "'", "ʼ": "'", "´": "'", "`": "'",
    "“": '"', "”": '"', "„": '"', "«": '"', "»": '"', "‹": '"', "›": '"',
    "–": "-", "—": "-", "‑": "-", "−": "-",
    " ": " ", " ": " ", " ": " ", "\t": " ", "\n": " ", "\r": " ",
    "…": ".",
}


def _fold(text: str) -> tuple[str, list[int]]:
    """Return a case/punctuation-folded version of text and a map folded_index -> original_index.
    Whitespace runs collapse to a single space."""
    out: list[str] = []
    idx: list[int] = []
    prev_space = False
    for i, ch in enumerate(text):
        c = _CHAR_FOLD.get(ch, ch)
        if c.isspace():
            if prev_space:
                continue
            c = " "
            prev_space = True
        else:
            prev_space = False
        c = c.lower()
        out.append(c)
        idx.append(i)
    return "".join(out), idx


def _find_chunk(sentence: str, chunk: str, start: int) -> tuple[int, int, bool] | None:
    """Locate chunk in sentence at/after start. Returns (begin, end, exact) or None."""
    i = sentence.find(chunk, start)
    if i != -1:
        return i, i + len(chunk), True
    fs, fmap = _fold(sentence)
    fc, _ = _fold(chunk)
    fc = fc.strip()
    if not fc:
        return None
    # translate `start` into folded coordinates
    fstart = next((k for k, orig in enumerate(fmap) if orig >= start), len(fs))
    j = fs.find(fc, fstart)
    if j == -1:
        return None
    begin = fmap[j]
    end = fmap[j + len(fc) - 1] + 1
    return begin, end, False


def _clean_str(value: Any) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        value = str(value)
    return value


def _strip_breaks(value: str) -> tuple[str, bool]:
    if any(c in value for c in "\n\r\t"):
        return re.sub(r"\s*[\n\r\t]+\s*", " ", value).strip(), True
    return value, False


# ─────────────────────────────────────────────────────────── validator ──

def parse_input(data: Any) -> Any:
    """Accept a dict or a JSON string (some clients stringify tool arguments)."""
    if isinstance(data, (bytes, bytearray)):
        data = data.decode("utf-8", errors="replace")
    if isinstance(data, str):
        s = data.strip()
        m = re.match(r"^```(?:json)?\s*(.*?)\s*```$", s, re.S)
        if m:
            s = m.group(1)
        return json.loads(s)
    return data


def validate_document(data: Any, *, defaults: dict | None = None) -> Report:
    """Normalize + validate a full document. `defaults` may supply meta values (e.g. project languages)."""
    rep = Report()
    try:
        data = parse_input(data)
    except (json.JSONDecodeError, ValueError) as e:
        rep.error("schema", "E_JSON", "$", f"JSON inválido: {e}")
        return rep
    if not isinstance(data, dict):
        rep.error("schema", "E_TYPE", "$", "El documento debe ser un objeto JSON")
        return rep

    # Legacy .xlan input → convert transparently
    if "blocks" not in data and isinstance(data.get("content"), list):
        from app.lextor.legacy import xlan_to_lextor
        data = xlan_to_lextor(data)
        rep.fix("schema", "F_LEGACY", "$", "Documento .xlan antiguo convertido a lextor/1")

    meta = _validate_meta(data.get("meta"), defaults or {}, rep)
    if meta is None:
        return rep
    blocks = validate_blocks(data.get("blocks"), meta["text_lang"], meta["notes_lang"], rep, path="blocks")

    doc = {"format": FORMAT_VERSION, "meta": meta, "blocks": blocks}
    ann = data.get("annotations")
    if isinstance(ann, dict) and ann:
        doc["annotations"] = ann
    if not meta.get("title"):
        first = next((b for b in blocks if b["type"] == "heading"), None)
        meta["title"] = first["sentences"][0]["text"] if first and first["sentences"] else "Sin título"
        rep.warn("schema", "W_NO_TITLE", "meta.title", "Falta el título; se ha deducido uno",
                 "Incluye meta.title")
    rep.doc = doc
    rep.stats = compute_stats(doc)
    _doc_level_checks(doc, rep)
    return rep


def validate_blocks(raw_blocks: Any, text_lang: str, notes_lang: str, rep: Report, *, path: str = "blocks",
                    block_offset: int = 0) -> list[dict]:
    """Normalize + validate a list of blocks (also used by the incremental `append` flow)."""
    if not isinstance(raw_blocks, list) or not raw_blocks:
        rep.error("schema", "E_NO_BLOCKS", path, "`blocks` debe ser una lista no vacía",
                  "Divide el texto en bloques heading/paragraph/verse")
        return []
    if len(raw_blocks) > MAX_BLOCKS:
        rep.error("schema", "E_TOO_LARGE", path, f"Demasiados bloques ({len(raw_blocks)} > {MAX_BLOCKS})",
                  "Usa lextor_append_blocks en varias llamadas")
        return []
    lang = languages.get(text_lang)
    out = []
    for bi, raw in enumerate(raw_blocks):
        b = _validate_block(raw, f"{path}[{bi}]", lang, rep)
        if b is not None:
            out.append(b)
    total = sum(len(s["text"]) for b in out for s in b["sentences"])
    if total > MAX_TOTAL_CHARS:
        rep.error("schema", "E_TOO_LARGE", path, f"Texto demasiado largo ({total} caracteres)")
    return out


def _validate_meta(raw: Any, defaults: dict, rep: Report) -> dict | None:
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        rep.error("schema", "E_TYPE", "meta", "`meta` debe ser un objeto")
        return None
    meta: dict = {}
    for k in META_KEYS:
        v = raw.get(k, defaults.get(k))
        if v is None or v == "":
            continue
        v = _clean_str(v).strip()
        v, had = _strip_breaks(v)
        meta[k] = v
    # legacy key names
    for old, new in (("text_language", "text_lang"), ("notes_language", "notes_lang"),
                     ("source_lang", "text_lang"), ("target_lang", "notes_lang"),
                     ("description", "summary")):
        if new not in meta and raw.get(old):
            meta[new] = _clean_str(raw[old]).strip()
            rep.fix("schema", "F_META_KEY", f"meta.{old}", f"Renombrado a meta.{new}")
    for k in ("text_lang", "notes_lang"):
        if not meta.get(k):
            rep.error("languages", "E_LANG_MISSING", f"meta.{k}", f"Falta meta.{k}",
                      "Código ISO 639-1, p. ej. 'fr' o 'es'")
        elif not languages.is_valid_code(meta[k].lower()):
            rep.error("languages", "E_LANG_INVALID", f"meta.{k}", f"Código de idioma no válido: {meta[k]!r}",
                      "Usa un código ISO 639-1 de dos letras")
        else:
            meta[k] = meta[k].lower()
    if rep.errors:
        return None
    if meta["text_lang"] == meta["notes_lang"]:
        rep.error("languages", "E_LANG_SAME", "meta", "text_lang y notes_lang no pueden ser iguales")
        return None
    if "level" in meta:
        lv = meta["level"].upper()[:2]
        if lv in LEVELS:
            meta["level"] = lv
        else:
            rep.warn("schema", "W_LEVEL", "meta.level", f"Nivel desconocido {meta['level']!r}; se ignora",
                     "Usa A1, A2, B1, B2, C1 o C2")
            meta.pop("level")
    return meta


def _validate_block(raw: Any, path: str, lang: languages.Language, rep: Report) -> dict | None:
    if not isinstance(raw, dict):
        rep.error("schema", "E_TYPE", path, "Cada bloque debe ser un objeto")
        return None
    btype = _clean_str(raw.get("type") or "paragraph").strip().lower()
    if btype not in BLOCK_TYPES:
        if btype in _BLOCK_ALIASES:
            rep.fix("schema", "F_BLOCK_TYPE", f"{path}.type", f"Tipo «{btype}» → «{_BLOCK_ALIASES[btype]}»")
            btype = _BLOCK_ALIASES[btype]
        else:
            rep.error("schema", "E_BLOCK_TYPE", f"{path}.type", f"Tipo de bloque desconocido: {btype!r}",
                      "Usa heading, paragraph o verse")
            return None
    block: dict = {"type": btype}
    if btype == "heading":
        try:
            level = int(raw.get("level", 1))
        except (TypeError, ValueError):
            level = 1
        block["level"] = max(1, min(3, level))
    sentences_raw = raw.get("sentences")
    if sentences_raw is None and isinstance(raw.get("lines"), list):
        sentences_raw = raw["lines"]
    if not isinstance(sentences_raw, list) or not sentences_raw:
        rep.error("schema", "E_NO_SENTENCES", f"{path}.sentences", "El bloque no tiene frases",
                  "Cada bloque necesita `sentences`: [{text, translation, chunks}]")
        return None
    block["sentences"] = []
    for si, s in enumerate(sentences_raw):
        sent = _validate_sentence(s, f"{path}.sentences[{si}]", lang, rep)
        if sent is not None:
            block["sentences"].append(sent)
    return block if block["sentences"] else None


def _validate_sentence(raw: Any, path: str, lang: languages.Language, rep: Report) -> dict | None:
    if not isinstance(raw, dict):
        rep.error("schema", "E_TYPE", path, "Cada frase debe ser un objeto {text, translation, chunks}")
        return None
    text = _clean_str(raw.get("text"))
    text, had_break = _strip_breaks(text)
    if had_break:
        rep.warn("no_line_breaks", "W_NEWLINE", f"{path}.text",
                 "La frase contenía saltos de línea; se han sustituido por espacios",
                 "Si eran versos, usa un bloque `verse` con una frase por línea")
    text = text.strip()
    if not text:
        rep.error("schema", "E_EMPTY_TEXT", f"{path}.text", "Frase vacía")
        return None

    sent: dict = {"text": text}
    translation = _clean_str(raw.get("translation")).strip()
    translation, had_break = _strip_breaks(translation)
    if had_break:
        rep.fix("no_line_breaks", "F_NEWLINE", f"{path}.translation", "Saltos de línea eliminados")

    chunks_raw = raw.get("chunks")
    if not isinstance(chunks_raw, list) or not chunks_raw:
        rep.error("schema", "E_NO_CHUNKS", f"{path}.chunks", f"La frase «{_short(text)}» no tiene fragmentos",
                  "Divide la frase en fragmentos {text, gloss, note}")
        return None

    chunks: list[dict] = []
    pos = 0
    covered = [False] * len(text)
    for ci, c in enumerate(chunks_raw):
        cpath = f"{path}.chunks[{ci}]"
        if isinstance(c, (list, tuple)) and 2 <= len(c) <= 3 and all(isinstance(x, str) for x in c):
            c = {"text": c[0], "gloss": c[1], **({"note": c[2]} if len(c) == 3 else {})}
            rep.fix("schema", "F_TUPLE", cpath, "Tupla convertida a objeto {text, gloss, note}")
        if not isinstance(c, dict):
            rep.error("schema", "E_TYPE", cpath, "Cada fragmento debe ser un objeto {text, gloss, note}")
            continue
        ctext_raw = _clean_str(c.get("text"))
        ctext, had_break = _strip_breaks(ctext_raw)
        if had_break:
            rep.warn("no_line_breaks", "W_NEWLINE", f"{cpath}.text", "El fragmento contenía saltos de línea",
                     "Nunca incluyas \\n: los saltos de línea se expresan con bloques")
        if ctext != ctext.strip():
            rep.fix("chunk_alignment", "F_TRIM", f"{cpath}.text", "Espacios iniciales/finales eliminados")
        ctext = ctext.strip()
        if not ctext:
            rep.error("schema", "E_EMPTY_CHUNK", f"{cpath}.text", "Fragmento vacío")
            continue
        found = _find_chunk(text, ctext, pos)
        if found is None:
            earlier = text.find(ctext)
            hint = ("aparece antes en la frase: los fragmentos deben ir en orden y sin solaparse"
                    if earlier != -1 else "copia el fragmento literalmente desde `text` de la frase")
            rep.error("chunk_alignment", "E_CHUNK_NOT_FOUND", f"{cpath}.text",
                      f"«{ctext}» no está en la frase «{_short(text, 80)}» (a partir del carácter {pos})", hint)
            continue
        begin, end, exact = found
        if not exact:
            actual = text[begin:end]
            rep.fix("chunk_alignment", "F_CHUNK_MATCH", f"{cpath}.text",
                    f"«{ctext}» ajustado al texto exacto «{actual}»")
            ctext = actual
        for k in range(begin, end):
            covered[k] = True
        pos = end

        chunk: dict = {"text": ctext}
        gloss = _clean_str(c.get("gloss", c.get("translation"))).strip()
        gloss, had_break = _strip_breaks(gloss)
        if had_break:
            rep.fix("no_line_breaks", "F_NEWLINE", f"{cpath}.gloss", "Saltos de línea eliminados")
        if not gloss:
            rep.error("translations", "E_NO_GLOSS", f"{cpath}.gloss", f"«{ctext}» no tiene glosa",
                      "Añade la traducción contextual del fragmento")
        elif _ALT_RE.search(gloss):
            rep.warn("translations", "W_ALTERNATIVES", f"{cpath}.gloss",
                     f"La glosa «{gloss}» parece ofrecer alternativas",
                     "Da una única traducción, la que encaja en el contexto; menciona otras en la nota")
        chunk["gloss"] = gloss
        for k in CHUNK_OPTIONAL:
            v = c.get(k)
            if k == "note" and v is None:
                v = c.get("info")
            if v is None or v == "":
                continue
            v, _ = _strip_breaks(_clean_str(v).strip())
            if v:
                chunk[k] = v
        for k in ("gloss", "note"):
            if chunk.get(k) and _MARKUP_RE.search(chunk[k]):
                rep.warn("plain_text", "W_MARKUP", f"{cpath}.{k}", "Contiene Markdown/HTML",
                         "Escribe texto plano; usa «comillas» para citar palabras")
        if lang.script != "latin" and "translit" not in chunk and _has_non_latin_letters(ctext):
            rep.warn("transliteration", "W_NO_TRANSLIT", f"{cpath}.translit",
                     f"«{ctext}» no tiene transliteración",
                     languages.TRANSLIT_HINTS.get(lang.script, "Añade `translit`"))
        n_words = _word_count(ctext, lang.spaced)
        if lang.spaced and n_words > MAX_CHUNK_WORDS:
            rep.warn("granularity", "W_LONG_CHUNK", cpath, f"Fragmento de {n_words} palabras: «{_short(ctext)}»",
                     f"Divídelo en unidades de sentido de ≤{MAX_CHUNK_WORDS} palabras")
        elif not lang.spaced and n_words > MAX_CHUNK_CHARS_UNSPACED:
            rep.warn("granularity", "W_LONG_CHUNK", cpath, f"Fragmento de {n_words} caracteres: «{ctext}»",
                     f"Divídelo en palabras o expresiones (≤{MAX_CHUNK_CHARS_UNSPACED} caracteres)")
        chunks.append(chunk)

    if not chunks:
        return None

    # coverage
    gaps = _uncovered_gaps(text, covered)
    if gaps:
        total_words = _word_count(text, lang.spaced) or 1
        missing = sum(_word_count(g, lang.spaced) for g in gaps)
        gap_txt = ", ".join(f"«{g}»" for g in gaps[:5])
        if missing > 3 or missing / total_words > 0.3:
            rep.error("coverage", "E_UNCOVERED", path, f"Palabras sin fragmento: {gap_txt}",
                      "Cada palabra de la frase debe estar en un fragmento")
        else:
            rep.warn("coverage", "W_UNCOVERED", path, f"Palabras sin fragmento: {gap_txt}",
                     "Se mostrarán sin traducción; añade fragmentos que las cubran")

    words = _word_count(text, lang.spaced)
    if len(chunks) == 1 and ((lang.spaced and words > MAX_CHUNK_WORDS) or
                             (not lang.spaced and words > MAX_CHUNK_CHARS_UNSPACED + 2)):
        rep.warn("granularity", "W_UNSPLIT", path, f"Frase de {words} palabras con un solo fragmento",
                 "Divide la frase en unidades mínimas de sentido")
    if lang.spaced and words > MAX_SENTENCE_WORDS:
        rep.warn("granularity", "W_LONG_SENTENCE", path, f"Frase muy larga ({words} palabras)",
                 "Divide en varias frases para que la vista paralela sea legible")

    if not translation:
        translation = " ".join(c["gloss"] for c in chunks if c["gloss"])
        rep.warn("translations", "W_NO_TRANSLATION", f"{path}.translation",
                 "Falta la traducción natural de la frase; se ha compuesto con las glosas",
                 "Añade `translation` con una traducción fluida de la frase completa")
    elif _MARKUP_RE.search(translation):
        rep.warn("plain_text", "W_MARKUP", f"{path}.translation", "Contiene Markdown/HTML")
    sent["translation"] = translation
    note = _clean_str(raw.get("note")).strip()
    if note:
        sent["note"], _ = _strip_breaks(note)

    # groups
    groups: dict[str, int] = {}
    for c in chunks:
        if "group" in c:
            groups[c["group"]] = groups.get(c["group"], 0) + 1
    for g, n in groups.items():
        if n < 2:
            rep.warn("groups", "W_GROUP_SINGLE", path, f"El grupo {g!r} solo tiene un fragmento",
                     "`group` sirve para enlazar piezas discontinuas (p. ej. ne…pas, verbos separables)")
            for c in chunks:
                if c.get("group") == g:
                    c.pop("group")
    sent["chunks"] = chunks
    return sent


def _uncovered_gaps(text: str, covered: list[bool]) -> list[str]:
    gaps, cur = [], []
    for ch, cov in zip(text, covered):
        if cov:
            if cur:
                gaps.append("".join(cur))
                cur = []
        else:
            cur.append(ch)
    if cur:
        gaps.append("".join(cur))
    return [g.strip() for g in gaps if any(_is_word_char(ch) for ch in g)]


def _doc_level_checks(doc: dict, rep: Report) -> None:
    chunks = [c for b in doc["blocks"] for s in b["sentences"] for c in s["chunks"]]
    if not chunks:
        return
    without = sum(1 for c in chunks if not c.get("note"))
    if without / len(chunks) > 0.4:
        rep.warn("notes", "W_FEW_NOTES", "blocks",
                 f"{without} de {len(chunks)} fragmentos no tienen nota didáctica",
                 "Añade `note` explicando gramática, uso o matices (salvo en palabras triviales)")


def _short(s: str, n: int = 50) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"


# ───────────────────────────────────────────────────────────── utilities ──

def compute_stats(doc: dict) -> dict:
    lang = languages.get(doc.get("meta", {}).get("text_lang", ""))
    blocks = doc.get("blocks", [])
    sentences = [s for b in blocks for s in b.get("sentences", [])]
    chunks = [c for s in sentences for c in s.get("chunks", [])]
    words = sum(_word_count(s["text"], lang.spaced) for s in sentences)
    return {
        "blocks": len(blocks),
        "sentences": len(sentences),
        "chunks": len(chunks),
        "words": words,
        "avg_words_per_chunk": round(words / len(chunks), 2) if chunks else 0,
        "notes_pct": round(100 * sum(1 for c in chunks if c.get("note")) / len(chunks)) if chunks else 0,
        "reading_minutes": max(1, round(words / 150)) if lang.spaced else max(1, round(words / 300)),
    }


def iter_chunks(doc: dict):
    """Yield (chunk_id, block, sentence, chunk)."""
    for bi, b in enumerate(doc.get("blocks", [])):
        for si, s in enumerate(b.get("sentences", [])):
            for ci, c in enumerate(s.get("chunks", [])):
                yield f"b{bi}.s{si}.c{ci}", b, s, c


def plain_text(doc: dict) -> str:
    """Reconstruct the original text (blocks separated by blank lines, verse lines by newlines)."""
    out = []
    for b in doc.get("blocks", []):
        sep = "\n" if b["type"] == "verse" else " "
        out.append(sep.join(s["text"] for s in b.get("sentences", [])))
    return "\n\n".join(out)
