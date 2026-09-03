// Server-only client for the swappable rag-engine container (spec §3).
//
// The bridge is the ONLY caller. We attach the internal service token and talk
// to the engine over the internal docker network. This file must never reach the
// client bundle (it reads process.env + a shared secret) — keep imports server-side
// (e.g. used from createServerFn handlers).

import process from "node:process";

import type {
  HealthResponse,
  IngestRequest,
  IngestResponse,
  SearchRequest,
  SearchResponse,
} from "./contract";

function engineUrl(): string {
  return process.env.RAG_ENGINE_URL ?? "http://127.0.0.1:8081";
}

function authHeaders(): Record<string, string> {
  const token = process.env.RAG_INTERNAL_TOKEN ?? "";
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (token) headers.Authorization = `Bearer ${token}`;
  return headers;
}

const INGEST_TIMEOUT_MS = 120_000; // extraction + embedding can be heavy
const SEARCH_TIMEOUT_MS = 15_000;
const HEALTH_TIMEOUT_MS = 4_000;

class RagEngineError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code?: string,
  ) {
    super(message);
    this.name = "RagEngineError";
  }
}

async function postJson<T>(path: string, body: unknown, timeoutMs: number): Promise<T> {
  const res = await fetch(`${engineUrl()}${path}`, {
    method: "POST",
    headers: authHeaders(),
    body: JSON.stringify(body),
    signal: AbortSignal.timeout(timeoutMs),
  });
  if (!res.ok) {
    let code: string | undefined;
    let message = `rag-engine HTTP ${res.status}`;
    try {
      const j = (await res.json()) as { error?: { code?: string; message?: string } };
      code = j.error?.code;
      if (j.error?.message) message = j.error.message;
    } catch {
      /* non-JSON error body */
    }
    throw new RagEngineError(message, res.status, code);
  }
  return (await res.json()) as T;
}

export async function ingestDocument(req: IngestRequest): Promise<IngestResponse> {
  return postJson<IngestResponse>("/ingest", req, INGEST_TIMEOUT_MS);
}

/**
 * Retrieval. The caller MUST have already computed `allowed_file_ids` via the
 * ACL pre-filter. As a defence-in-depth check, an empty set short-circuits to an
 * empty result so we never round-trip an unconstrained query (invariant 2).
 */
export async function searchEngine(req: SearchRequest): Promise<SearchResponse> {
  if (!req.allowed_file_ids || req.allowed_file_ids.length === 0) {
    return { query: req.query, hits: [], engine: emptyEngineInfo(), latency_ms: 0 };
  }
  return postJson<SearchResponse>("/search", req, SEARCH_TIMEOUT_MS);
}

export async function engineHealth(): Promise<HealthResponse> {
  const res = await fetch(`${engineUrl()}/healthz`, {
    method: "GET",
    headers: authHeaders(),
    signal: AbortSignal.timeout(HEALTH_TIMEOUT_MS),
  });
  if (!res.ok) throw new RagEngineError(`rag-engine HTTP ${res.status}`, res.status);
  return (await res.json()) as HealthResponse;
}

function emptyEngineInfo() {
  return { name: "none", version: "0", embed_model: "" };
}

export { RagEngineError };
