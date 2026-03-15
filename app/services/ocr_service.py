"""
ocr_service.py — OCR pipeline module using PaddleOCR (PP-OCRv5).

Receives image bytes, runs OCR, returns extracted plain text
ready to be fed into the .xlan creation pipeline.
"""

from __future__ import annotations

import os
import re
import tempfile

# Mapping from ISO 639-1 codes (used in projects) to PaddleOCR lang codes
_LANG_MAP = {
    "es": "es",
    "en": "en",
    "de": "de",
    "fr": "fr",
    "it": "it",
    "pt": "pt",
    "ru": "ru",
    "zh": "ch",
    "ja": "ja",
    "ar": "ar",
    "ko": "ko",
}

# Cache OCR instances per language to avoid reloading models
_ocr_cache: dict = {}


def _get_ocr_instance(lang: str):
    """Return a cached PaddleOCR instance for the given language."""
    paddle_lang = _LANG_MAP.get(lang, "en")
    if paddle_lang not in _ocr_cache:
        os.environ["PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"] = "True"
        from paddleocr import PaddleOCR

        _ocr_cache[paddle_lang] = PaddleOCR(lang=paddle_lang)
    return _ocr_cache[paddle_lang]


def extract_text_from_image(image_bytes: bytes, lang: str = "en") -> str:
    """
    Run OCR on image bytes and return the extracted text.

    Args:
        image_bytes: Raw bytes of the image (JPEG, PNG, etc.).
        lang: ISO 639-1 code of the text language in the image.

    Returns:
        Extracted text as a single string, with lines joined.
        Empty string if no text is detected.
    """
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
        tmp.write(image_bytes)
        tmp_path = tmp.name

    try:
        ocr = _get_ocr_instance(lang)
        results = list(ocr.predict(tmp_path))

        if not results:
            return ""

        r = results[0]
        texts = r["rec_texts"]
        scores = r["rec_scores"]
        polys = r["dt_polys"]

        if not texts:
            return ""

        # Build (y_center, x_left, height, text) for each detection
        fragments: list[tuple[float, float, float, str]] = []
        for text, score, poly in zip(texts, scores, polys):
            if score < 0.8:
                continue
            stripped = text.strip()
            if not stripped:
                continue
            y_min = min(pt[1] for pt in poly)
            y_max = max(pt[1] for pt in poly)
            x_left = min(pt[0] for pt in poly)
            y_center = (y_min + y_max) / 2
            height = y_max - y_min
            fragments.append((y_center, x_left, height, stripped))

        if not fragments:
            return ""

        # Group fragments into logical lines by Y proximity
        fragments.sort(key=lambda f: (f[0], f[1]))
        avg_height = sum(f[2] for f in fragments) / len(fragments)
        threshold = avg_height * 0.5

        lines: list[list[tuple[float, float, str]]] = []  # [(y_center, x_left, text), ...]
        for y_center, x_left, _h, text in fragments:
            placed = False
            for line in lines:
                line_y = sum(y for y, _, _ in line) / len(line)
                if abs(y_center - line_y) < threshold:
                    line.append((y_center, x_left, text))
                    placed = True
                    break
            if not placed:
                lines.append([(y_center, x_left, text)])

        # Sort lines top-to-bottom, fragments within each line left-to-right
        def line_y(line):
            return sum(y for y, _, _ in line) / len(line)

        lines.sort(key=line_y)
        result_lines = []
        for line in lines:
            line.sort(key=lambda f: f[1])  # sort by x_left
            result_lines.append(" ".join(t for _, _, t in line))

        text = "\n".join(result_lines)

        # Re-join words broken by end-of-line hyphens
        text = re.sub(r"(\w)- *\n(\w)", r"\1\2", text)
        text = re.sub(r"(\w)-\n(\w)", r"\1\2", text)

        return text

    finally:
        os.unlink(tmp_path)
