"""Smoke test for the RAG service contract.

Boots `service.py` in a subprocess against a throwaway index, then exercises the
contract over HTTP and asserts the two properties that matter most for a
PageIndexes-compatible drop-in:

  * idempotency   : re-ingesting the same (tenant, corpus, path) reuses doc_id
  * corpus isolation: a query in corpus A never returns corpus B documents

Plus contract-shape checks: /health, check->null for unsupported types,
single-step content_b64, drop/tree counts, and the 409 path is documented.

Runs on the dev box with VIVESEC_BACKEND=fallback (no Ollama needed); the
hashing embedding is deterministic so keyword overlap still ranks sanely.
"""
import base64
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = int(os.environ.get("SMOKE_PORT", "8097"))
BASE = "http://127.0.0.1:%d" % PORT

_failures = []


def _req(method, path, body=None):
    url = BASE + path
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


def _req_raw(method, path, raw):
    """POST a raw (non-JSON) body, e.g. file content to /content/{token}."""
    req = urllib.request.Request(BASE + path, data=raw, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


def check(name, cond, detail=""):
    mark = "PASS" if cond else "FAIL"
    print("[%s] %s%s" % (mark, name, (" -- " + detail) if detail else ""))
    if not cond:
        _failures.append(name)


def wait_ready(proc, attempts=50):
    for _ in range(attempts):
        if proc.poll() is not None:
            raise RuntimeError("service exited early with code %s" % proc.returncode)
        try:
            status, body = _req("GET", "/ready")
            if status == 200 and body.get("ready"):
                return
        except Exception:
            pass
        time.sleep(0.2)
    raise RuntimeError("service did not become ready")


def run_tests():
    # --- health / embedding shape ---
    status, health = _req("GET", "/health")
    check("health 200", status == 200)
    emb = health.get("embedding", {})
    check("health embedding info", all(k in emb for k in ("provider", "model_name", "dimension", "normalized")),
          json.dumps(emb))
    check("health vector_backend", "vector_backend" in health, health.get("vector_backend"))

    CA = "corpus-a"
    CB = "corpus-b"

    # --- ingest text into corpus A ---
    status, r1 = _req("POST", "/ingest", {
        "corpus_id": CA, "path": "/d/a/quarterly.txt", "title": "Quarterly",
        "text": "Revenue for the fourth quarter reached forty two million dollars across all regions.",
    })
    check("ingest A ok", status == 200 and r1.get("ok"), json.dumps(r1))
    doc_id_1 = r1.get("doc_id")
    check("ingest A produced chunks", (r1.get("chunks") or 0) >= 1, "chunks=%s" % r1.get("chunks"))
    check("successful ingest carries no warning", "warning" not in r1, json.dumps(r1))

    # --- no extractable text (e.g. a scanned PDF) must NOT pass silently ---
    status, rz = _req("POST", "/ingest", {
        "corpus_id": CA, "path": "/d/a/scanned.txt", "title": "Scanned",
        "text": "   ",
    })
    check("zero-text ingest warns", status == 200 and rz.get("warning") == "extraction_empty",
          json.dumps(rz))
    check("zero-text ingest produced no chunks", (rz.get("chunks") or 0) == 0,
          "chunks=%s" % rz.get("chunks"))

    # --- idempotency: same path, changed text -> SAME doc_id ---
    status, r2 = _req("POST", "/ingest", {
        "corpus_id": CA, "path": "/d/a/quarterly.txt", "title": "Quarterly v2",
        "text": "Updated revenue figure for the fourth quarter is now forty five million dollars.",
    })
    check("re-ingest same doc_id (idempotent)", r2.get("doc_id") == doc_id_1,
          "%s vs %s" % (doc_id_1, r2.get("doc_id")))

    # --- corpus isolation in IDs: same path, different corpus -> DIFFERENT doc_id ---
    status, r3 = _req("POST", "/ingest", {
        "corpus_id": CB, "path": "/d/a/quarterly.txt", "title": "Other tenant doc",
        "text": "Internal handbook: vacation policy and onboarding checklist for new employees.",
    })
    check("different corpus -> different doc_id", r3.get("doc_id") != doc_id_1,
          "%s vs %s" % (doc_id_1, r3.get("doc_id")))

    # --- search in corpus A returns ONLY corpus A docs ---
    status, s1 = _req("POST", "/rag/search_context", {
        "corpus_id": CA, "question": "quarterly revenue figure", "top_k": 5, "include_debug": True,
    })
    contexts = s1.get("contexts", [])
    check("search A returns results", len(contexts) >= 1, "n=%d" % len(contexts))
    check("search A only corpus A", all(c.get("corpus_id") == CA for c in contexts),
          "corpora=%s" % {c.get("corpus_id") for c in contexts})
    check("search A no corpus B doc", all(c.get("doc_id") != r3.get("doc_id") for c in contexts))
    if contexts:
        top = contexts[0]
        check("context has provenance", all(k in top for k in ("chunk_id", "doc_id", "page_id", "source_path", "score", "score_breakdown")))
        check("section_path defaults to root", top.get("section_path") == "root", str(top.get("section_path")))

    # --- search in corpus B is independent ---
    status, s2 = _req("POST", "/rag/search_context", {
        "corpus_id": CB, "question": "vacation policy", "top_k": 5,
    })
    cb = s2.get("contexts", [])
    check("search B only corpus B", all(c.get("corpus_id") == CB for c in cb), "n=%d" % len(cb))

    # --- check() on unsupported type -> token null + reason ---
    head_b64 = base64.b64encode(b"\x89PNG\r\n\x1a\n").decode("ascii")
    status, ck = _req("POST", "/index/upsert/file/check", {
        "corpus_id": CA, "path": "/d/a/logo.png", "size": 2048, "mtime": 1, "head": head_b64,
    })
    check("check unsupported -> null token", ck.get("token") is None and ck.get("reason") == "unsupported_type",
          json.dumps(ck))

    # --- check() empty file -> empty_content ---
    status, ce = _req("POST", "/index/upsert/file/check", {
        "corpus_id": CA, "path": "/d/a/empty.txt", "size": 0, "mtime": 1, "head": "",
    })
    check("check empty -> empty_content", ce.get("token") is None and ce.get("reason") == "empty_content",
          json.dumps(ce))

    # --- check() NUL bytes in a text file -> invalid_content ---
    corrupt_head = base64.b64encode(b"hello\x00\x00binary garbage").decode("ascii")
    status, ci = _req("POST", "/index/upsert/file/check", {
        "corpus_id": CA, "path": "/d/a/corrupt.txt", "size": 200, "mtime": 1, "head": corrupt_head,
    })
    check("check NUL text -> invalid_content", ci.get("token") is None and ci.get("reason") == "invalid_content",
          json.dumps(ci))

    # --- check() supported type -> token + token_ttl_seconds (spec v1 #6) ---
    status, ck2 = _req("POST", "/index/upsert/file/check", {
        "corpus_id": CA, "path": "/d/a/notes.txt", "size": 64, "mtime": 2,
        "head": base64.b64encode(b"plain text notes").decode("ascii"),
    })
    tok = ck2.get("token")
    check("check supported -> token", tok is not None, json.dumps(ck2))
    check("check returns token_ttl_seconds", isinstance(ck2.get("token_ttl_seconds"), int) and ck2.get("token_ttl_seconds") > 0,
          str(ck2.get("token_ttl_seconds")))

    # --- two-phase upload: content/{token} with a valid token succeeds ---
    status, cc = _req_raw("POST", "/index/upsert/file/content/%s" % tok,
                          b"Revenue notes: the fourth quarter total was strong across regions.")
    check("two-phase content ok", status == 200 and cc.get("ok") and (cc.get("chunks") or 0) >= 1, json.dumps(cc))

    # --- unknown token -> 400 invalid_or_expired_upload_token ---
    status, bad = _req_raw("POST", "/index/upsert/file/content/deadbeefdeadbeef", b"orphan content")
    check("unknown token -> 400 invalid_or_expired",
          status == 400 and bad.get("error") == "invalid_or_expired_upload_token",
          "%s %s" % (status, json.dumps(bad)))

    # --- expired token -> 400 invalid_or_expired_upload_token (TTL is short here) ---
    status, ck3 = _req("POST", "/index/upsert/file/check", {
        "corpus_id": CA, "path": "/d/a/expire.txt", "size": 32, "mtime": 3,
        "head": base64.b64encode(b"expiring content").decode("ascii"),
    })
    tok_exp = ck3.get("token")
    ttl = ck3.get("token_ttl_seconds") or 2
    time.sleep(ttl + 0.6)
    status, exp = _req_raw("POST", "/index/upsert/file/content/%s" % tok_exp, b"too late content")
    check("expired token -> 400 invalid_or_expired",
          status == 400 and exp.get("error") == "invalid_or_expired_upload_token",
          "%s %s" % (status, json.dumps(exp)))

    # --- single-step content_b64 (supported type) ---
    payload = b"Security audit findings: two critical vulnerabilities were remediated this quarter."
    status, cs = _req("POST", "/index/upsert/file/content", {
        "corpus_id": CA, "path": "/d/a/audit.txt", "title": "Audit",
        "content_b64": base64.b64encode(payload).decode("ascii"),
    })
    check("single-step content ok", status == 200 and cs.get("ok") and (cs.get("chunks") or 0) >= 1, json.dumps(cs))

    # --- stats reflect documents ---
    status, st = _req("GET", "/stats")
    stats = st.get("stats", {})
    check("stats has corpora>=2", (stats.get("corpora") or 0) >= 2, json.dumps(stats))

    # --- drop/tree removes corpus A subtree, leaves corpus B intact ---
    status, dr = _req("POST", "/index/drop/tree", {"corpus_id": CA, "path": "/d/a", "keep_exact": False})
    check("drop/tree returns counts", dr.get("deleted_documents", 0) >= 2, json.dumps(dr))
    status, s3 = _req("POST", "/rag/search_context", {"corpus_id": CA, "question": "quarterly revenue", "top_k": 5})
    check("corpus A empty after drop", len(s3.get("contexts", [])) == 0)
    status, s4 = _req("POST", "/rag/search_context", {"corpus_id": CB, "question": "vacation policy", "top_k": 5})
    check("corpus B intact after A drop", len(s4.get("contexts", [])) >= 1, "n=%d" % len(s4.get("contexts", [])))

    # --- dropping the ROOT of a corpus must clear it (also what rebuild uses) ---
    status, dr2 = _req("POST", "/index/drop/tree", {"corpus_id": CB, "path": "/", "keep_exact": False})
    check("drop/tree at root deletes documents", dr2.get("deleted_documents", 0) >= 1, json.dumps(dr2))
    status, s5 = _req("POST", "/rag/search_context", {"corpus_id": CB, "question": "vacation policy", "top_k": 5})
    check("corpus B empty after root drop", len(s5.get("contexts", [])) == 0,
          "n=%d" % len(s5.get("contexts", [])))


def _min_score_tests():
    """With a floor of 0.99 nothing can clear it, so search must return empty.

    This is the mechanism behind refusing out-of-scope questions: no context
    means the generation layer has nothing to ground an answer on.
    """
    status, health = _req("GET", "/health")
    check("health reports min_score", health.get("min_score") == 0.99, str(health.get("min_score")))

    _req("POST", "/ingest", {
        "corpus_id": "corpus-floor", "path": "/d/f/doc.txt", "title": "Doc",
        "text": "Revenue for the fourth quarter reached forty two million dollars.",
    })
    status, sf = _req("POST", "/rag/search_context", {
        "corpus_id": "corpus-floor", "question": "quarterly revenue figure", "top_k": 5,
    })
    check("floor filters low-score contexts", status == 200 and len(sf.get("contexts", [])) == 0,
          "n=%d" % len(sf.get("contexts", [])))


def _reaccent_tests():
    """An accent-less Hungarian question must reach the accented document.

    Users type "napidij" for "napidíj". Without the repair the query embeds as
    a different word, and on the production index every hit fell under the
    relevance floor -- the box answered "no data" to a question it could answer.
    """
    status, health = _req("GET", "/health")
    check("health reports query_reaccent", health.get("query_reaccent") is True,
          str(health.get("query_reaccent")))

    _req("POST", "/ingest", {
        "corpus_id": "corpus-hu", "path": "/d/hu/utazas.txt", "title": "Utazási szabályzat",
        "text": "A napidíj mértéke Magyarországon 32 EUR/nap. "
                "A költségtérítési igényt 30 napon belül kell benyújtani.",
    })
    status, plain = _req("POST", "/rag/search_context", {
        "corpus_id": "corpus-hu", "question": "mennyi a napidij", "top_k": 5,
    })
    check("accent-less question finds the accented document",
          status == 200 and len(plain.get("contexts", [])) >= 1,
          "n=%d" % len(plain.get("contexts", [])))

    status, accented = _req("POST", "/rag/search_context", {
        "corpus_id": "corpus-hu", "question": "mennyi a napidíj", "top_k": 5,
    })
    plain_score = plain.get("contexts", [{}])[0].get("score")
    accented_score = accented.get("contexts", [{}])[0].get("score")
    check("both spellings score the same", plain_score == accented_score,
          "%s vs %s" % (plain_score, accented_score))


def _reaccent_off_tests():
    """Switched off, the accent-less question is embedded exactly as typed."""
    status, health = _req("GET", "/health")
    check("health reports query_reaccent off", health.get("query_reaccent") is False,
          str(health.get("query_reaccent")))

    _req("POST", "/ingest", {
        "corpus_id": "corpus-hu", "path": "/d/hu/utazas.txt", "title": "Utazási szabályzat",
        "text": "A napidíj mértéke Magyarországon 32 EUR/nap.",
    })
    status, plain = _req("POST", "/rag/search_context", {
        "corpus_id": "corpus-hu", "question": "mennyi a napidij", "top_k": 5,
    })
    status, accented = _req("POST", "/rag/search_context", {
        "corpus_id": "corpus-hu", "question": "mennyi a napidíj", "top_k": 5,
    })
    plain_score = plain.get("contexts", [{}])[0].get("score")
    accented_score = accented.get("contexts", [{}])[0].get("score")
    check("without the repair the spellings differ", plain_score != accented_score,
          "%s vs %s" % (plain_score, accented_score))


def _run_backend(backend, extra_env=None, tests=None, label=None):
    print("\n===== backend: %s =====" % (label or backend))
    tmpdir = tempfile.mkdtemp(prefix="ragsmoke-")
    ext = "db" if backend == "sqlite" else "json"
    index_path = os.path.join(tmpdir, "rag_index." + ext)
    env = dict(os.environ)
    env["VIVESEC_BACKEND"] = env.get("VIVESEC_BACKEND", "fallback")
    env["RAG_STORE_BACKEND"] = backend
    env["RAG_PORT"] = str(PORT)
    env["RAG_HOST"] = "127.0.0.1"
    env["RAG_INDEX_PATH"] = index_path
    env["RAG_TOKEN_TTL_SECONDS"] = "2"  # short TTL so the expiry test stays fast
    env.pop("RAG_API_KEY", None)  # run open for the smoke test
    env.update(extra_env or {})

    proc = subprocess.Popen(
        [sys.executable, os.path.join(HERE, "service.py")],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    try:
        wait_ready(proc)
        (tests or run_tests)()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()


def main():
    # Run the same contract suite against each store backend so both the JSON
    # (PoC) and the sqlite-vec backend stay contract-identical.
    backends = [b.strip() for b in os.environ.get("SMOKE_BACKENDS", "json,sqlite").split(",") if b.strip()]
    for backend in backends:
        _run_backend(backend)
        _run_backend(backend, extra_env={"RAG_MIN_SCORE": "0.99"},
                     tests=_min_score_tests, label="%s + min_score" % backend)
        _run_backend(backend, tests=_reaccent_tests, label="%s + reaccent" % backend)
        _run_backend(backend, extra_env={"RAG_QUERY_REACCENT": "off"},
                     tests=_reaccent_off_tests, label="%s + reaccent off" % backend)

    print("\n%d check(s) failed: %s" % (len(_failures), _failures) if _failures else "\nALL CHECKS PASSED")
    sys.exit(1 if _failures else 0)


if __name__ == "__main__":
    main()
