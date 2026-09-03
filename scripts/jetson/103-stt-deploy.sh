#!/usr/bin/env bash
# STT motor a boxra: faster-whisper-server (arm64, CPU int8), loopback-only.
# A böngésző WebM/Opus felvételét ez a build ffmpeg-gel dekódolja, ezért
# alkalmas — a whisper.cpp szerver nem (16 kHz WAV-ot vár), és arm64 sincs.
#
#   bash 103-stt-deploy.sh              small modell (ajánlott)
#   STT_MODEL=Systran/faster-whisper-base bash 103-stt-deploy.sh
set -eu

PORT=${STT_PORT:-8101}
MODEL=${STT_MODEL:-Systran/faster-whisper-small}
IMAGE=${STT_IMAGE:-fedirz/faster-whisper-server:latest-cpu}
NAME=vivesec-stt
# /data sudót kér a boxon -> a modell-cache a HOME-ba megy (mint a rag-demo).
CACHE=${STT_CACHE:-$HOME/voice/hf-cache}

echo "=== 1. image ==="
docker image inspect "$IMAGE" >/dev/null 2>&1 || docker pull "$IMAGE"
docker image inspect -f '  {{.Id}} / {{.Architecture}} / {{.Os}}' "$IMAGE"

echo
echo "=== 2. korábbi példány eltakarítása ==="
docker rm -f "$NAME" >/dev/null 2>&1 && echo "  régi $NAME törölve" || echo "  nem futott"
mkdir -p "$CACHE"

echo
echo "=== 3. indítás :$PORT (CSAK loopback) ==="
docker run -d --name "$NAME" --restart unless-stopped \
  -p 127.0.0.1:$PORT:8000 \
  -v "$CACHE:/root/.cache/huggingface" \
  -e WHISPER__MODEL="$MODEL" \
  -e WHISPER__INFERENCE_DEVICE=cpu \
  -e WHISPER__COMPUTE_TYPE=int8 \
  -e WHISPER__TTL=-1 \
  -e ENABLE_UI=false \
  "$IMAGE" >/dev/null
echo "  elindítva, modell: $MODEL"

echo
echo "=== 4. várakozás az API-ra (a modell letöltése tarthat pár percig) ==="
ok=0
for i in $(seq 1 90); do
  code=$(curl -s -o /dev/null -w '%{http_code}' -m 5 "http://127.0.0.1:$PORT/health" || true)
  [ "$code" = "200" ] && { ok=1; break; }
  code=$(curl -s -o /dev/null -w '%{http_code}' -m 5 "http://127.0.0.1:$PORT/v1/models" || true)
  [ "$code" = "200" ] && { ok=1; break; }
  sleep 4
done
if [ "$ok" != "1" ]; then
  echo "  NEM ÁLL FEL. Utolsó 30 sor log:"
  docker logs --tail 30 "$NAME" 2>&1 | sed 's/^/    /'
  exit 1
fi
echo "  API él."

echo
echo "=== 5. valódi beszéd-teszt (espeak-ng-vel generált mondat) ==="
if command -v espeak-ng >/dev/null 2>&1; then
  espeak-ng -v en -s 150 -w /tmp/stt_probe.wav "What was the revenue in the second quarter of 2026?"
  echo -n "  átirat: "
  curl -s -m 180 -X POST "http://127.0.0.1:$PORT/v1/audio/transcriptions" \
    -F "file=@/tmp/stt_probe.wav" -F "language=en" -F "response_format=json"
  echo
else
  echo "  (espeak-ng nincs telepítve -> a hangos próba a UI-ból megy)"
fi

echo
echo "=== 6. végpont az adapternek ==="
echo "  ADAPTER_STT_URL=http://127.0.0.1:$PORT/v1/audio/transcriptions"
echo "  ADAPTER_STT_MODEL=$MODEL"
echo
echo "Leállítás:  docker rm -f $NAME"
