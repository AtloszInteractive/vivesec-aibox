#!/usr/bin/env bash
# F1 deploy: persistent multi-thread conversations (adapter side).
#
# New: adapter/conversations.py (ConversationStore) + five POST endpoints under
# /api/v1/ui/conversations/*, `conversation_id` on /ui/query and /ui/ask, and a
# short post-answer title call. The `default` thread IS the old session file, so
# a client that does not send conversation_id behaves exactly as before.
#
# No new bind: threads live under ADAPTER_SESSION_DIR (already bound). The
# retention knobs are baked into the image and set explicitly here.
#
# Safety contract (same as 84-analyze-adapter-deploy.sh): diff the source going
# into the image against the RUNNING image first and stop on anything
# unexpected, run the tests INSIDE the new image before touching anything live,
# keep a rollback tag, inherit env + binds.
#
# NOTE: recreating the adapter drops the ws-fs channel for ~2 minutes -> never
# run this while a drive sync is in progress.
set -eu

TS=$(date +%Y%m%d-%H%M)
SRC=${SRC:-$HOME/adapter-f1}
STATUS_URL=${STATUS_URL:-http://127.0.0.1:8088/api/v1/status}
CONVERSATION_MAX_TURNS=${CONVERSATION_MAX_TURNS:-200}
CONVERSATION_RETENTION_DAYS=${CONVERSATION_RETENTION_DAYS:-90}
CONVERSATIONS_PER_USER=${CONVERSATIONS_PER_USER:-50}
# Files this change is allowed to touch. Besides F1 itself this also carries the
# three commits the dev box is behind on since the 2026-09-12 release (mTLS
# listener + SSDP LOCATION fixes already verified on the prod boxes, peer-IP
# logging, docx export); anything OUTSIDE this list means the staging dir holds
# work that was never reviewed for this deploy.
EXPECTED="Dockerfile chat_policy_test.py conversations.py conversations_http_test.py conversations_test.py discovery.py docgen.py docgen_test.py llm.py service.py session.py smoke_test.py"

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
echo "=== 2. build vivesec-adapter:f1-$TS ==="
cd "$SRC"
docker build -q -f Dockerfile -t "vivesec-adapter:f1-$TS" . >/dev/null
echo "  new image: $(docker inspect -f '{{.Id}}' "vivesec-adapter:f1-$TS" | cut -c8-19)"

echo
echo "=== 3. tests inside the new image ==="
set +e
convout=$(docker run --rm --entrypoint sh "vivesec-adapter:f1-$TS" -c \
  'cd /app/adapter && python -m unittest conversations_test conversations_http_test chat_policy_test 2>&1' )
convstatus=$?
docgenout=$(docker run --rm --entrypoint python "vivesec-adapter:f1-$TS" /app/adapter/docgen_test.py 2>&1)
docgenstatus=$?
smokeout=$(docker run --rm --entrypoint python "vivesec-adapter:f1-$TS" /app/adapter/smoke_test.py 2>&1)
smokestatus=$?
set -e
echo "$convout" | tail -3 | sed 's/^/  /'
echo "$docgenout" | tail -2 | sed 's/^/  /'
echo "$smokeout" | tail -2 | sed 's/^/  /'
if [ "$convstatus" -ne 0 ] || [ "$docgenstatus" -ne 0 ] || [ "$smokestatus" -ne 0 ]; then
  echo "  ABORT: tests failed in the new image; nothing was promoted."
  exit 1
fi

echo
echo "=== 4. keep the live image for rollback ==="
docker tag "$live" "vivesec-adapter:prev-$TS"
echo "  vivesec-adapter:prev-$TS -> $(docker inspect -f '{{.Id}}' "vivesec-adapter:prev-$TS" | cut -c8-19)"

echo
echo "=== 5. promote + recreate (env/binds inherited) ==="
docker tag "vivesec-adapter:f1-$TS" vivesec-adapter:latest
envargs=()
while IFS= read -r e; do
  [ -n "$e" ] && envargs+=( -e "$e" )
done < <(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' vivesec-adapter \
         | grep -vE '^(PATH|LANG|GPG_KEY|PYTHON_VERSION|PYTHON_SHA256|PYTHONUNBUFFERED)=' \
         | grep -vE '^ADAPTER_(CONVERSATION_MAX_TURNS|CONVERSATION_RETENTION_DAYS|CONVERSATIONS_PER_USER)=')
envargs+=( -e "ADAPTER_CONVERSATION_MAX_TURNS=$CONVERSATION_MAX_TURNS" \
           -e "ADAPTER_CONVERSATION_RETENTION_DAYS=$CONVERSATION_RETENTION_DAYS" \
           -e "ADAPTER_CONVERSATIONS_PER_USER=$CONVERSATIONS_PER_USER" )
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
echo -n "  status sessions block: "
curl -fsS -m 20 "$STATUS_URL" | python3 -c 'import json,sys; print(json.load(sys.stdin)["sessions"])' || echo FAILED
echo "  conversation env:"
docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' vivesec-adapter \
  | grep -E '^ADAPTER_(SESSION|CONVERSATION)' | sort | sed 's/^/    /'

echo
echo "ROLLBACK: docker tag vivesec-adapter:prev-$TS vivesec-adapter:latest; then rerun step 5's recreate."
