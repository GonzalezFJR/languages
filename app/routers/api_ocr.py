"""
api_ocr.py — OCR-to-text endpoint.

POST /api/projects/{pid}/pipeline/ocr
  Receives an image, runs PaddleOCR, returns extracted text.
  The frontend then decides whether to proceed with the .xlan
  agent pipeline (if LLM API key is configured) or show an error.
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException, UploadFile, File, Form, Request

from app.config import settings
from app.services.project_service import load_metadata
from app.services.ocr_service import extract_text_from_image

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/projects", tags=["ocr"])

_ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tiff", ".tif"}
_MAX_SIZE = 20 * 1024 * 1024  # 20 MB


@router.post("/{project_id}/pipeline/ocr")
async def ocr_extract(
    request: Request,
    project_id: str,
    file: UploadFile = File(...),
    title: str = Form(""),
    description: str = Form(""),
):
    """Run OCR on an uploaded image and return the extracted text."""
    ud = request.state.user_dir
    meta = load_metadata(project_id, ud)
    if not meta:
        raise HTTPException(404, "Proyecto no encontrado")

    ext = Path(file.filename or "").suffix.lower()
    if ext not in _ALLOWED_EXTENSIONS:
        raise HTTPException(
            400,
            f"Formato no soportado. Formatos válidos: {', '.join(sorted(_ALLOWED_EXTENSIONS))}",
        )

    content = await file.read()
    if not content:
        raise HTTPException(400, "La imagen está vacía")
    if len(content) > _MAX_SIZE:
        raise HTTPException(400, "La imagen es demasiado grande (máximo 20 MB)")

    target_lang = meta.get("target", "en")
    try:
        text = extract_text_from_image(content, lang=target_lang)
    except ImportError:
        raise HTTPException(
            500,
            "PaddleOCR no está instalado. Ejecute: pip install paddleocr paddlepaddle",
        )
    except Exception as exc:
        logger.exception("OCR processing failed")
        raise HTTPException(500, f"Error al ejecutar OCR: {exc}")

    if not text.strip():
        raise HTTPException(400, "No se pudo extraer texto de la imagen")

    # Check if LLM pipeline is available
    api_key = (settings.llm_api_key or "").strip()
    pipeline_available = bool(api_key and api_key != "sk-...")

    # Always log OCR text to server console for debugging
    print(f"\n{'='*60}")
    print(f"OCR TEXT — project: {project_id}")
    print(f"{'='*60}")
    print(text)
    print(f"{'='*60}\n")

    return {
        "ok": True,
        "text": text,
        "pipeline_available": pipeline_available,
    }
