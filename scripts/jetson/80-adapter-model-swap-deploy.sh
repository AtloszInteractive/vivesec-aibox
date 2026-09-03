#!/usr/bin/env bash
# Adapter redeploy: LLM swap qwen2.5:14b -> qwen3.6:35b (+ADAPTER_THINK=off,
# without it the reply lands in the thinking field and content comes back
# empty), question-language detection (llm.detect_lang -> localized guard,
# refusal and system messages in hu/en/da/de), NUM_PREDICT raised to 2048
# (the 35b is wordier; decks were cut at 1024).
#
# Same safety contract as 76-adapter-deck-deploy.sh: build + unit tests BEFORE
# touching anything live, rollback tag, container recreated with env/binds
# inherited from the running one (RAG API key inherited, never printed).
# The model/think/num_predict keys are explicitly OVERRIDDEN because the old
# container's env would otherwise carry the old model forward.
#
#   STATUS_URL=http://127.0.0.1:80/api/v1/status   demo box (adapter on :80)
set -eu

TS=$(date +%Y%m%d-%H%M)
SRC=${SRC:-$HOME/adapter-src}
STATUS_URL=${STATUS_URL:-http://127.0.0.1:8088/api/v1/status}
GEN_MODEL=${GEN_MODEL:-qwen3.6:35b}
THINK=${THINK:-off}
NUM_PREDICT=${NUM_PREDICT:-2048}

echo "=== 0. model present? ==="
ollama list | grep -q "^${GEN_MODEL%%:*}" || { echo "  ABORT: $GEN_MODEL not in ollama list"; exit 1; }
ollama list | sed 's/^/  /'

echo
echo "=== 1. source going into the image ==="
cd "$SRC"
sha256sum llm.py confidence.py service.py smoke_test.py Dockerfile | sed 's/^/  /'

echo
echo "=== 2. build vivesec-adapter:new ==="
docker build -f Dockerfile -t vivesec-adapter:new . 2>&1 | tail -3

echo
echo "=== 3. unit tests inside the new image ==="
set +e
docgenout=$(docker run --rm --entrypoint python vivesec-adapter:new /app/adapter/docgen_test.py 2>&1)
docgenstatus=$?
testout=$(docker run --rm --entrypoint python vivesec-adapter:new /app/adapter/smoke_test.py 2>&1)
teststatus=$?
set -e
echo "$docgenout" | tail -3
echo "$testout" | tail -3
if [ "$docgenstatus" -ne 0 ] || [ "$teststatus" -ne 0 ]; then
  echo "  ABORT: tests failed in the new image; nothing was promoted."
  exit 1
fi

echo
echo "=== 4. keep the live image for rollback ==="
docker tag vivesec-adapter:latest "vivesec-adapter:prev-$TS"
echo "  vivesec-adapter:prev-$TS -> $(docker inspect -f '{{.Id}}' "vivesec-adapter:prev-$TS" | cut -c8-19)"

echo
echo "=== 5. promote + recreate (env/binds inherited, model keys overridden) ==="
docker tag vivesec-adapter:new vivesec-adapter:latest
envargs=()
while IFS= read -r e; do
  [ -n "$e" ] && envargs+=( -e "$e" )
done < <(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' vivesec-adapter \
         | grep -vE '^(PATH|LANG|GPG_KEY|PYTHON_VERSION|PYTHON_SHA256|PYTHONUNBUFFERED)=' \
         | grep -vE '^(ADAPTER_GEN_MODEL|ADAPTER_THINK|ADAPTER_NUM_PREDICT)=')
envargs+=( -e "ADAPTER_GEN_MODEL=$GEN_MODEL" -e "ADAPTER_THINK=$THINK" -e "ADAPTER_NUM_PREDICT=$NUM_PREDICT" )
bindargs=()
while IFS= read -r b; do
  [ -n "$b" ] && bindargs+=( -v "$b" )
done < <(docker inspect -f '{{range .HostConfig.Binds}}{{println .}}{{end}}' vivesec-adapter)

docker stop vivesec-adapter >/dev/null
docker rm vivesec-adapter >/dev/null
docker run -d --name vivesec-adapter --network host --restart unless-stopped \
  "${envargs[@]}" "${bindargs[@]}" vivesec-adapter:latest >/dev/null
sleep 8

echo -n "  status: "; curl -fsS -m 20 "$STATUS_URL" | head -c 140 || echo FAILED
echo
echo "  model env:"
docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' vivesec-adapter \
  | grep -E '^(ADAPTER_GEN_MODEL|ADAPTER_THINK|ADAPTER_NUM_PREDICT)=' | sed 's/^/    /'
echo -n "  detect_lang live: "
docker exec vivesec-adapter grep -c "def detect_lang" /app/adapter/llm.py || echo 0

echo
echo "=== rollback ==="
echo "  docker rm -f vivesec-adapter && docker run -d --name vivesec-adapter \\"
echo "    --network host --restart unless-stopped <same env/binds, old model env> vivesec-adapter:prev-$TS"
