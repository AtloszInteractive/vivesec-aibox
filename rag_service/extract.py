"""Deterministic content extraction for the RAG service.

Mirrors the supported-format policy of the PageIndexes contract
(`drive_sync_api_spec.txt`): plain text/markdown/csv are decoded directly, the
office/binary formats go through MarkItDown. Extraction is the boundary where a
raw uploaded file becomes text we can chunk + embed.

SECURITY: extraction works on in-memory bytes (BytesIO), never writing the
cleartext source to disk on our side. (MarkItDown's own converters may buffer
internally; on the appliance that buffering must land on tmpfs or the LUKS
volume — a deployment concern, see the project notes.)
"""
import io
import os

# Formats we can decode as text with zero dependencies.
TEXT_EXTS = {
    ".txt", ".md", ".markdown", ".text", ".csv", ".tsv",
    ".json", ".log", ".rst", ".yaml", ".yml", ".ini", ".xml",
}

# Formats that need MarkItDown to extract text from (office / rich / binary).
MARKITDOWN_EXTS = {
    ".pdf", ".docx", ".pptx", ".ppt", ".xlsx", ".xls",
    ".html", ".htm", ".rtf",
}

SUPPORTED_EXTS = TEXT_EXTS | MARKITDOWN_EXTS

# pdfminer separates pages with a form feed; plain text sources may too.
PAGE_BREAK = "\f"


class ExtractionError(Exception):
    """Raised when a supported format cannot be turned into text."""


# Lazily import MarkItDown so the module loads (and TEXT_EXTS still work) even
# where the dependency is not installed (e.g. the dev laptop). The real RAG
# container pins markitdown in requirements.
_MD = None
_MD_TRIED = False
_PDFMINER = None
_PDFMINER_TRIED = False


def _markitdown():
    global _MD, _MD_TRIED
    if not _MD_TRIED:
        _MD_TRIED = True
        try:
            from markitdown import MarkItDown
            _MD = MarkItDown()
        except Exception:
            _MD = None
    return _MD


def _pdfminer_extract_text():
    """pdfminer.six's extract_text, or None where it is not installed.

    Comes with markitdown[all], so on the appliance this is always available.
    """
    global _PDFMINER, _PDFMINER_TRIED
    if not _PDFMINER_TRIED:
        _PDFMINER_TRIED = True
        try:
            from pdfminer.high_level import extract_text
            _PDFMINER = extract_text
        except Exception:
            _PDFMINER = None
    return _PDFMINER


def _extract_pdf(raw, path):
    """PDF text with page separators intact.

    MarkItDown renders PDFs to markdown and normalises whitespace, which strips
    pdfminer's form feeds -- so every PDF used to collapse into a single logical
    page and every citation said "page 1". Reading pdfminer directly keeps the
    page boundaries the citations need. Falls back to MarkItDown if pdfminer is
    unavailable or chokes on the file.
    """
    extract_text = _pdfminer_extract_text()
    if extract_text is not None:
        try:
            text = extract_text(io.BytesIO(raw))
        except Exception:  # noqa: BLE001 -- malformed PDF, try the other reader
            text = ""
        if text.strip():
            return text
    return _extract_markitdown(raw, path)


def ext_of(path):
    return os.path.splitext(path)[1].lower()


def is_supported(path):
    return ext_of(path) in SUPPORTED_EXTS


def needs_markitdown(path):
    return ext_of(path) in MARKITDOWN_EXTS


def extractor_available(path):
    """Can we actually extract this file here and now?

    Text formats: always. MarkItDown formats: only if the library is importable.
    Used by the /check policy to decide between a real token and a graceful
    `extractor_unavailable` skip on hosts without the dependency.
    """
    ext = ext_of(path)
    if ext in TEXT_EXTS:
        return True
    if ext == ".pdf":
        return _pdfminer_extract_text() is not None or _markitdown() is not None
    if ext in MARKITDOWN_EXTS:
        return _markitdown() is not None
    return False


def _decode_text(raw):
    """Decode bytes to text, trying UTF-8 then Latin-1 (never fails)."""
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("latin-1")


def _extract_markitdown(raw, path):
    md = _markitdown()
    if md is None:
        raise ExtractionError("markitdown not installed for %s" % ext_of(path))
    ext = ext_of(path)
    stream = io.BytesIO(raw)
    errors = []
    result = None

    # Strategy 1 (markitdown >= 0.1): pass a StreamInfo type hint so the right
    # converter is selected without a filename on disk.
    try:
        from markitdown import StreamInfo
        stream.seek(0)
        result = md.convert_stream(stream, stream_info=StreamInfo(extension=ext))
    except Exception as exc:  # noqa: BLE001
        errors.append("stream_info: %s" % exc)
        result = None

    # Strategy 2 (older signature): file_extension keyword.
    if result is None:
        try:
            stream.seek(0)
            result = md.convert_stream(stream, file_extension=ext)
        except Exception as exc:  # noqa: BLE001
            errors.append("file_extension: %s" % exc)
            result = None

    # Strategy 3 (last resort): positional stream, let magika sniff the type.
    if result is None:
        try:
            stream.seek(0)
            result = md.convert_stream(stream)
        except Exception as exc:  # noqa: BLE001
            errors.append("positional: %s" % exc)
            result = None

    if result is None:
        raise ExtractionError("markitdown failed for %s: %s" % (path, "; ".join(errors)))
    text = getattr(result, "text_content", None) or ""
    return text


def extract_pages(raw, path):
    """Return a list of (page_number, section_path, text) for a file's content.

    Paginated sources (PDF via pdfminer, and plain text that uses form feeds)
    are split on U+000C so citations can name a real page. Formats with no
    intrinsic pagination stay a single logical page. section_path is filled in
    during chunking from markdown headings.
    """
    ext = ext_of(path)
    if ext in TEXT_EXTS:
        text = _decode_text(raw)
    elif ext == ".pdf":
        text = _extract_pdf(raw, path)
    elif ext in MARKITDOWN_EXTS:
        text = _extract_markitdown(raw, path)
    else:
        raise ExtractionError("unsupported type: %s" % ext)
    text = text.strip()
    if not text:
        return []
    if PAGE_BREAK not in text:
        return [(1, "root", text)]
    # Number before discarding blanks, so a scanned/empty page does not shift
    # the pages after it.
    return [
        (number, "root", chunk.strip())
        for number, chunk in enumerate(text.split(PAGE_BREAK), start=1)
        if chunk.strip()
    ]
