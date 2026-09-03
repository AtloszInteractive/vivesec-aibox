#!/usr/bin/env bash
# RAG deploy: whole-document retrieval for the #analyze action.
#
# Adds POST /rag/document_context — every chunk of ONE named document in
# reading order. Similarity search can only return what matches the question,
# so "analyse this file" must not go through it.
#
# Safety contract (same as 68-prod-deploy-reaccent.sh): rollback tag from the
# RUNNING image, sha-diff guard against unexpected file changes, unit tests
# BEFORE the container is touched, env inherited from the live container.
# No re-ingest: the endpoint reads the chunks already in the index.
set -eu

TS=$(date +%Y%m%d-%H%M)
PREV="vivesec-rag:prev-$TS"
EXPECTED="service.py store.py sqlite_store.py document_context_test.py"

echo "=== 1. keep the live image for rollback ==="
docker tag vivesec-rag:latest "$PREV"
echo "  $PREV -> $(docker inspect -f '{{.Id}}' "$PREV" | cut -c8-19)"

echo
echo "=== 2. build ==="
cd "$HOME/rag-build"
sha256sum rag_service/service.py rag_service/store.py rag_service/sqlite_store.py \
          rag_service/document_context_test.py | sed 's/^/  /'
docker build -q -f rag_service/Dockerfile -t vivesec-rag:new . >/dev/null
echo "  new image: $(docker inspect -f '{{.Id}}' vivesec-rag:new | cut -c8-19)"

echo
echo "=== 3. what actually changed vs the live image ==="
list_hashes() {
  docker run --rm --entrypoint sh "$1" -c \
    'for f in /app/rag_service/*.py /app/poc/*.py; do
       printf "%s %s\n" "${f##*/}" "$(sha256sum "$f" | cut -c1-16)"; done' | sort
}
list_hashes "$PREV" > /tmp/rag_prev.txt
list_hashes vivesec-rag:new > /tmp/rag_new.txt
CHANGED=$(diff /tmp/rag_prev.txt /tmp/rag_new.txt | awk '/^[<>]/ {print $2}' | sort -u)
echo "  changed: ${CHANGED:-<none>}"
for f in $CHANGED; do
  case " $EXPECTED " in
    *" $f "*) ;;
    *) echo "  ABORT: unexpected change in $f — the live container was NOT touched"; exit 1;;
  esac
done

echo
echo "=== 4. unit tests in the new image ==="
fail=0
for t in document_context_test reaccent_test query_split_test extract_pages_test \
         skipped_test concurrency_test; do
  printf '  %-24s ' "$t"
  if out=$(docker run --rm -e VIVESEC_BACKEND=fallback vivesec-rag:new \
             python "/app/rag_service/$t.py" 2>&1); then
    echo "$out" | tail -1
  else
    echo "FAILED"; echo "$out" | tail -5 | sed 's/^/      /'; fail=1
  fi
done
[ "$fail" -eq 0 ] || { echo "  ABORT: tests failed; nothing was promoted."; exit 1; }

echo
echo "=== 5. smoke test in the new image (own instance, real embeddings) ==="
docker run --rm --network host \
  -e OLLAMA_URL=http://localhost:11434 -e VIVESEC_BACKEND=auto \
  vivesec-rag:new python /app/rag_service/smoke_test.py 2>&1 | tail -6

echo
echo "=== 6. promote + recreate (env inherited, key never printed) ==="
docker tag vivesec-rag:new vivesec-rag:latest
ENV_ARGS=()
while IFS= read -r line; do
  [ -n "$line" ] && ENV_ARGS+=(-e "$line")
done < <(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' vivesec-rag \
         | grep -E '^(RAG_|OLLAMA_|VIVESEC_)')

docker stop vivesec-rag >/dev/null
docker rm vivesec-rag >/dev/null
docker run -d --name vivesec-rag --network host --restart unless-stopped \
  -v /data/rag:/data "${ENV_ARGS[@]}" vivesec-rag:latest >/dev/null
sleep 8

echo
echo "=== 7. verify ==="
docker inspect -f '  running={{.State.Running}} image={{.Image}}' vivesec-rag | cut -c1-60
echo -n "  health: "; curl -fsS -m 20 http://127.0.0.1:8090/health || echo FAILED
echo
echo -n "  stats : "
curl -fsS -m 30 -H "X-API-Key: $(cat ~/prod_rag_api_key.txt)" http://127.0.0.1:8090/stats || echo FAILED
echo
echo -n "  document_context reachable (expect ok/400, not 404): "
curl -s -o /dev/null -w '%{http_code}\n' -m 20 -X POST \
  -H "X-API-Key: $(cat ~/prod_rag_api_key.txt)" -H 'Content-Type: application/json' \
  -d '{}' http://127.0.0.1:8090/rag/document_context

echo
echo "ROLLBACK: docker tag $PREV vivesec-rag:latest && recreate"
