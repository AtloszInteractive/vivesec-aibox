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
