"""Pydantic schemas for the rag-engine contract.

These mirror ViVeSec_AIBox_RAG_Interfesz_Spec.md (§4 /ingest, §5 /search, §7 errors)
exactly. The contract is the stable boundary between the in-house HÍD (bridge) and
the swappable rag-engine container — do not change field names/semantics without
updating the spec and bumping the contract version.
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

CONTRACT_VERSION = "0.1"


# --------------------------------------------------------------------------- #
# /ingest
# --------------------------------------------------------------------------- #
class IngestRequest(BaseModel):
    file_id: str = Field(..., description="Stable ViVeSec Box file id; chunk ids derive from it.")
    content_hash: str = Field(..., description="sha256 of the raw file — idempotency + reconcile.")
    filename: str
    mime_type: str
    acl_scope: list[str] = Field(..., description="Scope tags stamped onto every chunk.")
    sensitivity: str = Field(..., description="public | internal | confidential | restricted")
    language_hint: Optional[str] = None
    doc_type: Optional[str] = None
    content_base64: Optional[str] = Field(None, description="Raw file, base64. Or use content_uri.")
    content_uri: Optional[str] = Field(None, description="Mount path alternative for large files.")
    metadata: dict[str, Any] = Field(default_factory=dict)


class EngineInfo(BaseModel):
    name: str
    version: str
    embed_model: str
    embed_dim: Optional[int] = None


class IngestResponse(BaseModel):
    file_id: str
    status: Literal["indexed", "unchanged", "queued_for_ocr"]
    chunks_indexed: int = 0
    chunks_total: int = 0
    scanned_pdf: bool = False
    skipped_for_ocr: bool = False
    engine: EngineInfo
    warnings: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# /search
# --------------------------------------------------------------------------- #
class SearchFilters(BaseModel):
    doc_type: Optional[list[str]] = None
    max_sensitivity: Optional[str] = None


class SearchOptions(BaseModel):
    rerank: bool = True
    return_text: bool = True


class SearchRequest(BaseModel):
    query: str
    # HARD pre-filter. Empty/missing => the engine MUST return zero hits (spec §5.1).
    allowed_file_ids: list[str] = Field(default_factory=list)
    top_k: int = 5
    language_hint: Optional[str] = None
    filters: Optional[SearchFilters] = None
    options: SearchOptions = Field(default_factory=SearchOptions)


class SourceRef(BaseModel):
    filename: Optional[str] = None
    page: Optional[int] = None


class ScoreComponents(BaseModel):
    dense: Optional[float] = None
    sparse: Optional[float] = None
    bm25: Optional[float] = None
    rerank: Optional[float] = None


class Hit(BaseModel):
    chunk_id: str
    file_id: str
    chunk_index: int
    score: float
    rank: int
    chunk_text: str = ""  # verbatim source text — mandatory for T1 citations (spec invariant 3)
    section_path: list[str] = Field(default_factory=list)
    page: Optional[int] = None
    source_ref: Optional[SourceRef] = None
    components: Optional[ScoreComponents] = None


class SearchResponse(BaseModel):
    query: str
    hits: list[Hit] = Field(default_factory=list)
    engine: EngineInfo
    latency_ms: int = 0


# --------------------------------------------------------------------------- #
# /answer (optional — generation normally stays in the HÍD)
# --------------------------------------------------------------------------- #
class AnswerRequest(BaseModel):
    query: str
    allowed_file_ids: list[str] = Field(default_factory=list)
    top_k: int = 5


class AnswerCitation(BaseModel):
    chunk_id: str
    file_id: str
    quote: str


class AnswerResponse(BaseModel):
    answer: str
    citations: list[AnswerCitation] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Health / stats / errors
# --------------------------------------------------------------------------- #
class HealthResponse(BaseModel):
    ok: bool = True
    engine: EngineInfo
    vector_backend: str
    rerank_enabled: bool
    index_ready: bool
    contract_version: str = CONTRACT_VERSION


class StatsResponse(BaseModel):
    files_indexed: int = 0
    chunks_indexed: int = 0
    vector_backend: str = ""
    embed_model: str = ""


class ErrorBody(BaseModel):
    code: str
    message: str
    details: Optional[dict[str, Any]] = None


class ErrorResponse(BaseModel):
    error: ErrorBody
