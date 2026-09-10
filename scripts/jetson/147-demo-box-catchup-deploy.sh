#!/usr/bin/env bash
# Demo box (10.10.10.85) catch-up deploy: RAG multi-corpus + adapter (jobs,
# scheduler, streaming/cancel, scope, get-file, prompt upgrade).
#
# Shaped after 138-scope-deploy.sh, with three demo-box differences:
#   * the adapter listens on :80 (the ViVeSecBox connects there after SSDP),
#   * the persistent job store needs a NEW bind that the old container lacks,
#   * the sources live in ~/adapter-src and ~/rag-build.
#
# RAG is promoted first: a missing `corpus_ids` still means the single corpus,
# so the box is never asked for something the RAG cannot answer.
set -eu

TS=$(date +%Y%m%d-%H%M)
RAG_SRC=${RAG_SRC:-$HOME/rag-build}
ADAPTER_SRC=${ADAPTER_SRC:-$HOME/adapter-src}
STATUS_URL=${STATUS_URL:-http://127.0.0.1:80/api/v1/status}
JOBS_DIR=${JOBS_DIR:-/data/jobs}
RAG_PREV="vivesec-rag:prev-$TS"
ADAPTER_PREV="vivesec-adapter:prev-$TS"

echo "=== 0. live containers ==="
for c in vivesec-rag vivesec-adapter; do
  net=$(docker inspect -f '{{.HostConfig.NetworkMode}}' "$c")
  echo "  $c image=$(docker inspect -f '{{.Image}}' "$c" | cut -c8-19) network=$net"
  [ "$net" = "host" ] || { echo "ABORT: $c is not on the host network"; exit 1; }
done
echo "  index before: $(curl -s --max-time 10 http://127.0.0.1:8090/stats | head -c 200)"

echo
echo "=== 1. rollback tags ==="
docker tag "$(docker inspect -f '{{.Image}}' vivesec-rag)" "$RAG_PREV"
docker tag "$(docker inspect -f '{{.Image}}' vivesec-adapter)" "$ADAPTER_PREV"
echo "  $RAG_PREV"
echo "  $ADAPTER_PREV"

echo
echo "=== 2. build RAG candidate ==="
cd "$RAG_SRC"
docker build -q -f rag_service/Dockerfile -t vivesec-rag:catchup . >/dev/null
echo "  $(docker inspect -f '{{.Id}}' vivesec-rag:catchup | cut -c8-19)"

echo
echo "=== 3. RAG tests inside the candidate ==="
fail=0
for t in multi_corpus_test document_context_test reaccent_test query_split_test \
         extract_pages_test skipped_test concurrency_test; do
  printf '  %-24s ' "$t"
  if out=$(docker run --rm -e VIVESEC_BACKEND=fallback vivesec-rag:catchup \
             python "/app/rag_service/$t.py" 2>&1); then
    echo "$out" | tail -1
  else
    echo "FAILED"; echo "$out" | tail -8 | sed 's/^/      /'; fail=1
  fi
done
[ "$fail" -eq 0 ] || { echo "  ABORT: RAG tests failed; nothing was promoted."; exit 1; }

echo
echo "=== 4. promote RAG (env + binds inherited) ==="
ragenv=()
while IFS= read -r e; do
  [ -n "$e" ] && ragenv+=( -e "$e" )
done < <(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' vivesec-rag \
         | grep -E '^(RAG_|OLLAMA_|VIVESEC_)')
ragbinds=()
while IFS= read -r b; do
  [ -n "$b" ] && ragbinds+=( -v "$b" )
done < <(docker inspect -f '{{range .HostConfig.Binds}}{{println .}}{{end}}' vivesec-rag)
docker tag vivesec-rag:catchup vivesec-rag:latest
docker rm -f vivesec-rag >/dev/null
docker run -d --name vivesec-rag --network host --restart unless-stopped \
  "${ragenv[@]}" "${ragbinds[@]}" vivesec-rag:latest >/dev/null
sleep 5
echo "  health: $(curl -s --max-time 15 http://127.0.0.1:8090/health | head -c 220)"
echo "  stats : $(curl -s --max-time 15 http://127.0.0.1:8090/stats | head -c 220)"

echo
echo "=== 5. build adapter candidate ==="
cd "$ADAPTER_SRC"
sha256sum scope.py jobstore.py scheduler.py service.py llm.py wsfs.py | sed 's/^/  /'
docker build -q -f Dockerfile -t vivesec-adapter:catchup . >/dev/null
echo "  $(docker inspect -f '{{.Id}}' vivesec-adapter:catchup | cut -c8-19)"

echo
echo "=== 6. adapter tests inside the candidate ==="
for t in scope_test voice_test docgen_test scheduler_test llm_stream_test smoke_test; do
  printf '  %-18s ' "$t"
  if out=$(docker run --rm --entrypoint python vivesec-adapter:catchup \
             "/app/adapter/$t.py" 2>&1); then
    echo "$out" | tail -1
  else
    echo "FAILED"; echo "$out" | tail -10 | sed 's/^/      /'
    echo "  ABORT: adapter tests failed; RAG is already promoted, adapter is NOT."
    exit 1
  fi
done

echo
echo "=== 7. promote adapter (env + binds inherited, job store added) ==="
aenv=()
while IFS= read -r e; do
  [ -n "$e" ] && aenv+=( -e "$e" )
done < <(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' vivesec-adapter \
         | grep -vE '^(PATH|LANG|GPG_KEY|PYTHON_VERSION|PYTHON_SHA256|PYTHONUNBUFFERED)=')
abinds=()
while IFS= read -r b; do
  [ -n "$b" ] && abinds+=( -v "$b" )
done < <(docker inspect -f '{{range .HostConfig.Binds}}{{println .}}{{end}}' vivesec-adapter)
# The persistent job store is new on this box: without the bind the queue would
# live inside the container and vanish on the next recreate.
abinds+=( -v "$JOBS_DIR:$JOBS_DIR" )
aenv+=( -e "ADAPTER_JOBS_DIR=$JOBS_DIR" )
docker tag vivesec-adapter:catchup vivesec-adapter:latest
docker rm -f vivesec-adapter >/dev/null
docker run -d --name vivesec-adapter --network host --restart unless-stopped \
  "${aenv[@]}" "${abinds[@]}" vivesec-adapter:latest >/dev/null
sleep 6

echo
echo "=== 8. verify ==="
echo "--- status"
curl -s --max-time 20 "$STATUS_URL" | head -c 700
echo
echo "--- scope block present?"
curl -s --max-time 20 "$STATUS_URL" | grep -o '"scope":[^}]*}' || echo "  MISSING"
echo "--- get-file endpoint (400 = present, 404 = missing)"
DRIVE=$(printf '/storage/drives/aiboxdev/' | base64 | tr -d '=' | tr '+/' '-_')
curl -s -o /dev/null -w '  HTTP %{http_code}\n' --max-time 15 \
  -H "VVS-Drive: $DRIVE" -H 'VVS-User: demo' \
  "http://127.0.0.1:80/api/v1/ui/file"
echo "--- job store"
ls -la "$JOBS_DIR" 2>/dev/null | head -3 || echo "  (empty)"
echo "--- num_ctx in effect"
docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' vivesec-adapter | grep -E 'NUM_CTX|JOBS_DIR' | sed 's/^/  /'
echo "--- ws-fs channel"
curl -s --max-time 15 "$STATUS_URL" | grep -o '"ws_fs":[^}]*}'

echo
echo "ROLLBACK:"
echo "  docker rm -f vivesec-rag     && docker run -d --name vivesec-rag --network host --restart unless-stopped <env/binds> $RAG_PREV"
echo "  docker rm -f vivesec-adapter && docker run -d --name vivesec-adapter --network host --restart unless-stopped <env/binds> $ADAPTER_PREV"
echo "done."
