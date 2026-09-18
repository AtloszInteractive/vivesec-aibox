#!/usr/bin/env bash
# RAG deploy: spreadsheet extraction rewrite (openpyxl, sheet = page, row-aware
# chunking). Replaces the MarkItDown/pandas route that turned every empty cell
# into a literal "NaN" (27% of all spreadsheet tokens on the demo index).
#
# Safety contract (83-analyze-rag-deploy.sh pattern): rollback tag from the
# RUNNING image, sha-diff guard against unexpected file changes, unit tests +
# smoke test BEFORE the container is touched, env AND mounts inherited from
# the live container. The existing index is not modified: already-indexed
# spreadsheets keep their old chunks until the file is sent again
# (175-drive-sync-folder.py or a ViVeSecBox resync).
set -eu

TS=$(date +%Y%m%d-%H%M)
PREV="vivesec-rag:prev-$TS"
EXPECTED="extract.py store.py sqlite_store.py spreadsheet_test.py chunking.py"
LIVE_ID=$(docker inspect -f '{{.Image}}' vivesec-rag)

echo "=== 1. keep the live image for rollback ==="
docker tag "$LIVE_ID" "$PREV"
echo "  $PREV -> $(echo "$LIVE_ID" | cut -c8-19)"

echo
echo "=== 2. build from ~/rag-build ==="
cd "$HOME/rag-build"
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
for t in spreadsheet_test extract_pages_test skipped_test document_context_test \
         multi_corpus_test concurrency_test query_split_test reaccent_test; do
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
  vivesec-rag:new python /app/rag_service/smoke_test.py 2>&1 | tail -3

echo
echo "=== 6. promote + recreate (env + mounts inherited, key never printed) ==="
ENV_ARGS=()
while IFS= read -r line; do
  [ -n "$line" ] && ENV_ARGS+=(-e "$line")
done < <(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' vivesec-rag \
         | grep -E '^(RAG_|OLLAMA_|VIVESEC_)')
MOUNT_ARGS=()
while IFS= read -r line; do
  [ -n "$line" ] && MOUNT_ARGS+=(-v "$line")
done < <(docker inspect -f '{{range .Mounts}}{{.Source}}:{{.Destination}}{{println}}{{end}}' vivesec-rag)
RESTART=$(docker inspect -f '{{.HostConfig.RestartPolicy.Name}}' vivesec-rag)
echo "  mounts: ${MOUNT_ARGS[*]}  restart: $RESTART"

docker tag vivesec-rag:new vivesec-rag:latest
docker tag vivesec-rag:new "vivesec-rag:xlsx-$TS"
docker stop vivesec-rag >/dev/null
docker rm vivesec-rag >/dev/null
docker run -d --name vivesec-rag --network host --restart "${RESTART:-unless-stopped}" \
  "${MOUNT_ARGS[@]}" "${ENV_ARGS[@]}" vivesec-rag:latest >/dev/null
for _ in $(seq 1 30); do curl -sf -m 5 http://127.0.0.1:8090/health >/dev/null && break; sleep 1; done

echo
echo "=== 7. verify ==="
docker inspect -f '  running={{.State.Running}} image={{.Image}}' vivesec-rag | cut -c1-60
echo -n "  health: "; curl -fsS -m 20 http://127.0.0.1:8090/health || echo FAILED
echo
echo -n "  stats : "; curl -fsS -m 30 http://127.0.0.1:8090/stats | cut -c1-300 || echo FAILED
echo
echo -n "  adapter status: "; curl -fsS -m 10 http://127.0.0.1:8088/api/v1/status | cut -c1-200 || echo FAILED
echo
echo "  adapter drive/scope env:"
docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' vivesec-adapter \
  | grep -E '^ADAPTER_(DEMO_|ENTITLEMENTS|SCOPE|TENANT)' | grep -vi key | sed 's/^/    /'

echo
echo "ROLLBACK: docker tag $PREV vivesec-rag:latest && recreate with the same env/mounts"
