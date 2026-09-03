"""rag-engine HTTP app — the stable /ingest + /search contract (spec §3-§8).

Internal-only FastAPI service. The HÍD (bridge) is the only client. Generation
(LLM) is NOT here — it stays in the shared Ollama container.
"""
from __future__ import annotations

from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse

from .config import settings
from .engine import build_engine
from .schemas import (
    CONTRACT_VERSION,
    AnswerResponse,
    HealthResponse,
    IngestRequest,
    IngestResponse,
    SearchRequest,
    SearchResponse,
    StatsResponse,
)
from .security import require_internal_token

app = FastAPI(
    title="ViVeSec rag-engine",
    version=settings.ENGINE_VERSION,
    description="Swappable retrieval-only RAG engine (ingest + embed + hybrid search).",
)

engine = build_engine()


@app.get("/healthz", response_model=HealthResponse)
def healthz() -> HealthResponse:
    return HealthResponse(
        engine=engine.info(),
        vector_backend=settings.VECTOR_BACKEND,
        rerank_enabled=settings.RERANK,
        index_ready=engine.index_ready(),
        contract_version=CONTRACT_VERSION,
    )


@app.get("/stats", response_model=StatsResponse, dependencies=[Depends(require_internal_token)])
def stats() -> StatsResponse:
    files, chunks = engine.stats()
    return StatsResponse(
        files_indexed=files,
        chunks_indexed=chunks,
        vector_backend=settings.VECTOR_BACKEND,
        embed_model=settings.EMBED_MODEL,
    )


@app.post(
    "/ingest",
    response_model=IngestResponse,
    dependencies=[Depends(require_internal_token)],
)
def ingest(req: IngestRequest) -> IngestResponse:
    if not req.content_base64 and not req.content_uri:
        return JSONResponse(
            status_code=400,
            content={
                "error": {
                    "code": "invalid_request",
                    "message": "Either content_base64 or content_uri is required.",
                }
            },
        )
    return engine.ingest(req)


@app.post(
    "/search",
    response_model=SearchResponse,
    dependencies=[Depends(require_internal_token)],
)
def search(req: SearchRequest) -> SearchResponse:
    # Invariant 2 is enforced inside the engine: empty allowed_file_ids => 0 hits.
    return engine.search(req)


@app.post(
    "/answer",
    response_model=AnswerResponse,
    dependencies=[Depends(require_internal_token)],
)
def answer() -> JSONResponse:
    # Optional endpoint. By default generation stays in the HÍD (spec §6).
    return JSONResponse(
        status_code=501,
        content={
            "error": {
                "code": "not_implemented",
                "message": "Generation stays in the HÍD; /answer is optional and disabled.",
            }
        },
    )
