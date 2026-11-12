"""Conversion from the legacy .xlan format to lextor/1.

Legacy segments carried leading spaces and trailing "\\n" inside their text. Here:
  * "\\n" inside a block turns the block into a `verse` block (one sentence per line);
  * "\\n\\n" splits blocks (stanza / paragraph break);
  * other paragraphs are grouped into sentences at terminal punctuation;
  * each legacy segment becomes one chunk (text stripped), and user notes become annotations.
"""

from __future__ import annotations

import re

_SENT_END = re.compile(r"[.!?…。！？]['\"”»’)\]]*$")
_PENDING = "[pendiente de revisión]"


def xlan_to_lextor(xlan: dict) -> dict:
    meta_in = xlan.get("meta", {}) or {}
    meta = {
        "title": (meta_in.get("title") or "").strip() or "Sin título",
        "text_lang": meta_in.get("text_language") or meta_in.get("source_language") or "en",
        "notes_lang": meta_in.get("notes_language") or meta_in.get("target_language") or "es",
    }
    # Old projects sometimes swapped the keys; best effort only.
    if meta_in.get("description"):
        meta["summary"] = meta_in["description"].strip()
    if meta_in.get("created_at"):
        meta["created_at"] = meta_in["created_at"]

    blocks: list[dict] = []
    annotations: dict = {}

    for old in xlan.get("content", []) or []:
        segs = [s for s in (old.get("segments") or []) if (s.get("text") or "").strip()]
        if not segs:
            continue
        if old.get("type") == "heading":
            sent = _make_sentence(segs)
            if sent:
                bi = len(blocks)
                blocks.append({"type": "heading", "level": int(old.get("level", 1) or 1), "sentences": [sent]})
                _collect_notes(segs, bi, 0, annotations)
                _block_note(old, bi, annotations)
            continue

        # Split into stanzas on blank lines, lines on single newlines
        stanzas: list[list[list[dict]]] = [[[]]]
        for seg in segs:
            stanzas[-1][-1].append(seg)
            raw = seg.get("text", "")
            n = raw.rstrip(" ").count("\n", len(raw.rstrip(" \n")))  # trailing newlines only
            if n >= 2:
                stanzas.append([[]])
            elif n == 1:
                stanzas[-1].append([])
        first_block = len(blocks)
        for stanza in stanzas:
            lines = [l for l in stanza if l]
            if not lines:
                continue
            bi = len(blocks)
            if len(lines) > 1:
                sentences = []
                for line in lines:
                    s = _make_sentence(line)
                    if s:
                        _collect_notes(line, bi, len(sentences), annotations)
                        sentences.append(s)
                if sentences:
                    blocks.append({"type": "verse", "sentences": sentences})
            else:
                groups = _group_sentences(lines[0])
                sentences = []
                for g in groups:
                    s = _make_sentence(g)
                    if s:
                        _collect_notes(g, bi, len(sentences), annotations)
                        sentences.append(s)
                if sentences:
                    blocks.append({"type": "paragraph", "sentences": sentences})
        if len(blocks) > first_block:
            _block_note(old, first_block, annotations)

    doc = {"format": "lextor/1", "meta": meta, "blocks": blocks}
    if annotations:
        doc["annotations"] = annotations
    return doc


def _group_sentences(segs: list[dict]) -> list[list[dict]]:
    groups: list[list[dict]] = [[]]
    for seg in segs:
        groups[-1].append(seg)
        if _SENT_END.search(seg.get("text", "").strip()):
            groups.append([])
    return [g for g in groups if g]


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").replace("\n", " ")).strip()


def _make_sentence(segs: list[dict]) -> dict | None:
    text = _clean("".join(s.get("text", "") for s in segs))
    if not text:
        return None
    chunks = []
    for s in segs:
        t = _clean(s.get("text", ""))
        if not t:
            continue
        c = {"text": t, "gloss": _clean(s.get("translation", "")) or t}
        info = _clean(s.get("info", ""))
        if info and info != _PENDING:
            c["note"] = info
        chunks.append(c)
    translation = _clean(" ".join(_clean(s.get("translation", "")) for s in segs))
    return {"text": text, "translation": translation or text, "chunks": chunks}


def _note_to_annotation(note: dict) -> dict | None:
    if not note:
        return None
    ann = {}
    if note.get("highlight_color"):
        ann["color"] = note["highlight_color"]
    if note.get("comment"):
        ann["comment"] = note["comment"]
    return ann or None


def _collect_notes(segs: list[dict], bi: int, si: int, annotations: dict) -> None:
    ci = 0
    for s in segs:
        if not _clean(s.get("text", "")):
            continue
        ann = _note_to_annotation(s.get("user_note") or {})
        if ann:
            annotations[f"b{bi}.s{si}.c{ci}"] = ann
        ci += 1


def _block_note(old_block: dict, bi: int, annotations: dict) -> None:
    ann = _note_to_annotation(old_block.get("user_note") or {})
    key = f"b{bi}.s0.c0"
    if ann and key not in annotations:
        annotations[key] = ann
