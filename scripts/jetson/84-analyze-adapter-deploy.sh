#!/usr/bin/env bash
# Adapter deploy: the #analyze quick action (whole-document analysis).
#
# The action bypasses similarity retrieval and reads the named file end to end
# via the RAG's /rag/document_context. Three settings matter and are set here
# explicitly, because Ollama SILENTLY truncates an oversized prompt — without a
# matching window the tail of the document would be analysed as if it did not
# exist. Measured on this box: a bigger window costs ~1 GB and no speed, while
# the RAG's word-based token estimate underestimates dense logs by 6.4x, so the
# real bound is the character cap.
#   ADAPTER_ANALYZE_NUM_CTX             model context window for that call
#   ADAPTER_ANALYZE_MAX_CHARS           real bound on how much document is sent
#   ADAPTER_ANALYZE_MAX_CONTEXT_TOKENS  outer bound on the RAG response
#
# Same safety contract as 80-adapter-model-swap-deploy.sh: build + unit tests
# BEFORE anything live is touched, rollback tag, env/binds inherited.
set -eu

TS=$(date +%Y%m%d-%H%M)
SRC=${SRC:-$HOME/adapter-src}
STATUS_URL=${STATUS_URL:-http://127.0.0.1:8088/api/v1/status}
ANALYZE_MAX_CONTEXT_TOKENS=${ANALYZE_MAX_CONTEXT_TOKENS:-60000}
ANALYZE_MAX_CHARS=${ANALYZE_MAX_CHARS:-100000}
ANALYZE_NUM_CTX=${ANALYZE_NUM_CTX:-65536}

echo "=== 1. source going into the image ==="
cd "$SRC"
sha256sum service.py llm.py Dockerfile | sed 's/^/  /'

echo
echo "=== 2. build vivesec-adapter:new ==="
docker build -q -f Dockerfile -t vivesec-adapter:new . >/dev/null
echo "  new image: $(docker inspect -f '{{.Id}}' vivesec-adapter:new | cut -c8-19)"

echo
echo "=== 3. unit tests inside the new image ==="
set +e
docgenout=$(docker run --rm --entrypoint python vivesec-adapter:new /app/adapter/docgen_test.py 2>&1)
docgenstatus=$?
testout=$(docker run --rm --entrypoint python vivesec-adapter:new /app/adapter/smoke_test.py 2>&1)
teststatus=$?
set -e
echo "$docgenout" | tail -2 | sed 's/^/  /'
echo "$testout" | tail -2 | sed 's/^/  /'
if [ "$docgenstatus" -ne 0 ] || [ "$teststatus" -ne 0 ]; then
  echo "  ABORT: tests failed in the new image; nothing was promoted."
  exit 1
fi

echo
echo "=== 4. keep the live image for rollback ==="
docker tag vivesec-adapter:latest "vivesec-adapter:prev-$TS"
echo "  vivesec-adapter:prev-$TS -> $(docker inspect -f '{{.Id}}' "vivesec-adapter:prev-$TS" | cut -c8-19)"

echo
echo "=== 5. promote + recreate (env/binds inherited, analyze keys set) ==="
docker tag vivesec-adapter:new vivesec-adapter:latest
envargs=()
while IFS= read -r e; do
  [ -n "$e" ] && envargs+=( -e "$e" )
done < <(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' vivesec-adapter \
         | grep -vE '^(PATH|LANG|GPG_KEY|PYTHON_VERSION|PYTHON_SHA256|PYTHONUNBUFFERED)=' \
         | grep -vE '^(ADAPTER_ANALYZE_MAX_CONTEXT_TOKENS|ADAPTER_ANALYZE_MAX_CHARS|ADAPTER_ANALYZE_NUM_CTX)=')
envargs+=( -e "ADAPTER_ANALYZE_MAX_CONTEXT_TOKENS=$ANALYZE_MAX_CONTEXT_TOKENS" \
           -e "ADAPTER_ANALYZE_MAX_CHARS=$ANALYZE_MAX_CHARS" \
           -e "ADAPTER_ANALYZE_NUM_CTX=$ANALYZE_NUM_CTX" )
bindargs=()
while IFS= read -r b; do
  [ -n "$b" ] && bindargs+=( -v "$b" )
done < <(docker inspect -f '{{range .HostConfig.Binds}}{{println .}}{{end}}' vivesec-adapter)

docker stop vivesec-adapter >/dev/null
docker rm vivesec-adapter >/dev/null
docker run -d --name vivesec-adapter --network host --restart unless-stopped \
  "${envargs[@]}" "${bindargs[@]}" vivesec-adapter:latest >/dev/null
sleep 8

echo
echo "=== 6. verify ==="
docker inspect -f '  running={{.State.Running}} image={{.Image}}' vivesec-adapter | cut -c1-60
echo -n "  status: "; curl -fsS -m 20 "$STATUS_URL" | head -c 120 || echo FAILED
echo
echo "  generation env:"
docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' vivesec-adapter \
  | grep -E '^ADAPTER_(GEN_MODEL|THINK|NUM_PREDICT|NUM_CTX|ANALYZE)' | sed 's/^/    /'

echo
echo "ROLLBACK: docker tag vivesec-adapter:prev-$TS vivesec-adapter:latest && recreate"
