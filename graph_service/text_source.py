"""Document text for extraction: the rag_service extractor, plus OCR for scans.

Uses exactly the extractor the RAG index uses, so page numbers and text agree
between search hits and graph evidence. A PDF without a text layer is OCR'd
page by page (pypdfium2 renders, Tesseract reads, Hungarian + English) when
GRAPH_OCR is not "off" and Tesseract is installed; otherwise it is reported as
ocr_unavailable instead of silently yielding an empty document.
"""
from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import tempfile

_EXTRACT_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                             "rag_service", "extract.py")
_spec = importlib.util.spec_from_file_location("rag_extract", _EXTRACT_PATH)
rag_extract = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rag_extract)

OCR_MODE = os.environ.get("GRAPH_OCR", "auto").strip().lower()
TESSERACT = os.environ.get("GRAPH_TESSERACT", "") or shutil.which("tesseract") or ""
OCR_LANGS = os.environ.get("GRAPH_OCR_LANGS", "hun+eng")
OCR_DPI = int(os.environ.get("GRAPH_OCR_DPI", "200"))


def ocr_available() -> bool:
    if OCR_MODE == "off" or not TESSERACT:
        return False
    return importlib.util.find_spec("pypdfium2") is not None


def _ocr_pdf(raw: bytes) -> list[tuple[int, str]]:
    import pypdfium2

    pages = []
    pdf = pypdfium2.PdfDocument(raw)
    with tempfile.TemporaryDirectory() as tmp:
        for i in range(len(pdf)):
            image = pdf[i].render(scale=OCR_DPI / 72).to_pil().convert("L")
            png = os.path.join(tmp, "p%d.png" % i)
            image.save(png)
            out = subprocess.run([TESSERACT, png, "stdout", "-l", OCR_LANGS],
                                 capture_output=True, timeout=300)
            text = out.stdout.decode("utf-8", "replace").strip()
            if text:
                pages.append((i + 1, text))
    return pages


def document_pages(raw: bytes, path: str) -> tuple[list[tuple[int, str]], str]:
    """([(page, text)], how) with how in text | ocr | empty | ocr_unavailable | unsupported."""
    try:
        pages = [(n, t) for n, _, t in rag_extract.extract_pages(raw, path)]
    except rag_extract.ExtractionError:
        return [], "unsupported"
    if pages:
        return pages, "text"
    if not path.lower().endswith(".pdf"):
        return [], "empty"
    if not ocr_available():
        return [], "ocr_unavailable"
    pages = _ocr_pdf(raw)
    return pages, "ocr" if pages else "empty"
