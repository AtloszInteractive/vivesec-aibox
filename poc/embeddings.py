"""Embeddings with two interchangeable backends.

* ollama  : production path (nomic-embed-text / bge) — high quality, GPU-accel.
* fallback: dependency-free signed feature-hashing — runs anywhere, good enough
            to prove the retrieval mechanics without any install.
"""
import hashlib
import math
import re

import config
import ollama_client as ollama

_STOP = set(
    "a an the and or of to in on for with at by from is are was were be been being "
    "this that these those it its as into over under your you our their his her them "
    "we they i not no but if then than so such can will would should could".split()
)
_TOKEN_RE = re.compile(r"[a-z0-9]+")


def tokens(text):
    return [t for t in _TOKEN_RE.findall(text.lower()) if t not in _STOP and len(t) > 1]


def _hash_embed(text, dim=None):
    dim = dim or config.FALLBACK_EMBED_DIM
    vec = [0.0] * dim
    for tok in tokens(text):
        h = hashlib.sha1(tok.encode("utf-8")).digest()
        idx = int.from_bytes(h[0:4], "big") % dim
        sign = 1.0 if (h[4] & 1) else -1.0
        vec[idx] += sign
    norm = math.sqrt(sum(v * v for v in vec))
    if norm > 0:
        vec = [v / norm for v in vec]
    return vec


def use_ollama_embed():
    if config.BACKEND == "fallback":
        return False
    if config.BACKEND == "ollama":
        return True
    return ollama.available() and ollama.model_pulled(config.EMBED_MODEL)


def backend_name(use_ollama):
    if use_ollama:
        return "ollama:" + config.EMBED_MODEL
    return "fallback:hashing-%dd" % config.FALLBACK_EMBED_DIM


def embed_one(text, use_ollama):
    if use_ollama:
        return ollama.embed(text)
    return _hash_embed(text)


_EMBED_BATCH = 32


def embed_many(texts, use_ollama):
    if not use_ollama:
        return [_hash_embed(t) for t in texts]
    out = []
    for i in range(0, len(texts), _EMBED_BATCH):
        out.extend(ollama.embed_batch(texts[i:i + _EMBED_BATCH]))
    return out
