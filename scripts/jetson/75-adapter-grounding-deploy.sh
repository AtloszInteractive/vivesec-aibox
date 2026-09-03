#!/usr/bin/env bash
# Adapter redeploy: grounding rule (verbatim dates/figures) + graded number
# adherence, so one reformatted date no longer suppresses a whole report.
#
# Safe by construction: the image is built and unit-tested BEFORE anything
# running is touched, the live image is retagged for rollback, and the
# container is recreated with the env/binds captured from the running one
# (the RAG API key is inherited, never printed).
set -eu

TS=$(date +%Y%m%d-%H%M)
SRC=/home/aibox/adapter-src

echo "=== 1. source going into the image ==="
cd "$SRC"
sha256sum confidence.py llm.py service.py smoke_test.py | sed 's/^/  /'

echo
echo "=== 2. build vivesec-adapter:new ==="
docker build -f Dockerfile -t vivesec-adapter:new . 2>&1 | tail -3

echo
echo "=== 3. unit tests inside the new image ==="
# The Dockerfile copies the adapter dir to /app/adapter (WORKDIR /app).
# Keep the exit status: a pipe into tail would always report success.
set +e
testout=$(docker run --rm --entrypoint python vivesec-adapter:new /app/adapter/smoke_test.py 2>&1)
teststatus=$?
set -e
echo "$testout" | tail -3
if [ "$teststatus" -ne 0 ]; then
  echo "  ABORT: tests failed in the new image; nothing was promoted."
  exit 1
fi

echo
echo "=== 4. keep the live image for rollback ==="
docker tag vivesec-adapter:latest "vivesec-adapter:prev-$TS"
echo "  vivesec-adapter:prev-$TS -> $(docker inspect -f '{{.Id}}' "vivesec-adapter:prev-$TS" | cut -c8-19)"

echo
echo "=== 5. promote + recreate (env and binds inherited) ==="
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

echo -n "  status: "; curl -fsS -m 20 -X POST http://127.0.0.1:8088/api/v1/status -d '{}' | head -c 120 || echo FAILED
echo
echo -n "  num_predict: "; docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' vivesec-adapter | grep '^ADAPTER_NUM_PREDICT=' || echo "(unset)"

echo
echo "=== rollback ==="
echo "  docker rm -f vivesec-adapter && docker run -d --name vivesec-adapter \\"
echo "    --network host --restart unless-stopped <same env/binds> vivesec-adapter:prev-$TS"
