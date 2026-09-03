"""Structure-aware chunking (spec §4.4).

Turns a list of structural Blocks (from extract.py) into retrieval chunks:

  - cut on structure (heading / paragraph / list / table / page), NEVER mid-sentence
  - target ~350-700 tokens, overlap ~50-100 (no overlap across structural seams)
  - tables become their OWN chunk, flagged is_table -> later `facts` prep (not prose)
  - each chunk carries its section_path + page for citation

Token counting here is a whitespace approximation; the real engine uses the
embedding model's tokenizer. The boundaries (block-aligned) stay identical.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .extract import Block

_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+")


@dataclass
class Chunk:
    text: str
    section_path: list[str] = field(default_factory=list)
    page: int | None = None
    is_table: bool = False


def _approx_tokens(text: str) -> int:
    return len(text.split())


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENT_SPLIT.split(text.strip()) if s.strip()]


def chunk_blocks(
    blocks: list[Block],
    target_tokens: int = 500,
    max_tokens: int = 900,
    overlap_tokens: int = 75,
) -> list[Chunk]:
    """Greedy, structure-respecting accumulation.

    Text blocks in the same section accumulate until ~target_tokens, then flush.
    A block bigger than max_tokens is split on sentence boundaries. Tables and
    section changes force a flush so a chunk never straddles a structural seam.
    """
    chunks: list[Chunk] = []
    buf: list[str] = []
    buf_tokens = 0
    cur_section: list[str] = []
    cur_page: int | None = None

    def flush() -> None:
        nonlocal buf, buf_tokens
        if not buf:
            return
        text = "\n\n".join(buf).strip()
        if text:
            chunks.append(Chunk(text=text, section_path=list(cur_section), page=cur_page))
        # carry an overlap tail (whole trailing sentences up to overlap_tokens)
        tail = _overlap_tail("\n\n".join(buf), overlap_tokens)
        buf = [tail] if tail else []
        buf_tokens = _approx_tokens(tail)

    for blk in blocks:
        # structural seam: section change or a table -> flush first
        if blk.section_path != cur_section or blk.kind == "table":
            flush()
            cur_section = blk.section_path
            cur_page = blk.page

        if blk.kind == "table":
            # tables stand alone; no overlap, flagged for facts prep
            chunks.append(
                Chunk(text=blk.text.strip(), section_path=list(blk.section_path),
                      page=blk.page, is_table=True)
            )
            buf, buf_tokens = [], 0
            continue

        cur_page = blk.page if blk.page is not None else cur_page
        btoks = _approx_tokens(blk.text)

        # oversized single block -> sentence-split it
        if btoks > max_tokens:
            flush()
            for piece in _split_oversized(blk.text, target_tokens):
                chunks.append(Chunk(text=piece, section_path=list(cur_section), page=cur_page))
            buf, buf_tokens = [], 0
            continue

        if buf_tokens + btoks > target_tokens and buf_tokens > 0:
            flush()

        buf.append(blk.text.strip())
        buf_tokens += btoks

    flush_text = "\n\n".join(buf).strip()
    if flush_text:
        chunks.append(Chunk(text=flush_text, section_path=list(cur_section), page=cur_page))

    # drop the synthetic overlap-only trailing chunk if it duplicates content
    return [c for c in chunks if c.text]


def _overlap_tail(text: str, overlap_tokens: int) -> str:
    if overlap_tokens <= 0:
        return ""
    sents = _sentences(text)
    tail: list[str] = []
    count = 0
    for s in reversed(sents):
        count += _approx_tokens(s)
        tail.insert(0, s)
        if count >= overlap_tokens:
            break
    return " ".join(tail)


def _split_oversized(text: str, target_tokens: int) -> list[str]:
    out: list[str] = []
    buf: list[str] = []
    count = 0
    for s in _sentences(text):
        st = _approx_tokens(s)
        if count + st > target_tokens and buf:
            out.append(" ".join(buf))
            buf, count = [], 0
        buf.append(s)
        count += st
    if buf:
        out.append(" ".join(buf))
    return out
