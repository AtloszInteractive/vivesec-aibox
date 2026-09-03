#!/usr/bin/env bash
# TTS motor a boxra: Piper (CPU) egy pici saját HTTP wrapperrel, loopback-only.
# A hivatalos piper http_server flask-ot kér és EGY hangot szolgál ki; a UI négy
# nyelven válaszol, ezért kell a nyelvenként választható hang.
#
# Forrás a boxon: ~/piper-src (piper_http.py + Dockerfile.piper)
#   bash 107-tts-deploy.sh
set -eu

PORT=${TTS_PORT:-8102}
NAME=vivesec-tts
SRC=${SRC:-$HOME/piper-src}
VOICES=${VOICES:-"en_US-lessac-medium hu_HU-anna-medium"}

echo "=== 1. build (a hangmodellek a build alatt töltődnek le) ==="
cd "$SRC"
docker build -f Dockerfile.piper --build-arg VOICES="$VOICES" -t vivesec-piper:latest . 2>&1 | tail -5

echo
echo "=== 2. korábbi példány ==="
docker rm -f "$NAME" >/dev/null 2>&1 && echo "  régi törölve" || echo "  nem futott"

echo
echo "=== 3. indítás :$PORT (CSAK loopback) ==="
docker run -d --name "$NAME" --restart unless-stopped \
  -p 127.0.0.1:$PORT:5000 vivesec-piper:latest >/dev/null

ok=0
for i in $(seq 1 30); do
  curl -sf -m 5 "http://127.0.0.1:$PORT/health" >/dev/null 2>&1 && { ok=1; break; }
  sleep 2
done
if [ "$ok" != "1" ]; then
  echo "  NEM ÁLL FEL. Log:"; docker logs --tail 30 "$NAME" 2>&1 | sed 's/^/    /'; exit 1
fi
echo -n "  health: "; curl -s -m 5 "http://127.0.0.1:$PORT/health"; echo

echo
echo "=== 4. valódi szintézis-teszt ==="
for spec in "en_US-lessac-medium|The revenue in the second quarter of 2026 was 14.7 million euros." \
            "hu_HU-anna-medium|A flotta rendelkezésre állása 2026 áprilisában 99,85 százalék volt."; do
  v="${spec%%|*}"; t="${spec#*|}"
  out="/tmp/tts_$v.wav"
  start=$(date +%s%N)
  code=$(curl -s -o "$out" -w '%{http_code}' -m 120 -X POST "http://127.0.0.1:$PORT/?voice=$v" \
         -H 'Content-Type: text/plain; charset=utf-8' --data-binary "$t")
  end=$(date +%s%N)
  ms=$(( (end - start) / 1000000 ))
  hdr=$(head -c4 "$out" 2>/dev/null || true)
  echo "  $v -> HTTP $code, $(wc -c <"$out") bájt, ${ms} ms, fejléc='$hdr'"
done

echo
echo "=== 5. végpont az adapternek ==="
echo "  ADAPTER_TTS_URL=http://127.0.0.1:$PORT/"
echo "  ADAPTER_TTS_VOICES=hu=hu_HU-anna-medium,en=en_US-lessac-medium"
echo
echo "Leállítás:  docker rm -f $NAME"
