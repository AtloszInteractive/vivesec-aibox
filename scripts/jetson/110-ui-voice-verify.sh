#!/usr/bin/env bash
# Záró ellenőrzés: amit a BÖNGÉSZŐ lát a UI-n keresztül (nem közvetlenül az adapteren).
set -u
UI=${UI:-http://127.0.0.1:8080}

echo "=== 1. UI él ==="
curl -s -o /dev/null -w '  HTTP %{http_code}\n' -m 20 "$UI/"

echo
echo "=== 2. /api/v1/status a UI proxyján át (ezt olvassa a mikrofon-gomb) ==="
curl -s -m 20 -X POST "$UI/api/v1/status" -H 'Content-Type: application/json' -d '{}' \
  | tr '{},' '\n' | grep -iE '"(voice|stt|tts|stt_model|ui_ready)"' | sed 's/^/  /'

echo
echo "=== 3. /api/v1/ui/tts a UI proxyján át ==="
code=$(curl -s -o /tmp/ui_tts.wav -w '%{http_code}' -m 120 -X POST "$UI/api/v1/ui/tts" \
       -H 'Content-Type: application/json' \
       -d '{"text":"The fleet availability in April 2026 was 99.85 percent.","lang":"English"}')
echo "  HTTP $code, $(wc -c </tmp/ui_tts.wav) bájt, fejléc='$(head -c4 /tmp/ui_tts.wav)'"

echo
echo "=== 4. /api/v1/ui/stt a UI proxyján át ==="
if [ -f "$HOME/stt_probe_fleet.wav" ]; then
  python3 - "$HOME/stt_probe_fleet.wav" > /tmp/ui_stt.json <<'PY'
import base64, json, sys
print(json.dumps({"audio_b64": base64.b64encode(open(sys.argv[1],"rb").read()).decode(),
                  "content_type": "audio/wav", "lang": "English"}))
PY
  curl -s -m 300 -X POST "$UI/api/v1/ui/stt" -H 'Content-Type: application/json' \
       --data-binary @/tmp/ui_stt.json | sed 's/^/  /'
  echo
else
  echo "  (nincs próbafájl)"
fi

echo
echo "=== 5. a hangmód kódja benne van-e a kiszolgált bundle-ben ==="
grep -rl 'ui/stt' "$HOME/ui-app/.output/server" 2>/dev/null | sed 's|.*/|  |' || echo "  NEM TALÁLHATÓ"

echo
echo "=== 6. voice konténerek ==="
docker ps --filter name=vivesec-stt --filter name=vivesec-tts --format '  {{.Names}} | {{.Status}} | {{.Ports}}'
