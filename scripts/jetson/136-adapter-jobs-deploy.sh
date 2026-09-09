#!/usr/bin/env bash
# Deploy persistent background jobs (F1-F2 backend) to the dev AI Box.
set -eu

TS=$(date +%Y%m%d-%H%M)
SRC=${SRC:-$HOME/adapter-jobs-src}
STATUS_URL=${STATUS_URL:-http://127.0.0.1:8088/api/v1/status}
JOBS_DIR=${JOBS_DIR:-/data/jobs}
IMAGE_NEW=vivesec-adapter:jobs-new
ROLLBACK=vivesec-adapter:prev-$TS

echo "=== 1. source and live configuration ==="
cd "$SRC"
sha256sum jobstore.py scheduler.py service.py Dockerfile | sed 's/^/  /'
live=$(docker inspect -f '{{.Image}}' vivesec-adapter)
restart=$(docker inspect -f '{{.HostConfig.RestartPolicy.Name}}' vivesec-adapter)
network=$(docker inspect -f '{{.HostConfig.NetworkMode}}' vivesec-adapter)
echo "  live image: $live"
echo "  restart=$restart network=$network"
[ "$network" = "host" ] || { echo "ABORT: unexpected network mode"; exit 1; }

echo
echo "=== 2. build candidate ==="
docker build -f Dockerfile -t "$IMAGE_NEW" . 2>&1 | tail -4

echo
echo "=== 3. tests in candidate image ==="
run_test() {
  name=$1
  script=$2
  set +e
  output=$(docker run --rm --entrypoint python "$IMAGE_NEW" "$script" 2>&1)
  rc=$?
  set -e
  echo "  $name: $(echo "$output" | tail -2 | tr '\n' ' ')"
  [ $rc -eq 0 ] || { echo "$output"; echo "ABORT: $name failed"; exit 1; }
}
run_test voice /app/adapter/voice_test.py
run_test docgen /app/adapter/docgen_test.py
run_test scheduler /app/adapter/scheduler_test.py
run_test stream /app/adapter/llm_stream_test.py
run_test smoke /app/adapter/smoke_test.py

echo
echo "=== 4. prepare inherited env and binds ==="
envargs=()
while IFS= read -r value; do
  [ -n "$value" ] && envargs+=( -e "$value" )
done < <(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' vivesec-adapter \
  | grep -vE '^(PATH|LANG|GPG_KEY|PYTHON_VERSION|PYTHON_SHA256|PYTHONUNBUFFERED)=' \
  | grep -vE '^ADAPTER_(JOBS_DIR|JOB_RETENTION_DAYS|JOB_MAX_PER_USER|JOB_QUEUE_MAX)=')
envargs+=( -e "ADAPTER_JOBS_DIR=$JOBS_DIR" \
           -e "ADAPTER_JOB_RETENTION_DAYS=7" \
           -e "ADAPTER_JOB_MAX_PER_USER=50" \
           -e "ADAPTER_JOB_QUEUE_MAX=5" )

bindargs=()
while IFS= read -r bind; do
  [ -n "$bind" ] && bindargs+=( -v "$bind" )
done < <(docker inspect -f '{{range .HostConfig.Binds}}{{println .}}{{end}}' vivesec-adapter \
  | grep -vE ":${JOBS_DIR}(:|$)")
bindargs+=( -v "$JOBS_DIR:$JOBS_DIR" )

echo "  inherited env: ${#envargs[@]} arguments"
echo "  inherited binds + jobs: ${#bindargs[@]} arguments"

echo
echo "=== 5. rollback tag and swap ==="
docker tag "$live" "$ROLLBACK"
docker tag "$IMAGE_NEW" vivesec-adapter:latest
docker stop vivesec-adapter >/dev/null
docker rm vivesec-adapter >/dev/null
docker run -d --name vivesec-adapter --network host --restart "${restart:-unless-stopped}" \
  "${envargs[@]}" "${bindargs[@]}" vivesec-adapter:latest >/dev/null
sleep 8

echo
echo "=== 6. post-deploy verification ==="
curl -fsS -m 20 -X POST "$STATUS_URL" -H 'Content-Type: application/json' -d '{}' >/dev/null
echo "  status: OK"
docker exec vivesec-adapter python -c \
  "import os; p=os.environ['ADAPTER_JOBS_DIR']; os.makedirs(p,exist_ok=True); open(p+'/.probe','w').close(); os.remove(p+'/.probe'); print('  jobs writable:',p)"
code=$(curl -s -o /tmp/jobs-probe.json -w '%{http_code}' -m 20 \
  -H 'VVS-Drive: L3N0b3JhZ2UvZHJpdmVzL2VuZ2luZWVyaW5nLw' -H 'VVS-User: deploy-probe' \
  http://127.0.0.1:8088/api/v1/ui/jobs)
[ "$code" = "200" ] || { cat /tmp/jobs-probe.json; echo; echo "ABORT: jobs route HTTP $code"; exit 1; }
echo "  jobs route: HTTP 200"
echo "  image: $(docker inspect -f '{{.Image}}' vivesec-adapter)"
echo
echo "ROLLBACK_IMAGE=$ROLLBACK"
echo "ROLLBACK: recreate vivesec-adapter from $ROLLBACK with the inherited env/binds above"