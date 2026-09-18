"""Text chunking and sentence splitting (stdlib only)."""
import re

_SENT_RE = re.compile(r"(?<=[.!?])\s+")


def split_sentences(text):
    parts = _SENT_RE.split(text.strip())
    return [p.strip() for p in parts if p.strip()]


def chunk_text(text, size, overlap):
    """Sliding word-window chunks with overlap."""
    words = text.split()
    if not words:
        return []
    chunks = []
    step = max(1, size - overlap)
    for start in range(0, len(words), step):
        window = words[start:start + size]
        if not window:
            break
        chunks.append(" ".join(window))
        if start + size >= len(words):
            break
    return chunks


def chunk_rows(text, size, overlap, header_lines=0):
    """Row-boundary chunks for tabular text (one record per line).

    Rows are never split; consecutive rows are packed up to `size` words, the
    trailing rows worth up to `overlap` words carry over, and the first
    `header_lines` lines (sheet name / column labels) are repeated at the top
    of every chunk so a row keeps its column meaning wherever it lands.
    """
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return []
    header = lines[:header_lines]
    body = lines[header_lines:]
    if not body:
        return ["\n".join(header)]
    header_words = sum(len(h.split()) for h in header)
    budget = max(1, size - header_words)
    chunks = []
    current = []
    current_words = 0
    for line in body:
        n = len(line.split())
        if current and current_words + n > budget:
            chunks.append("\n".join(header + current))
            carried = []
            carried_words = 0
            for prev in reversed(current):
                pw = len(prev.split())
                if carried_words + pw > overlap:
                    break
                carried.insert(0, prev)
                carried_words += pw
            if carried_words + n > budget:
                # The next row alone fills the chunk; a carried tail would only
                # be re-emitted as a duplicate.
                carried, carried_words = [], 0
            current = carried
            current_words = carried_words
        current.append(line)
        current_words += n
    if current:
        chunks.append("\n".join(header + current))
    return chunks
