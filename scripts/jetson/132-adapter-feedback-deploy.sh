#!/usr/bin/env bash
# Adapter redeploy: VÁLASZ-ÉRTÉKELÉS (POST /api/v1/ui/feedback) + a köztes
# adapter-változások (llm.py, service.py) kivitele a demo boxra.
#
# A 108-as szkript biztonsági szerződése: build és TESZTEK az élő konténer
# érintése ELŐTT, rollback-tag az ÉLŐ image-re, majd recreate env/bind
# örökléssel. A feedback BIND-ot expliciten adjuk hozzá (öröklésnél nem lenne
# benne); a bind forrását a docker daemon hozza létre, ezért nem kell sudo.
#
#   STATUS_URL=http://127.0.0.1:80/api/v1/status   demo box (adapter a :80-on)
#   FEEDBACK_DIR=/data/feedback                    a JSONL-ek helye a hoston
set -eu

TS=$(date +%Y%m%d-%H%M)
SRC=${SRC:-$HOME/adapter-src}
STATUS_URL=${STATUS_URL:-http://127.0.0.1:8088/api/v1/status}
FEEDBACK_DIR=${FEEDBACK_DIR:-/data/feedback}

echo "=== 1. az image-be kerülő forrás ==="
cd "$SRC"
sha256sum feedback.py service.py llm.py voice.py smoke_test.py Dockerfile | sed 's/^/  /'

echo
echo "=== 2. build vivesec-adapter:new ==="
docker build -f Dockerfile -t vivesec-adapter:new . 2>&1 | tail -3

echo
echo "=== 3. tesztek AZ ÚJ IMAGE-BEN (bukásnál semmit nem cserélünk) ==="
set +e
voiceout=$(docker run --rm --entrypoint python vivesec-adapter:new /app/adapter/voice_test.py 2>&1); voicerc=$?
docgenout=$(docker run --rm --entrypoint python vivesec-adapter:new /app/adapter/docgen_test.py 2>&1); docgenrc=$?
smokeout=$(docker run --rm --entrypoint python vivesec-adapter:new /app/adapter/smoke_test.py 2>&1); smokerc=$?
set -e
echo "  voice : $(echo "$voiceout" | tail -1)"
echo "  docgen: $(echo "$docgenout" | tail -1)"
echo "  smoke : $(echo "$smokeout" | grep -E '^[0-9]+ passed' | tail -1)"
if [ $voicerc -ne 0 ] || [ $docgenrc -ne 0 ] || [ $smokerc -ne 0 ]; then
  echo "  ABORT: teszt bukott az új image-ben; az élő konténer érintetlen."
  exit 1
fi

echo
echo "=== 4. rollback-tag az ÉLŐ image-re ==="
live=$(docker inspect -f '{{.Image}}' vivesec-adapter)
docker tag "$live" "vivesec-adapter:prev-$TS"
echo "  vivesec-adapter:prev-$TS -> $(echo "$live" | cut -c8-19)"

echo
echo "=== 5. promote + recreate (env/bind örökölve, feedback-bind hozzáadva) ==="
docker tag vivesec-adapter:new vivesec-adapter:latest
envargs=()
while IFS= read -r e; do
  [ -n "$e" ] && envargs+=( -e "$e" )
done < <(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' vivesec-adapter \
         | grep -vE '^(PATH|LANG|GPG_KEY|PYTHON_VERSION|PYTHON_SHA256|PYTHONUNBUFFERED)=' \
         | grep -v '^ADAPTER_FEEDBACK_DIR=')
envargs+=( -e "ADAPTER_FEEDBACK_DIR=$FEEDBACK_DIR" )
bindargs=()
while IFS= read -r b; do
  [ -n "$b" ] && bindargs+=( -v "$b" )
done < <(docker inspect -f '{{range .HostConfig.Binds}}{{println .}}{{end}}' vivesec-adapter \
         | grep -v "^$FEEDBACK_DIR:")
bindargs+=( -v "$FEEDBACK_DIR:$FEEDBACK_DIR" )

docker stop vivesec-adapter >/dev/null
docker rm vivesec-adapter >/dev/null
docker run -d --name vivesec-adapter --network host --restart unless-stopped \
  "${envargs[@]}" "${bindargs[@]}" vivesec-adapter:latest >/dev/null
sleep 8

echo
echo "=== 6. igazolás ==="
echo -n "  ui_ready/fs_ready: "
curl -fsS -m 20 -X POST "$STATUS_URL" -H 'Content-Type: application/json' -d '{}' \
  | tr ',' '\n' | grep -E '"(ui_ready|fs_ready)"' | tr '\n' ' ' || echo FAILED
echo
echo -n "  feedback route él: "
docker exec vivesec-adapter grep -c '/api/v1/ui/feedback' /app/adapter/service.py
echo -n "  feedback könyvtár írható: "
docker exec vivesec-adapter python -c "import os;p=os.environ['ADAPTER_FEEDBACK_DIR'];os.makedirs(p,exist_ok=True);open(p+'/.probe','w').close();os.remove(p+'/.probe');print('OK',p)"

echo
echo "KÉSZ. Rollback:"
echo "  docker rm -f vivesec-adapter && docker tag vivesec-adapter:prev-$TS vivesec-adapter:latest"
echo "  majd újra a docker run a fenti env/bind listával."
