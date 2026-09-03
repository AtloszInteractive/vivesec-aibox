// Wire types for the rag-engine contract (ViVeSec_AIBox_RAG_Interfesz_Spec.md).
//
// This is the HÍD-side mirror of rag-engine/app/schemas.py. The two MUST stay
// in sync — they are the stable boundary that lets the RAG container be swapped
// (baseline <-> colearn) without touching the bridge.

export const RAG_CONTRACT_VERSION = "0.1";

export type Sensitivity = "public" | "internal" | "confidential" | "restricted";

// Ordered low -> high, used by the ACL pre-filter clearance check.
export const SENSITIVITY_ORDER: readonly Sensitivity[] = [
  "public",
  "internal",
  "confidential",
  "restricted",
] as const;

// ---- /ingest ------------------------------------------------------------- //
export type IngestRequest = {
  file_id: string;
  content_hash: string;
  filename: string;
  mime_type: string;
  acl_scope: string[];
  sensitivity: Sensitivity;
  language_hint?: string;
  doc_type?: string;
  content_base64?: string;
  content_uri?: string;
  metadata?: Record<string, unknown>;
};

export type EngineInfo = {
  name: string;
  version: string;
  embed_model: string;
  embed_dim?: number;
};

export type IngestResponse = {
  file_id: string;
  status: "indexed" | "unchanged" | "queued_for_ocr";
  chunks_indexed: number;
  chunks_total: number;
  scanned_pdf: boolean;
  skipped_for_ocr: boolean;
  engine: EngineInfo;
  warnings: string[];
};

// ---- /search ------------------------------------------------------------- //
export type SearchFilters = {
  doc_type?: string[];
  max_sensitivity?: Sensitivity;
};

export type SearchRequest = {
  query: string;
  // HARD pre-filter. The bridge computes this from ACL ∩ scope. Empty => 0 hits.
  allowed_file_ids: string[];
  top_k?: number;
  language_hint?: string;
  filters?: SearchFilters;
  options?: { rerank?: boolean; return_text?: boolean };
};

export type Hit = {
  chunk_id: string;
  file_id: string;
  chunk_index: number;
  score: number;
  rank: number;
  chunk_text: string; // verbatim — the bridge builds T1 citations from this
  section_path: string[];
  page?: number;
  source_ref?: { filename?: string; page?: number };
  components?: { dense?: number; sparse?: number; bm25?: number; rerank?: number };
};

export type SearchResponse = {
  query: string;
  hits: Hit[];
  engine: EngineInfo;
  latency_ms: number;
};

export type HealthResponse = {
  ok: boolean;
  engine: EngineInfo;
  vector_backend: string;
  rerank_enabled: boolean;
  index_ready: boolean;
  contract_version: string;
};
