#!/usr/bin/env bash
# Adapter redeploy: VOICE MODE (/ui/stt + /ui/tts + status.voice) + a frissített
# operations persona szövege (a system prompt doksi 1. szakasza: warm/proactive,
# tipó-tolerancia).
#
# A 80/84-es szkriptek biztonsági szerződése: build és TESZTEK az élő konténer
# érintése ELŐTT, rollback-tag, majd recreate env/bind örökléssel. A voice
# kulcsokat EXPLICITEN adjuk hozzá (öröklésnél nem lennének benne).
set -eu

TS=$(date +%Y%m%d-%H%M)
SRC=${SRC:-$HOME/adapter-src}
STATUS_URL=${STATUS_URL:-http://127.0.0.1:8088/api/v1/status}
STT_URL=${STT_URL:-http://127.0.0.1:8101/v1/audio/transcriptions}
STT_MODEL=${STT_MODEL:-Systran/faster-whisper-small}
TTS_URL=${TTS_URL:-http://127.0.0.1:8102/}
TTS_VOICES=${TTS_VOICES:-hu=hu_HU-anna-medium,en=en_US-lessac-medium}

echo "=== 0. beszédmotorok élnek-e (enélkül nincs értelme) ==="
code=$(curl -s -o /dev/null -w '%{http_code}' -m 10 -X POST "$STT_URL" || true)
echo "  STT $STT_URL -> HTTP $code"
[ "$code" = "000" ] && { echo "  ABORT: az STT nem válaszol"; exit 1; }
curl -sf -m 10 "${TTS_URL%/}/health" >/dev/null || { echo "  ABORT: a TTS nem válaszol"; exit 1; }
echo "  TTS $TTS_URL -> OK"

echo
echo "=== 1. az image-be kerülő forrás ==="
cd "$SRC"
sha256sum voice.py service.py llm.py smoke_test.py Dockerfile | sed 's/^/  /'

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
echo "  smoke : $(echo "$smokeout" | tail -1)"
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
echo "=== 5. promote + recreate (env/bind örökölve, voice-kulcsok hozzáadva) ==="
docker tag vivesec-adapter:new vivesec-adapter:latest
envargs=()
while IFS= read -r e; do
  [ -n "$e" ] && envargs+=( -e "$e" )
done < <(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' vivesec-adapter \
         | grep -vE '^(PATH|LANG|GPG_KEY|PYTHON_VERSION|PYTHON_SHA256|PYTHONUNBUFFERED)=' \
         | grep -vE '^ADAPTER_(STT_URL|STT_MODEL|TTS_URL|TTS_VOICES)=')
envargs+=( -e "ADAPTER_STT_URL=$STT_URL" -e "ADAPTER_STT_MODEL=$STT_MODEL" \
           -e "ADAPTER_TTS_URL=$TTS_URL" -e "ADAPTER_TTS_VOICES=$TTS_VOICES" )
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
echo "=== 6. igazolás ==="
echo -n "  status.voice: "
curl -fsS -m 20 -X POST "$STATUS_URL" -H 'Content-Type: application/json' -d '{}' \
  | tr '{},' '\n' | grep -iA3 '"voice"' | tr '\n' ' ' || echo FAILED
echo
echo -n "  ui_ready: "
curl -fsS -m 20 -X POST "$STATUS_URL" -H 'Content-Type: application/json' -d '{}' \
  | tr ',' '\n' | grep -E '"(ui_ready|fs_ready)"' | tr '\n' ' '
echo
echo
echo "KÉSZ. Rollback:  docker rm -f vivesec-adapter && docker tag vivesec-adapter:prev-$TS vivesec-adapter:latest  majd újra a docker run a régi env-vel."
