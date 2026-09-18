#!/usr/bin/env bash
# Spreadsheet-extraction A/B on a box: builds the candidate rag image from a
# staged source tree, runs TWO disposable rag instances (live image = baseline,
# candidate = new) with empty indexes on loopback, ingests the same xlsx files
# into both via the single-step content endpoint and prints extraction stats +
# side-by-side search hits. Touches nothing that is live.
#
#   bash 173-xlsx-ab-test.sh prepare   # build candidate image + start both instances
#   bash 173-xlsx-ab-test.sh ingest    # ingest ~/xlsx-ab/files/* into both
#   bash 173-xlsx-ab-test.sh search "kérdés" ["kérdés2" ...]
#   bash 173-xlsx-ab-test.sh stop      # remove both instances
set -euo pipefail
STAGE=${STAGE:-$HOME/xlsx-ab}
SRC=${SRC:-$HOME/rag-build}          # repo-root layout: poc/ + rag_service/
LIVE_IMG=$(docker inspect vivesec-rag --format '{{.Image}}')
CAND_TAG=vivesec-rag:xlsx-candidate
BASE_PORT=${BASE_PORT:-8111}
CAND_PORT=${CAND_PORT:-8112}
KEY_FILE=$STAGE/api_key.txt
CORPUS=xlsx-ab
mkdir -p "$STAGE/files" "$STAGE/base" "$STAGE/cand"
[ -f "$KEY_FILE" ] || python3 -c 'import secrets;print(secrets.token_hex(16))' > "$KEY_FILE"
KEY=$(cat "$KEY_FILE")

run_instance() { # name image port datadir
  docker rm -f "$1" >/dev/null 2>&1 || true
  docker run -d --name "$1" --network host --restart no \
    -e RAG_HOST=127.0.0.1 -e RAG_PORT="$3" -e RAG_API_KEY="$KEY" \
    -e RAG_STORE_BACKEND=sqlite -e RAG_INDEX_PATH=/data/index.db \
    -e OLLAMA_URL=http://127.0.0.1:11434 -e VIVESEC_BACKEND=auto -e RAG_MIN_SCORE=0 \
    -v "$4":/data "$2" >/dev/null
  for _ in $(seq 1 30); do
    curl -sf "http://127.0.0.1:$3/health" >/dev/null && return 0; sleep 1
  done
  echo "instance $1 did not come up"; docker logs "$1" | tail -20; return 1
}

case "${1:-}" in
  prepare)
    echo "live image: $LIVE_IMG"
    ( cd "$SRC" && docker build -q -f rag_service/Dockerfile -t "$CAND_TAG" . )
    echo "candidate: $(docker inspect $CAND_TAG --format '{{.Id}}' | cut -c8-19)"
    docker run --rm "$CAND_TAG" python rag_service/spreadsheet_test.py 2>&1 | tail -3
    docker run --rm "$CAND_TAG" python rag_service/extract_pages_test.py 2>&1 | tail -1
    run_instance xlsx-ab-base "$LIVE_IMG" "$BASE_PORT" "$STAGE/base"
    run_instance xlsx-ab-cand "$CAND_TAG" "$CAND_PORT" "$STAGE/cand"
    echo "base :$BASE_PORT  cand :$CAND_PORT  ready"
    ;;
  ingest)
    python3 - "$KEY" "$BASE_PORT" "$CAND_PORT" "$STAGE/files" "$CORPUS" <<'PY'
import base64, json, os, sys, time, urllib.request
key, bp, cp, folder, corpus = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5]
def post(port, path, body):
    req = urllib.request.Request("http://127.0.0.1:%s%s" % (port, path), data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", "X-API-Key": key})
    with urllib.request.urlopen(req, timeout=1800) as r:
        return json.loads(r.read().decode())
print("%-45s %14s %14s" % ("file", "base pg/chunks", "cand pg/chunks"))
tot = {bp: [0, 0, 0.0], cp: [0, 0, 0.0]}
for name in sorted(os.listdir(folder)):
    raw = open(os.path.join(folder, name), "rb").read()
    row = []
    for port in (bp, cp):
        t = time.time()
        try:
            res = post(port, "/index/upsert/file/content",
                       {"corpus_id": corpus, "tenant_id": "default", "path": "/storage/drives/ab/" + name,
                        "content_b64": base64.b64encode(raw).decode()})
            pg, ch = res.get("pages", 0), res.get("chunks", 0)
        except Exception as e:  # noqa: BLE001
            pg, ch = "ERR", str(e)[:30]
        dt = time.time() - t
        if isinstance(ch, int):
            tot[port][0] += pg; tot[port][1] += ch; tot[port][2] += dt
        row.append("%s/%s (%.0fs)" % (pg, ch, dt))
    print("%-45s %14s %14s" % (name[:45], row[0], row[1]))
print("%-45s %14s %14s" % ("TOTAL pages/chunks (s)", "%d/%d (%.0fs)" % tuple(tot[bp]), "%d/%d (%.0fs)" % tuple(tot[cp])))
PY
    ;;
  search)
    shift
    python3 - "$KEY" "$BASE_PORT" "$CAND_PORT" "$CORPUS" "$@" <<'PY'
import json, sys, urllib.request
key, bp, cp, corpus = sys.argv[1:5]
def post(port, body):
    req = urllib.request.Request("http://127.0.0.1:%s/rag/search_context" % port, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", "X-API-Key": key})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read().decode())
for q in sys.argv[5:]:
    print("=" * 100); print("Q:", q)
    for label, port in (("BASE", bp), ("CAND", cp)):
        res = post(port, {"corpus_id": corpus, "tenant_id": "default", "question": q, "top_k": 3})
        print("--", label)
        for c in res.get("contexts", []):
            t = c["text"].replace("\n", " ⏎ ")
            print("  %.3f  %s  p%s [%s]  NaN=%d" % (c["score"], c["source_path"].rsplit("/", 1)[-1][:40],
                  c.get("page_number"), c.get("section_path"), c["text"].count("NaN")))
            print("        " + t[:300])
PY
    ;;
  stop)
    docker rm -f xlsx-ab-base xlsx-ab-cand >/dev/null 2>&1 || true
    echo "instances removed (stage $STAGE kept)"
    ;;
  *) echo "usage: $0 prepare|ingest|search q...|stop"; exit 2 ;;
esac
