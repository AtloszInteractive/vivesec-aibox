"""v1 retrieval-contract client for the parity harness (stdlib-only).

Speaks the SAME HTTP contract the híd (adapter) uses against `rag_service`
(`ViVeSec_AIBox_RAG_Interfesz_Spec.md` / `drive_sync_api_spec.txt`):

    GET  /health
    POST /ingest             {corpus_id, tenant_id, path, title, text, metadata}
    POST /rag/search_context {corpus_id, tenant_id, question, top_k, max_context_tokens}
    POST /index/drop/tree    {corpus_id, path, keep_exact}
    GET  /stats

Isolation is per `corpus_id` (one drive = one corpus); that corpus boundary IS
the ACL pre-filter, so the harness measures the engine exactly as production
calls it. No third-party deps — keeps the dev/CI gate dependency-light.

Auth: optional `X-API-Key` (engine env `RAG_API_KEY`). When the service runs
open (no key) the header is simply omitted.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass


@dataclass
class EngineClient:
    base_url: str
    token: str = ""          # sent as X-API-Key when non-empty
    timeout: float = 30.0

    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self.token:
            h["X-API-Key"] = self.token
        return h

    def _post(self, path: str, payload: dict) -> tuple[int, dict, float]:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self.base_url.rstrip("/") + path, data=data, headers=self._headers(), method="POST"
        )
        t0 = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = json.loads(resp.read().decode("utf-8"))
                return resp.status, body, (time.perf_counter() - t0) * 1000.0
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode("utf-8")), (time.perf_counter() - t0) * 1000.0

    def _get(self, path: str) -> dict:
        req = urllib.request.Request(
            self.base_url.rstrip("/") + path, headers=self._headers(), method="GET"
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def health(self) -> dict:
        """GET /health -> {ok, status, embedding:{...}, vector_backend}."""
        return self._get("/health")

    def stats(self) -> dict:
        """GET /stats -> {ok, stats:{documents, pages, chunks, corpora}}."""
        return self._get("/stats")

    def ingest(self, payload: dict) -> dict:
        """POST /ingest (text ingest). Raises on non-200."""
        code, body, _ = self._post("/ingest", payload)
        if code != 200:
            raise RuntimeError(f"ingest failed ({code}): {body}")
        return body

    def drop_tree(self, corpus_id: str, path: str = "/", keep_exact: bool = False) -> dict:
        """POST /index/drop/tree — clear a corpus subtree (idempotent)."""
        code, body, _ = self._post(
            "/index/drop/tree",
            {"corpus_id": corpus_id, "path": path, "keep_exact": keep_exact},
        )
        if code != 200:
            raise RuntimeError(f"drop_tree failed ({code}): {body}")
        return body

    def search_context(self, payload: dict) -> tuple[dict, float]:
        """POST /rag/search_context -> (response, wall_clock_ms).

        v1 returns {ok, contexts:[...]}; the engine does not self-report a
        latency, so the harness measures wall-clock time.
        """
        code, body, wall_ms = self._post("/rag/search_context", payload)
        if code != 200:
            raise RuntimeError(f"search_context failed ({code}): {body}")
        return body, wall_ms
