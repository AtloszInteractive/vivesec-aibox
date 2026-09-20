#!/usr/bin/env bash
# Adapter deploy: delete a finished background job (POST /api/v1/ui/jobs/delete).
#
# jobstore.delete() removes one terminal job from the caller's (user, drive)
# scope and refuses while it is queued or running; the job id is validated as a
# single file name so it can never escape the scope directory.
#
# Safety contract (same as 179-adapter-conversations-deploy.sh): diff the source
# going into the image against the RUNNING image first and stop on anything
# unexpected, run the tests INSIDE the new image before touching anything live,
# keep a rollback tag, inherit env + binds.
#
# NOTE: recreating the adapter drops the ws-fs channel for ~2 minutes -> never
# run this while a drive sync is in progress.
set -eu

TS=$(date +%Y%m%d-%H%M)
SRC=${SRC:-$HOME/adapter-jobdel}
STATUS_URL=${STATUS_URL:-http://127.0.0.1:8088/api/v1/status}
# The adapter has not changed since the F1 deploy apart from these three files.
EXPECTED="jobstore.py scheduler_http_test.py service.py"

echo "=== 0. preflight: nothing may be syncing through the adapter ==="
if pgrep -f 'drive-sync-folder\.py' >/dev/null; then
  echo "  ABORT: a drive sync is running; the adapter restart would cut its ws-fs channel."
  exit 1
fi
echo "  no drive sync running"

echo
echo "=== 1. what changes vs the RUNNING image ==="
live=$(docker inspect -f '{{.Config.Image}}' vivesec-adapter)
echo "  live image: $live"
newsums=$(cd "$SRC" && sha256sum ./*.py Dockerfile | sed 's#\./##')
livesums=$(docker run --rm --entrypoint sh "$live" -c 'cd /app/adapter && sha256sum ./*.py Dockerfile 2>/dev/null' | sed 's#\./##')
changed=$(diff <(echo "$livesums" | awk '{print $2, $1}' | sort) \
               <(echo "$newsums"  | awk '{print $2, $1}' | sort) \
          | awk '/^[<>]/ {print $2}' | sort -u)
echo "$changed" | sed 's/^/    /'
for f in $changed; do
  case " $EXPECTED " in
    *" $f "*) ;;
    *) echo "  ABORT: unexpected difference in $f"; exit 1;;
  esac
done
echo "  only the expected files differ"

echo
echo "=== 2. build vivesec-adapter:jobdel-$TS ==="
cd "$SRC"
docker build -q -f Dockerfile -t "vivesec-adapter:jobdel-$TS" . >/dev/null
echo "  new image: $(docker inspect -f '{{.Id}}' "vivesec-adapter:jobdel-$TS" | cut -c8-19)"

echo
echo "=== 3. tests inside the new image ==="
set +e
unitout=$(docker run --rm --entrypoint sh "vivesec-adapter:jobdel-$TS" -c \
  'cd /app/adapter && python -m unittest scheduler_http_test scheduler_test conversations_http_test 2>&1')
unitstatus=$?
docgenout=$(docker run --rm --entrypoint python "vivesec-adapter:jobdel-$TS" /app/adapter/docgen_test.py 2>&1)
docgenstatus=$?
smokeout=$(docker run --rm --entrypoint python "vivesec-adapter:jobdel-$TS" /app/adapter/smoke_test.py 2>&1)
smokestatus=$?
set -e
echo "$unitout" | tail -3 | sed 's/^/  /'
echo "$docgenout" | tail -2 | sed 's/^/  /'
echo "$smokeout" | tail -2 | sed 's/^/  /'
if [ "$unitstatus" -ne 0 ] || [ "$docgenstatus" -ne 0 ] || [ "$smokestatus" -ne 0 ]; then
  echo "  ABORT: tests failed in the new image; nothing was promoted."
  exit 1
fi

echo
echo "=== 4. keep the live image for rollback ==="
docker tag "$live" "vivesec-adapter:prev-$TS"
echo "  vivesec-adapter:prev-$TS -> $(docker inspect -f '{{.Id}}' "vivesec-adapter:prev-$TS" | cut -c8-19)"

echo
echo "=== 5. promote + recreate (env/binds inherited) ==="
docker tag "vivesec-adapter:jobdel-$TS" vivesec-adapter:latest
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
docker inspect -f '  running={{.State.Running}} image={{.Config.Image}}' vivesec-adapter
echo -n "  status ok: "
curl -fsS -m 20 "$STATUS_URL" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("ok"), d.get("jobs"))' || echo FAILED
echo -n "  delete route answers (404 for an unknown id, not 'Not found: path'): "
curl -s -m 20 -o /tmp/jobdel.json -w '%{http_code} ' \
  -H 'Content-Type: application/json' \
  -H "VVS-User: deploy-probe" \
  -H "VVS-Drive: $(printf '/storage/drives/engineering/' | base64 -w0 | tr '+/' '-_' | tr -d '=')" \
  -d '{"job_id":"deploy-probe-missing"}' \
  http://127.0.0.1:8088/api/v1/ui/jobs/delete
cat /tmp/jobdel.json; echo
rm -f /tmp/jobdel.json
echo "  job env:"
docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' vivesec-adapter \
  | grep -E '^ADAPTER_JOB' | sort | sed 's/^/    /'

echo
echo "ROLLBACK: docker tag vivesec-adapter:prev-$TS vivesec-adapter:latest; then rerun step 5's recreate."
