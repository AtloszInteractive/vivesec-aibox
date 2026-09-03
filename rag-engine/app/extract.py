"""Document extraction -> structural blocks (the ingest front-end).

Strategy (spec §4, §4.4):
  - binary formats (PDF/DOCX/XLSX/PPTX) + Docling present  -> Docling converts to
    Markdown (structure + tables preserved), then we parse that Markdown.
  - plain text / Markdown, or Docling absent                -> parse the text directly.

Either way the output is a uniform list of `Block`s (heading / paragraph / list /
table) carrying a section_path and page, which chunking.py turns into chunks.

Docling is an OPTIONAL heavy dependency. The skeleton runs WITHOUT it (text
fallback) so the contract is testable on any machine; on the Jetson the real
engine installs Docling for true PDF/Office extraction.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

_BINARY_MIME_PREFIXES = (
    "application/pdf",
    "application/vnd.openxmlformats-officedocument",  # docx/xlsx/pptx
    "application/msword",
    "application/vnd.ms-excel",
)


@dataclass
class Block:
    text: str
    kind: str = "text"  # "heading" | "text" | "list" | "table"
    section_path: list[str] = field(default_factory=list)
    page: int | None = None


def docling_available() -> bool:
    try:
        import docling  # noqa: F401

        return True
    except Exception:
        return False


def extract_blocks(raw: bytes, mime_type: str, filename: str) -> list[Block]:
    """Top-level entry: bytes -> structural blocks."""
    is_binary = mime_type.startswith(_BINARY_MIME_PREFIXES) or _looks_binary(filename)

    if is_binary and docling_available():
        try:
            markdown = _docling_to_markdown(raw, filename)
            return parse_markdown_blocks(markdown)
        except Exception:
            # fall through to best-effort text decode
            pass

    text = raw.decode("utf-8", "replace")
    return parse_markdown_blocks(text)


def _looks_binary(filename: str) -> bool:
    return filename.lower().rsplit(".", 1)[-1] in {"pdf", "docx", "xlsx", "pptx", "doc", "xls"}


def _docling_to_markdown(raw: bytes, filename: str) -> str:
    """Convert a binary document to Markdown via Docling.

    Kept isolated so the import only happens when Docling is actually used.
    """
    import os
    import tempfile

    from docling.document_converter import DocumentConverter  # type: ignore

    suffix = os.path.splitext(filename)[1] or ".pdf"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(raw)
        tmp_path = tmp.name
    try:
        converter = DocumentConverter()
        result = converter.convert(tmp_path)
        return result.document.export_to_markdown()
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


# --------------------------------------------------------------------------- #
# Markdown / plain-text structure parser (no deps)
# --------------------------------------------------------------------------- #
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_LIST_RE = re.compile(r"^\s*([-*+]|\d+[.)])\s+\S")
_TABLE_ROW_RE = re.compile(r"^\s*\|.*\|\s*$")
_TABLE_SEP_RE = re.compile(r"^\s*\|?\s*:?-{2,}.*$")


def parse_markdown_blocks(text: str) -> list[Block]:
    """Parse Markdown-ish text into blocks, tracking a heading section_path."""
    lines = text.replace("\r\n", "\n").split("\n")
    blocks: list[Block] = []
    section_stack: list[tuple[int, str]] = []  # (level, title)
    para: list[str] = []
    table: list[str] = []

    def section_path() -> list[str]:
        return [title for _, title in section_stack]

    def flush_para() -> None:
        if para:
            joined = " ".join(p.strip() for p in para).strip()
            if joined:
                kind = "list" if all(_LIST_RE.match(p) for p in para if p.strip()) else "text"
                blocks.append(Block(text=joined, kind=kind, section_path=section_path()))
            para.clear()

    def flush_table() -> None:
        if table:
            blocks.append(
                Block(text="\n".join(table).strip(), kind="table", section_path=section_path())
            )
            table.clear()

    for line in lines:
        m = _HEADING_RE.match(line)
        if m:
            flush_para()
            flush_table()
            level = len(m.group(1))
            title = m.group(2).strip()
            while section_stack and section_stack[-1][0] >= level:
                section_stack.pop()
            section_stack.append((level, title))
            blocks.append(Block(text=title, kind="heading", section_path=section_path()))
            continue

        if _TABLE_ROW_RE.match(line):
            if _TABLE_SEP_RE.match(line):  # the |---|---| separator row
                continue
            flush_para()
            table.append(line.strip())
            continue
        else:
            flush_table()

        if not line.strip():
            flush_para()
            continue

        para.append(line)

    flush_para()
    flush_table()
    return blocks
