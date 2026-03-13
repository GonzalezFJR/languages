"""
text_extractor.py — Extract structured text blocks from PDF, DOCX, TXT, MD.

Returns a list of dicts: {type: "heading"|"paragraph", text: str, level?: int}
"""

from __future__ import annotations

import io
import re
from pathlib import Path


def extract_blocks(filename: str, content: bytes) -> list[dict]:
    ext = Path(filename).suffix.lower()
    if ext == ".pdf":
        return _from_pdf(content)
    elif ext in (".docx", ".doc"):
        return _from_docx(content)
    elif ext in (".txt", ".md"):
        return _from_txt(content)
    else:
        # Fallback: try plain text
        return _from_txt(content)


# ── PDF ──────────────────────────────────────────────────────────

def _from_pdf(content: bytes) -> list[dict]:
    try:
        from pypdf import PdfReader
    except ImportError:
        raise ImportError("pypdf is required for PDF extraction. Run: pip install pypdf")

    reader = PdfReader(io.BytesIO(content))
    blocks: list[dict] = []
    for page in reader.pages:
        text = page.extract_text() or ""
        for para in _split_paragraphs(text):
            if para:
                blocks.append({"type": "paragraph", "text": para})
    return _merge_short_blocks(blocks)


# ── DOCX ─────────────────────────────────────────────────────────

def _from_docx(content: bytes) -> list[dict]:
    try:
        import docx
    except ImportError:
        raise ImportError("python-docx is required for DOCX extraction. Run: pip install python-docx")

    doc = docx.Document(io.BytesIO(content))
    blocks: list[dict] = []
    for para in doc.paragraphs:
        text = para.text.strip()
        if not text:
            continue
        style = para.style.name.lower() if para.style else ""
        if "heading" in style:
            try:
                level = int(style.split()[-1])
            except (ValueError, IndexError):
                level = 1
            blocks.append({"type": "heading", "text": text, "level": min(level, 3)})
        else:
            blocks.append({"type": "paragraph", "text": text})
    return blocks


# ── TXT / MD ─────────────────────────────────────────────────────

def _from_txt(content: bytes) -> list[dict]:
    text = content.decode("utf-8", errors="replace")
    blocks: list[dict] = []
    for chunk in re.split(r"\n{2,}", text):
        chunk = chunk.strip()
        if not chunk:
            continue
        # Markdown heading
        m = re.match(r"^(#{1,3})\s+(.*)", chunk)
        if m:
            level = len(m.group(1))
            blocks.append({"type": "heading", "text": m.group(2).strip(), "level": level})
        else:
            # Collapse internal newlines to space
            flat = re.sub(r"\s*\n\s*", " ", chunk)
            blocks.append({"type": "paragraph", "text": flat})
    return blocks


# ── Helpers ──────────────────────────────────────────────────────

def _split_paragraphs(text: str) -> list[str]:
    """Split PDF page text into paragraphs."""
    paras = re.split(r"\n{2,}", text)
    result = []
    for p in paras:
        flat = re.sub(r"\s*\n\s*", " ", p).strip()
        if flat:
            result.append(flat)
    return result


def _merge_short_blocks(blocks: list[dict], min_words: int = 4) -> list[dict]:
    """Merge very short consecutive paragraph blocks (PDF artifacts)."""
    result: list[dict] = []
    for b in blocks:
        if (
            result
            and b["type"] == "paragraph"
            and result[-1]["type"] == "paragraph"
            and len(b["text"].split()) < min_words
        ):
            result[-1]["text"] += " " + b["text"]
        else:
            result.append(b)
    return result
