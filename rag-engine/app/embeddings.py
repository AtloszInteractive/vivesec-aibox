"""Embeddings — the dense vectorizer behind ingest + query.

Invariant 5 (spec §2): ingest- and query-embedding MUST use the same model. The
engine holds ONE embedder instance and uses it for both sides, so the invariant
holds by construction.

Two backends, selected by RAG_EMBED_BACKEND (auto | ollama | hashing):

  - OllamaEmbedder: the real path. Calls the shared Ollama (/api/embed) with
    bge-m3 — the SAME model family targeted for the Jetson. No torch in this
    container; embeddings come over HTTP from the Ollama service.
  - HashingEmbedder: dependency-free deterministic fallback (signed feature
    hashing over tokens + char trigrams). Lets the engine + harness + CI run on
    any machine with NO Ollama and NO heavy deps. NOT production quality — it is
    a lexical-ish stand-in so the contract + vector store are exercised.

Mirrors the Docling-optional pattern in extract.py: real backend when available,
honest fallback otherwise. Both backends return L2-normalized vectors, so cosine
similarity reduces to a dot product downstream.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import urllib.error
import urllib.request
from typing import Protocol

_WORD = re.compile(r"\w+", re.UNICODE)


def _normalize(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vec))
    if norm == 0.0:
        return vec
    return [x / norm for x in vec]


class Embedder(Protocol):
    name: str

    @property
    def dim(self) -> int: ...

    def embed(self, texts: list[str]) -> list[list[float]]: ...


# --------------------------------------------------------------------------- #
# Real backend: Ollama bge-m3 over HTTP
# --------------------------------------------------------------------------- #
class OllamaEmbedder:
    def __init__(self, url: str, model: str, timeout: float = 60.0) -> None:
        self.url = url.rstrip("/")
        self.model = model
        self.name = model
        self.timeout = timeout
        self._dim: int | None = None

    def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        payload = json.dumps({"model": self.model, "input": texts}).encode("utf-8")
        req = urllib.request.Request(
            self.url + "/api/embed",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        embs = body.get("embeddings")
        if not embs:
            raise RuntimeError(f"Ollama /api/embed returned no embeddings for model '{self.model}'")
        return embs

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        out = [_normalize(e) for e in self._embed_batch(texts)]
        if self._dim is None and out:
            self._dim = len(out[0])
        return out

    @property
    def dim(self) -> int:
        if self._dim is None:
            self.embed(["ping"])
        return self._dim or 0


# --------------------------------------------------------------------------- #
# Fallback backend: deterministic feature hashing (no deps, no model)
# --------------------------------------------------------------------------- #
def _features(text: str) -> list[str]:
    feats: list[str] = []
    for tok in _WORD.findall(text.lower()):
        feats.append(tok)
        s = f"#{tok}#"
        for i in range(len(s) - 2):  # char trigrams: morphology + multilingual robustness
            feats.append(s[i : i + 3])
    return feats


class HashingEmbedder:
    name = "hashing-fallback"

    def __init__(self, dim: int = 512) -> None:
        self._dim = dim

    @property
    def dim(self) -> int:
        return self._dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for text in texts:
            vec = [0.0] * self._dim
            for feat in _features(text):
                h = int.from_bytes(hashlib.blake2b(feat.encode("utf-8"), digest_size=8).digest(), "big")
                idx = h % self._dim
                sign = 1.0 if (h >> 63) & 1 else -1.0  # signed hashing reduces collision bias
                vec[idx] += sign
            out.append(_normalize(vec))
        return out


def build_embedder(settings) -> Embedder:
    """Pick the embedder per RAG_EMBED_BACKEND, with graceful fallback in `auto`."""
    backend = (getattr(settings, "EMBED_BACKEND", "auto") or "auto").lower()

    if backend in ("ollama", "auto"):
        emb = OllamaEmbedder(settings.OLLAMA_URL, settings.EMBED_MODEL)
        try:
            emb.embed(["ping"])  # verify reachable + model pulled
            print(f"[embeddings] Ollama '{settings.EMBED_MODEL}' dim={emb.dim} @ {settings.OLLAMA_URL}")
            return emb
        except (urllib.error.URLError, OSError, RuntimeError) as e:
            if backend == "ollama":
                raise
            print(f"[embeddings] Ollama unavailable ({e}); using hashing fallback")

    fallback = HashingEmbedder()
    print(f"[embeddings] hashing fallback dim={fallback.dim} (dev/CI only, NOT production quality)")
    return fallback
