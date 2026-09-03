#!/usr/bin/env bash
# Adapter deploy: conversational chat mode + operations persona.
#
# What ships: llm.py (condense() follow-up rewriter, chat-only branch,
# history-aware guard, PERSONAS + operations persona), service.py (condense
# wiring, agent field), confidence.py (history-aware adherence, conversational
# scoring), smoke_test.py (300 checks). No new env keys are required:
# ADAPTER_CHAT defaults to on ("off" restores the old one-shot behaviour) and
# the condenser fails open to the original question on any error.
#
# Same safety contract as 84-analyze-adapter-deploy.sh: build + unit tests
# BEFORE anything live is touched, rollback tag, env/binds inherited.
set -eu

TS=$(date +%Y%m%d-%H%M)
SRC=${SRC:-$HOME/adapter-src}
STATUS_URL=${STATUS_URL:-http://127.0.0.1:8088/api/v1/status}

echo "=== 1. source going into the image ==="
cd "$SRC"
sha256sum service.py llm.py confidence.py smoke_test.py Dockerfile | sed 's/^/  /'

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
echo "=== 5. promote + recreate (env/binds inherited) ==="
docker tag vivesec-adapter:new vivesec-adapter:latest
envargs=()
while IFS= read -r e; do
  [ -n "$e" ] && envargs+=( -e "$e" )
done < <(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' vivesec-adapter \
         | grep -vE '^(PATH|LANG|GPG_KEY|PYTHON_VERSION|PYTHON_SHA256|PYTHONUNBUFFERED)=')
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
  | grep -E '^ADAPTER_(GEN_MODEL|THINK|NUM_PREDICT|NUM_CTX|CHAT|AGENT)' | sed 's/^/    /'

echo
echo "ROLLBACK: docker tag vivesec-adapter:prev-$TS vivesec-adapter:latest && recreate"
