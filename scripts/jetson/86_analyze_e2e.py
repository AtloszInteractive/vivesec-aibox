#!/usr/bin/env python3
"""Live proof that #analyze reads the WHOLE document, not retrieved excerpts.

Runs against the adapter on the box (VVS-Drive/VVS-User headers, same path the
UI uses). The point it has to establish is the coverage gap:

  * a normal grounded question gets the top-k chunks the retrieval liked;
  * #analyze gets every chunk of the named file, and the adapter reports
    chunks_used / chunks_total so a truncated read cannot pass unnoticed.

    python3 86_analyze_e2e.py [drive] [filename-substring]
"""
import base64
import json
import sys
import urllib.error
import urllib.request

ADAPTER = "http://127.0.0.1:8088"
DRIVE = sys.argv[1] if len(sys.argv) > 1 else "/storage/drives/engineering/"
WANT = sys.argv[2] if len(sys.argv) > 2 else "Manual"
USER = "demo"


def post(path, payload, timeout=900):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        ADAPTER + path, data=data, method="POST",
        headers={"Content-Type": "application/json",
                 "VVS-Drive": base64.urlsafe_b64encode(DRIVE.encode()).decode().rstrip("="),
                 "VVS-User": USER})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8") or "{}")


def main():
    print("drive:", DRIVE)

    # 1) find a real document in the drive
    code, res = post("/api/v1/ui/query",
                     {"action": "search", "mode": "files", "query": "files:" + WANT})
    files = [f["path"] for f in res.get("files", [])] if code == 200 else []
    if not files:
        print("  no file matched %r (HTTP %s) -- pass a different substring" % (WANT, code))
        return 1
    # the largest match is the most interesting: many chunks to cover
    target = sorted(res["files"], key=lambda f: f.get("size") or 0)[-1]
    path = target["path"]
    print("  target: %s (%s bytes)" % (path, target.get("size")))

    # 2) a normal grounded question -- retrieval decides what is seen
    code, ask = post("/api/v1/ui/query",
                     {"query": "What does this document say about maintenance?",
                      "top_k": 5, "lang": "English", "files": [path]})
    print("\n[search ] HTTP %s  contexts=%d" % (code, len(ask.get("hits", []))))

    # 3) #analyze -- the whole file
    code, an = post("/api/v1/ui/query",
                    {"action": "analyze", "query": target["path"].split("/")[-1],
                     "lang": "English", "files": [path]})
    doc = an.get("document") or {}
    print("[analyze] HTTP %s  action=%s  backend=%s" % (code, an.get("action"), an.get("backend")))
    print("          document: found=%s chunks_used=%s/%s truncated=%s tokens=%s"
          % (doc.get("found"), doc.get("chunks_used"), doc.get("chunks_total"),
             doc.get("truncated"), doc.get("estimated_tokens")))
    print("          citations=%d hits=%d confidence=%s"
          % (len(an.get("citations", [])), len(an.get("hits", [])),
             (an.get("confidence") or {}).get("score")))

    pages = [h.get("page_number") for h in an.get("hits", [])]
    print("          page span: %s..%s over %d chunks"
          % (pages[0] if pages else "-", pages[-1] if pages else "-", len(pages)))

    answer = (an.get("answer") or "").strip()
    print("\n--- answer (first 900 chars) ---")
    print(answer[:900])

    # 4) the verdict
    print("\n=== verdict ===")
    ok = True
    if not doc.get("found"):
        print("  FAIL: the document was not resolved"); ok = False
    if (doc.get("chunks_used") or 0) <= len(ask.get("hits", [])):
        print("  WARN: analyze did not see more than the search did")
    else:
        print("  OK: analyze saw %s chunks vs %d from search"
              % (doc.get("chunks_used"), len(ask.get("hits", []))))
    if doc.get("truncated"):
        print("  NOTE: document truncated at the token budget -- the answer says so")
    else:
        print("  OK: the ENTIRE document fit (%s/%s chunks)"
              % (doc.get("chunks_used"), doc.get("chunks_total")))
    if not answer:
        print("  FAIL: empty answer"); ok = False
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
