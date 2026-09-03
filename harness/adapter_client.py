"""Adapter (AIBox) client for the LEVEL-2 system eval (stdlib-only).

While `client.py` speaks the v1 retrieval contract DIRECTLY against a RAG
engine (level 1: component gate), this client speaks the PRODUCTION surface of
the box — the ViVeSec v2 adapter API — exactly like the ViVeSecBox does:

    POST /api/v1/status
    POST /api/v1/index/upsert/file/check      {path,size,mtime,head}
    POST /api/v1/index/upsert/file/content/{token}   (raw bytes)
    POST /api/v1/index/drop/tree              {path,keep_exact}
    POST /api/v1/ui/ask   + VVS-Drive/VVS-User headers -> 202 {job_id}
    POST /api/v1/ui/poll  {job_id,timeout}    (bounded long-poll)

So a level-2 run exercises the REAL pipeline: sync-push ingest -> extraction ->
retrieval -> grounded generation (`llm.py`) -> citations, including the
VVS-Drive hard ACL filter (drive root = corpus).

The VVS-Drive header is urlsafe base64 of the UTF-8 drive-root path with a
trailing '/', exactly as the box sends it (adapter `decode_vvs_drive`).
"""
from __future__ import annotations

import base64
import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass


def encode_vvs_drive(drive_root: str) -> str:
    """`/storage/drives/eval-finance` -> urlsafe b64 of `/storage/drives/eval-finance/`."""
    root = drive_root if drive_root.endswith("/") else drive_root + "/"
    return base64.urlsafe_b64encode(root.encode("utf-8")).decode("ascii")


@dataclass
class AdapterClient:
    base_url: str
    user: str = "harness"
    timeout: float = 60.0        # plain HTTP timeout (ask/status/sync)
    poll_timeout_s: int = 25     # server-side long-poll window per /ui/poll
    max_polls: int = 24          # 24 * 25s = 10 min budget for one answer

    # ----- plumbing ---------------------------------------------------------
    def _request(self, path: str, data: bytes | None, headers: dict,
                 timeout: float) -> tuple[int, dict]:
        req = urllib.request.Request(self.base_url.rstrip("/") + path,
                                     data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read().decode("utf-8"))
            except Exception:  # noqa: BLE001
                return e.code, {"ok": False, "error": "http %s" % e.code}

    def _post_json(self, path: str, payload: dict, extra_headers: dict | None = None,
                   timeout: float | None = None) -> tuple[int, dict]:
        headers = {"Content-Type": "application/json"}
        if extra_headers:
            headers.update(extra_headers)
        return self._request(path, json.dumps(payload).encode("utf-8"), headers,
                             timeout or self.timeout)

    # ----- status -----------------------------------------------------------
    def status(self) -> dict:
        _, body = self._post_json("/api/v1/status", {})
        return body

    # ----- sync-push ingest (the production index path) ----------------------
    def sync_file(self, path: str, data: bytes, mtime: int) -> dict:
        """check -> content upload for one file. Returns
        {ingested: bool, reason: str|None, chunks: int|None}."""
        head = base64.b64encode(data[:256]).decode("ascii")
        code, res = self._post_json("/api/v1/index/upsert/file/check",
                                    {"path": path, "size": len(data),
                                     "mtime": mtime, "head": head})
        if code != 200:
            return {"ingested": False, "reason": res.get("error", "check http %s" % code),
                    "chunks": None}
        token = res.get("token")
        if not token:
            return {"ingested": False, "reason": res.get("reason") or "no token",
                    "chunks": None}
        code, up = self._request(
            "/api/v1/index/upsert/file/content/" + token, data,
            {"Content-Type": "application/octet-stream"}, self.timeout)
        if code != 200:
            return {"ingested": False, "reason": up.get("error", "content http %s" % code),
                    "chunks": None}
        return {"ingested": True, "reason": None, "chunks": up.get("chunks")}

    def drop_tree(self, path: str) -> dict:
        _, body = self._post_json("/api/v1/index/drop/tree",
                                  {"path": path, "keep_exact": False})
        return body

    # ----- agentic query (ask -> long-poll) ----------------------------------
    def ask(self, question: str, drive_root: str, top_k: int = 5,
            lang: str | None = None) -> tuple[dict, float]:
        """Submit one question scoped to `drive_root` and long-poll the answer.
        Returns (result, wall_ms); result is the adapter's answer payload
        (`{ok, answer, backend, citations, hits, ...}`) or `{ok: False, error}`."""
        headers = {"VVS-Drive": encode_vvs_drive(drive_root), "VVS-User": self.user}
        payload: dict = {"query": question, "top_k": top_k}
        if lang:
            payload["lang"] = lang
        t0 = time.perf_counter()
        code, res = self._post_json("/api/v1/ui/ask", payload, headers)
        if code != 202 or not res.get("job_id"):
            return ({"ok": False, "error": res.get("error", "ask http %s" % code)},
                    (time.perf_counter() - t0) * 1000.0)
        job_id = res["job_id"]
        for _ in range(self.max_polls):
            code, poll = self._post_json(
                "/api/v1/ui/poll", {"job_id": job_id, "timeout": self.poll_timeout_s},
                timeout=self.poll_timeout_s + 15)
            if code == 404:
                return ({"ok": False, "error": "job expired"},
                        (time.perf_counter() - t0) * 1000.0)
            if poll.get("status") == "pending":
                continue
            return poll, (time.perf_counter() - t0) * 1000.0
        return ({"ok": False, "error": "poll budget exhausted"},
                (time.perf_counter() - t0) * 1000.0)
