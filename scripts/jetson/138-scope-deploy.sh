#!/usr/bin/env bash
# Multi-drive read scope (VVS-Other-Drives) -> dev AI Box.
#
# Two coupled-but-compatible pieces: the RAG service learns `corpus_ids`, the
# adapter learns to resolve and narrow a scope. RAG goes first because it stays
# backward compatible (a missing corpus_ids simply means the single corpus), so
# the box is never in a state where the adapter asks for something the RAG
# cannot answer.
#
# Without the box header nothing changes: the scope resolves to the active
# VVS-Drive, which is exactly today's behaviour.
set -eu

TS=$(date +%Y%m%d-%H%M)
RAG_SRC=${RAG_SRC:-$HOME/rag-build}
ADAPTER_SRC=${ADAPTER_SRC:-$HOME/adapter-jobs-src}
STATUS_URL=${STATUS_URL:-http://127.0.0.1:8088/api/v1/status}
RAG_PREV="vivesec-rag:prev-$TS"
ADAPTER_PREV="vivesec-adapter:prev-$TS"

echo "=== 0. live containers ==="
for c in vivesec-rag vivesec-adapter; do
  net=$(docker inspect -f '{{.HostConfig.NetworkMode}}' "$c")
  echo "  $c image=$(docker inspect -f '{{.Image}}' "$c" | cut -c8-19) network=$net"
  [ "$net" = "host" ] || { echo "ABORT: $c is not on the host network"; exit 1; }
done

echo
echo "=== 1. rollback tags ==="
docker tag "$(docker inspect -f '{{.Image}}' vivesec-rag)" "$RAG_PREV"
docker tag "$(docker inspect -f '{{.Image}}' vivesec-adapter)" "$ADAPTER_PREV"
echo "  $RAG_PREV"
echo "  $ADAPTER_PREV"

echo
echo "=== 2. build RAG candidate ==="
cd "$RAG_SRC"
docker build -q -f rag_service/Dockerfile -t vivesec-rag:scope-new . >/dev/null
echo "  $(docker inspect -f '{{.Id}}' vivesec-rag:scope-new | cut -c8-19)"

echo
echo "=== 3. RAG tests in the candidate image ==="
fail=0
for t in multi_corpus_test document_context_test reaccent_test query_split_test \
         extract_pages_test skipped_test concurrency_test; do
  printf '  %-24s ' "$t"
  if out=$(docker run --rm -e VIVESEC_BACKEND=fallback vivesec-rag:scope-new \
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
docker tag vivesec-rag:scope-new vivesec-rag:latest
docker rm -f vivesec-rag >/dev/null
docker run -d --name vivesec-rag --network host --restart unless-stopped \
  "${ragenv[@]}" "${ragbinds[@]}" vivesec-rag:latest >/dev/null
sleep 4
echo "  health: $(curl -s --max-time 10 http://127.0.0.1:8090/health | head -c 200)"

echo
echo "=== 5. build adapter candidate ==="
cd "$ADAPTER_SRC"
sha256sum scope.py service.py | sed 's/^/  /'
docker build -q -f Dockerfile -t vivesec-adapter:scope-new . >/dev/null
echo "  $(docker inspect -f '{{.Id}}' vivesec-adapter:scope-new | cut -c8-19)"

echo
echo "=== 6. adapter tests in the candidate image ==="
for t in scope_test voice_test docgen_test scheduler_test smoke_test; do
  printf '  %-16s ' "$t"
  if out=$(docker run --rm --entrypoint python vivesec-adapter:scope-new \
             "/app/adapter/$t.py" 2>&1); then
    echo "$out" | tail -1
  else
    echo "FAILED"; echo "$out" | tail -8 | sed 's/^/      /'
    echo "  ABORT: adapter tests failed; RAG is already promoted, adapter is NOT."
    exit 1
  fi
done

echo
echo "=== 7. promote adapter (env + binds inherited) ==="
aenv=()
while IFS= read -r e; do
  [ -n "$e" ] && aenv+=( -e "$e" )
done < <(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' vivesec-adapter \
         | grep -vE '^(PATH|LANG|GPG_KEY|PYTHON_VERSION|PYTHON_SHA256|PYTHONUNBUFFERED)=')
abinds=()
while IFS= read -r b; do
  [ -n "$b" ] && abinds+=( -v "$b" )
done < <(docker inspect -f '{{range .HostConfig.Binds}}{{println .}}{{end}}' vivesec-adapter)
docker tag vivesec-adapter:scope-new vivesec-adapter:latest
docker rm -f vivesec-adapter >/dev/null
docker run -d --name vivesec-adapter --network host --restart unless-stopped \
  "${aenv[@]}" "${abinds[@]}" vivesec-adapter:latest >/dev/null
sleep 5

echo
echo "=== 8. verify ==="
echo "  status: $(curl -s --max-time 15 "$STATUS_URL" | head -c 260)"
echo
echo "ROLLBACK:"
echo "  docker rm -f vivesec-rag     && docker run -d --name vivesec-rag ... $RAG_PREV"
echo "  docker rm -f vivesec-adapter && docker run -d --name vivesec-adapter ... $ADAPTER_PREV"
