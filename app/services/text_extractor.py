"""
text_extractor.py — Extract plain text from PDF, DOCX, TXT, MD.

Returns the full text as a single string, preserving paragraph breaks
and verse/line formatting. The LLM agent handles the structural splitting.
"""

from __future__ import annotations

import io
import re
from pathlib import Path


def extract_text(filename: str, content: bytes) -> str:
    """Extract text from a file, preserving paragraph and line structure."""
    ext = Path(filename).suffix.lower()
    if ext == ".pdf":
        return _from_pdf(content)
    elif ext in (".docx", ".doc"):
        return _from_docx(content)
    elif ext in (".txt", ".md"):
        return _from_txt(content)
    else:
        return _from_txt(content)


# Keep backward-compatible name for any callers
def extract_blocks(filename: str, content: bytes) -> list[dict]:
    """Legacy wrapper — returns a single block with all extracted text."""
    text = extract_text(filename, content)
    if not text.strip():
        return []
    return [{"type": "paragraph", "text": text}]


# ── PDF ──────────────────────────────────────────────────────────

def _from_pdf(content: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError:
        raise ImportError("pypdf is required for PDF extraction. Run: pip install pypdf")

    reader = PdfReader(io.BytesIO(content))
    pages: list[str] = []
    for page in reader.pages:
        text = page.extract_text() or ""
        if text.strip():
            pages.append(text.strip())
    return "\n\n".join(pages)


# ── DOCX ─────────────────────────────────────────────────────────

def _from_docx(content: bytes) -> str:
    try:
        import docx
    except ImportError:
        raise ImportError("python-docx is required for DOCX extraction. Run: pip install python-docx")

    doc = docx.Document(io.BytesIO(content))
    parts: list[str] = []
    for para in doc.paragraphs:
        text = para.text
        if not text.strip():
            # Preserve blank lines as paragraph separators
            parts.append("")
            continue
        style = para.style.name.lower() if para.style else ""
        if "heading" in style:
            try:
                level = int(style.split()[-1])
            except (ValueError, IndexError):
                level = 1
            parts.append("#" * min(level, 3) + " " + text.strip())
        else:
            parts.append(text)
    return "\n".join(parts)


# ── TXT / MD ─────────────────────────────────────────────────────

def _from_txt(content: bytes) -> str:
    return content.decode("utf-8", errors="replace").strip()
