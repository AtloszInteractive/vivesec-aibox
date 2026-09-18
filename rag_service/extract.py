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
import datetime
import io
import os
import re

# Formats we can decode as text with zero dependencies.
TEXT_EXTS = {
    ".txt", ".md", ".markdown", ".text", ".csv", ".tsv",
    ".json", ".log", ".rst", ".yaml", ".yml", ".ini", ".xml",
}

# Formats that need MarkItDown to extract text from (office / rich / binary).
MARKITDOWN_EXTS = {
    ".pdf", ".docx", ".pptx", ".ppt", ".xls",
    ".html", ".htm", ".rtf",
}

# OOXML spreadsheets are read directly with openpyxl (see _extract_workbook):
# MarkItDown's pandas route rendered every empty cell as the literal "NaN"
# (27% of all spreadsheet tokens on the demo index), invented "Unnamed: N"
# headers and flattened every sheet into one page.
OPENPYXL_EXTS = {".xlsx", ".xlsm"}

# Row-oriented formats: chunked on row boundaries with the header repeated
# (chunking.chunk_rows) instead of the sliding word window.
TABULAR_EXTS = OPENPYXL_EXTS | {".csv", ".tsv"}

SUPPORTED_EXTS = TEXT_EXTS | MARKITDOWN_EXTS | OPENPYXL_EXTS

# pdfminer separates pages with a form feed; plain text sources may too.
PAGE_BREAK = "\f"

# Per-sheet row cap: a 100k-row export would otherwise dominate the corpus.
SHEET_MAX_ROWS = int(os.environ.get("RAG_SHEET_MAX_ROWS", "5000"))

# Line prefixes of the sheet preamble that chunk_rows repeats in every chunk.
SHEET_LINE = "Sheet: "
COLUMNS_LINE = "Columns: "


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


_OPENPYXL = None
_OPENPYXL_TRIED = False


def _openpyxl():
    global _OPENPYXL, _OPENPYXL_TRIED
    if not _OPENPYXL_TRIED:
        _OPENPYXL_TRIED = True
        try:
            import openpyxl
            _OPENPYXL = openpyxl
        except Exception:
            _OPENPYXL = None
    return _OPENPYXL


_WS_RE = re.compile(r"\s+")
_ERROR_VALUES = {"#REF!", "#N/A", "#VALUE!", "#DIV/0!", "#NAME?", "#NULL!", "#NUM!"}


def _cell_text(cell):
    """Human-readable cell value, or None for cells that carry no information."""
    v = cell.value
    if v is None:
        return None
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, datetime.datetime):
        if (v.hour, v.minute, v.second) == (0, 0, 0):
            return v.date().isoformat()
        return v.isoformat(sep=" ", timespec="minutes")
    if isinstance(v, (datetime.date, datetime.time)):
        return v.isoformat()
    if isinstance(v, float):
        fmt = getattr(cell, "number_format", "") or ""
        if "%" in fmt:
            return format(v * 100, ".10g") + "%"
        if v.is_integer() and abs(v) < 1e15:
            return str(int(v))
        return format(v, ".10g")
    if isinstance(v, int):
        return str(v)
    s = _WS_RE.sub(" ", str(v)).strip()
    if not s or s in _ERROR_VALUES:
        return None
    return s


def _column_letter(idx):
    letters = ""
    while idx > 0:
        idx, rem = divmod(idx - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


def _pick_header(rows):
    """Index of the header row among the leading rows, or None.

    The header is the first of the first ten non-empty rows that holds only
    labels (no numbers/dates) and spans at least half of the widest of them.
    Title rows and merged-cell banners above it are narrower, so they are left
    as prose; a header may itself have gaps (unlabelled columns fall back to
    the column letter).
    """
    head = rows[:10]
    if not head:
        return None
    widest = max(len(r) for r in head)
    if widest < 2:
        return None
    for i, row in enumerate(head):
        if len(row) >= max(2, widest / 2.0) and all(isinstance(v, str) for v in row.values()):
            return i
    return None


def _sheet_rows(ws):
    """Non-empty rows of a worksheet as {col_index: raw_value} dicts."""
    rows = []
    truncated = 0
    for row in ws.iter_rows():
        values = {}
        for cell in row:
            text = _cell_text(cell)
            if text is not None:
                values[cell.column] = (cell.value, text)
        if not values:
            continue
        if len(rows) >= SHEET_MAX_ROWS:
            truncated += 1
            continue
        rows.append(values)
    return rows, truncated


def _render_sheet(title, rows, truncated):
    """One worksheet as row records: `Sheet:` line, optional `Columns:` line,
    then one line per row as `Header: value; Header: value`. Empty cells are
    simply absent, so a sparse sheet does not turn into a wall of NaN."""
    raw_rows = [{c: v[0] for c, v in r.items()} for r in rows]
    text_rows = [{c: v[1] for c, v in r.items()} for r in rows]
    header_idx = _pick_header(raw_rows)
    lines = [SHEET_LINE + title]
    header = {}
    if header_idx is not None:
        header = text_rows[header_idx]
        lines.append(COLUMNS_LINE + " | ".join(header[c] for c in sorted(header)))
    for i, row in enumerate(text_rows):
        if i == header_idx:
            continue
        if header_idx is None or i < header_idx:
            lines.append(" | ".join(row[c] for c in sorted(row)))
            continue
        parts = []
        for c in sorted(row):
            label = header.get(c) or _column_letter(c)
            parts.append("%s: %s" % (label, row[c]))
        lines.append("; ".join(parts))
    if truncated:
        lines.append("(%d further rows of this sheet were not indexed)" % truncated)
    return "\n".join(lines)


def _extract_workbook(raw, path):
    """Each worksheet becomes one page (page_number = sheet position,
    section_path = sheet name) so a citation can name the sheet."""
    openpyxl = _openpyxl()
    if openpyxl is None:
        text = _extract_markitdown(raw, path)
        return [(1, "root", text.strip())] if text.strip() else []
    try:
        wb = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
    except Exception as exc:  # noqa: BLE001 -- corrupt/encrypted workbook
        raise ExtractionError("openpyxl failed for %s: %s" % (path, exc))
    pages = []
    try:
        for number, ws in enumerate(wb.worksheets, start=1):
            rows, truncated = _sheet_rows(ws)
            if not rows:
                continue
            pages.append((number, ws.title, _render_sheet(ws.title, rows, truncated)))
    finally:
        try:
            wb.close()
        except Exception:  # noqa: BLE001
            pass
    return pages


def ext_of(path):
    return os.path.splitext(path)[1].lower()


def is_supported(path):
    return ext_of(path) in SUPPORTED_EXTS


def is_tabular(path):
    return ext_of(path) in TABULAR_EXTS


def header_lines(path, text):
    """How many leading lines of a tabular page are header to repeat per chunk."""
    ext = ext_of(path)
    if ext in OPENPYXL_EXTS:
        n = 0
        for line in text.split("\n", 2)[:2]:
            if line.startswith(SHEET_LINE) or line.startswith(COLUMNS_LINE):
                n += 1
            else:
                break
        return n
    if ext in (".csv", ".tsv"):
        return 1
    return 0


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
    if ext in OPENPYXL_EXTS:
        return _openpyxl() is not None or _markitdown() is not None
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
    if not raw:
        return []
    if ext in TEXT_EXTS:
        text = _decode_text(raw)
    elif ext == ".pdf":
        text = _extract_pdf(raw, path)
    elif ext in OPENPYXL_EXTS:
        return _extract_workbook(raw, path)
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
