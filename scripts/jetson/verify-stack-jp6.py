#!/usr/bin/env python3
import base64
import hashlib
import json
import subprocess
import urllib.error
import urllib.request


RAG = "http://127.0.0.1:8090"
ADAPTER = "http://127.0.0.1:80"
DRIVE = "/storage/drives/aiboxdev/"
PATH = "/storage/drives/aiboxdev/handover-smoke.txt"
MARKER = "VIVESEC-ORIN-6147"


def post(url, payload, headers=None, timeout=300):
    request_headers = {"Content-Type": "application/json"}
    request_headers.update(headers or {})
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=request_headers,
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.status, json.load(response)


def main():
    key = open("/data/app/rag-api-key", encoding="ascii").read().strip()
    rag_headers = {"X-API-Key": key}
    drive_root = DRIVE.rstrip("/")
    slug = drive_root.rsplit("/", 1)[-1]
    corpus_id = "%s-%s" % (
        slug,
        hashlib.sha1(drive_root.encode("utf-8")).hexdigest()[:8],
    )
    text = (
        "AIBox handover verification record. "
        "The handover verification code is %s. "
        "This document exists only for the installation smoke test."
    ) % MARKER

    subprocess.run(
        ["docker", "exec", "vivesec-adapter", "test", "-s", "/data/pki/device_uuid"],
        check=True,
    )
    try:
        status, ingested = post(
            RAG + "/index/upsert/file/content",
            {
                "corpus_id": corpus_id,
                "path": PATH,
                "title": "AIBox handover smoke test",
                "content_b64": base64.b64encode(text.encode("utf-8")).decode("ascii"),
            },
            rag_headers,
        )
        assert status == 200 and ingested.get("ok") and ingested.get("chunks", 0) >= 1

        status, search = post(
            RAG + "/rag/search_context",
            {"corpus_id": corpus_id, "question": "What is the handover verification code?", "top_k": 3},
            rag_headers,
        )
        contexts = search.get("contexts") or []
        assert status == 200 and any(MARKER in (item.get("text") or "") for item in contexts)

        adapter_headers = {
            "VVS-Drive": base64.urlsafe_b64encode(DRIVE.encode("utf-8")).decode("ascii").rstrip("="),
            "VVS-User": "handover-smoke",
        }
        status, answer = post(
            ADAPTER + "/api/v1/ui/query",
            {"query": "What is the handover verification code?", "profile": "hybrid", "top_k": 3},
            adapter_headers,
        )
        assert status == 200 and answer.get("ok") and MARKER in (answer.get("answer") or "")
        assert answer.get("hits") or answer.get("citations")
        print(
            "AIBOX_DOCUMENT_SMOKE_OK corpus=%s chunks=%s backend=%s"
            % (corpus_id, ingested.get("chunks"), answer.get("backend"))
        )
    finally:
        try:
            post(
                RAG + "/index/drop/tree",
                {"corpus_id": corpus_id, "path": PATH, "keep_exact": False},
                rag_headers,
            )
        except (OSError, urllib.error.URLError):
            pass


if __name__ == "__main__":
    main()