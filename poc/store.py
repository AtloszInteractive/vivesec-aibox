"""Minimal JSON-backed cosine-similarity vector store.

For the PoC this is intentionally dependency-free. In production on the Jetson
swap this class for Qdrant or sqlite-vec — the interface (add/search) is the same.
"""
import json
import math


class VectorStore:
    def __init__(self):
        self.records = []  # {id, source, chunk_index, text, vector}
        self.meta = {}

    def add(self, source, chunk_index, text, vector):
        self.records.append({
            "id": "%s#%d" % (source, chunk_index),
            "source": source,
            "chunk_index": chunk_index,
            "text": text,
            "vector": vector,
        })

    def save(self, path):
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"meta": self.meta, "records": self.records}, f)

    def load(self, path):
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.meta = data.get("meta", {})
        self.records = data.get("records", [])
        return self

    @staticmethod
    def _cosine(a, b):
        s = na = nb = 0.0
        for x, y in zip(a, b):
            s += x * y
            na += x * x
            nb += y * y
        if na == 0 or nb == 0:
            return 0.0
        return s / (math.sqrt(na) * math.sqrt(nb))

    def search(self, qvec, top_k):
        scored = [(self._cosine(qvec, rec["vector"]), rec) for rec in self.records]
        scored.sort(key=lambda x: x[0], reverse=True)
        return scored[:top_k]
