"""Tiny stdlib-only Ollama client (no third-party deps).

Talks to the Ollama HTTP API via urllib so it works on the embeddable Python
on Windows and on arm64 Python on the Jetson without installing anything.
"""
import json
import urllib.request

import config

_AVAIL = None


def _post(path, payload, timeout=600):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        config.OLLAMA_URL + path,
        data=data,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def available(timeout=1.5, force=False):
    """Probe Ollama once and cache the result for the process lifetime."""
    global _AVAIL
    if _AVAIL is not None and not force:
        return _AVAIL
    try:
        req = urllib.request.Request(config.OLLAMA_URL + "/api/tags")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            json.loads(r.read().decode("utf-8"))
        _AVAIL = True
    except Exception:
        _AVAIL = False
    return _AVAIL


def list_models(timeout=3):
    try:
        req = urllib.request.Request(config.OLLAMA_URL + "/api/tags")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
        return [m.get("name", "") for m in data.get("models", [])]
    except Exception:
        return []


def model_pulled(name):
    base = name.split(":")[0]
    for m in list_models():
        if m == name or m.split(":")[0] == base:
            return True
    return False


def embed(text, model=None, timeout=120):
    out = _post("/api/embeddings", {"model": model or config.EMBED_MODEL, "prompt": text}, timeout)
    return out["embedding"]


def embed_batch(texts, model=None, timeout=600):
    """Batch embedding via /api/embed (one HTTP call for a list of inputs).

    Falls back to per-text /api/embeddings if the batch endpoint is missing
    or returns an unexpected shape (older Ollama builds). Cosine scoring is
    scale-invariant, so mixing vectors from the two endpoints is safe.
    """
    texts = list(texts)
    if not texts:
        return []
    try:
        out = _post("/api/embed", {"model": model or config.EMBED_MODEL, "input": texts}, timeout)
        embs = out.get("embeddings")
        if isinstance(embs, list) and len(embs) == len(texts):
            return embs
    except Exception:
        pass
    return [embed(t, model) for t in texts]


def chat(system, user, model=None, timeout=600):
    """Return (content, eval_count, eval_duration_ns)."""
    options = {"temperature": 0.2}
    if config.NUM_PREDICT:
        options["num_predict"] = config.NUM_PREDICT
    if getattr(config, "REPEAT_PENALTY", 0):
        options["repeat_penalty"] = config.REPEAT_PENALTY
    payload = {
        "model": model or config.GEN_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "stream": False,
        "options": options,
    }
    out = _post("/api/chat", payload, timeout)
    content = out.get("message", {}).get("content", "")
    return content, out.get("eval_count"), out.get("eval_duration")
