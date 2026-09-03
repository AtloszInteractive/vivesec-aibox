"""Runtime configuration for the rag-engine container.

All knobs come from env so the SAME image runs as the `baseline` (in-house) or
`colearn` motor — only the env/compose service differs (spec §8, §9). Model
weights are NEVER baked into the image: they are mounted at RAG_MODEL_DIR.
"""
from __future__ import annotations

import os


class Settings:
    # --- identity (surfaced in every response's `engine` block) ---------- #
    ENGINE_NAME: str = os.environ.get("RAG_ENGINE_NAME", "baseline")
    ENGINE_VERSION: str = os.environ.get("RAG_ENGINE_VERSION", "0.1.0-skeleton")

    # --- storage backend: sqlite | postgres (spec §11.1) ----------------- #
    VECTOR_BACKEND: str = os.environ.get("RAG_VECTOR_BACKEND", "sqlite")
    # sqlite: path to the on-disk store; postgres: DSN.
    # Default ":memory:" keeps local dev frictionless (no disk, no Windows path
    # issues); the container sets RAG_STORE_PATH=/data/rag/index.db on a volume
    # so production PERSISTS across restarts (no reingest needed).
    STORE_PATH: str = os.environ.get("RAG_STORE_PATH", ":memory:")
    POSTGRES_DSN: str = os.environ.get("RAG_POSTGRES_DSN", "")

    # --- models (weights live on a mounted volume) ----------------------- #
    EMBED_MODEL: str = os.environ.get("RAG_EMBED_MODEL", "bge-m3")
    # Embedding backend: auto | ollama | hashing. `auto` uses Ollama if reachable,
    # else a dependency-free hashing fallback (dev/CI). Invariant 5 holds either
    # way: one embedder serves both ingest and query.
    EMBED_BACKEND: str = os.environ.get("RAG_EMBED_BACKEND", "auto")
    # Shared Ollama (embeddings here; generation stays in the HÍD).
    OLLAMA_URL: str = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
    MODEL_DIR: str = os.environ.get("RAG_MODEL_DIR", "/models")
    RERANK: bool = os.environ.get("RAG_RERANK", "true").lower() == "true"

    # --- retrieval defaults ---------------------------------------------- #
    TOP_K: int = int(os.environ.get("RAG_TOP_K", "5"))
    CANDIDATE_K: int = int(os.environ.get("RAG_CANDIDATE_K", "30"))  # before rerank

    # --- security: internal service token (spec §3) ---------------------- #
    INTERNAL_TOKEN: str = os.environ.get("RAG_INTERNAL_TOKEN", "")
    # Bind to the internal docker network only; never expose publicly (invariant 1).
    HOST: str = os.environ.get("RAG_HOST", "0.0.0.0")
    PORT: int = int(os.environ.get("RAG_PORT", "8081"))


settings = Settings()
