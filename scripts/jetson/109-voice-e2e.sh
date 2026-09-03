#!/usr/bin/env bash
# Voice end-to-end AZ ADAPTEREN KERESZTÜL (nem közvetlenül a motorokon):
#   1) /api/v1/ui/stt  valódi WAV -> szöveg
#   2) a felismert szöveg -> /api/v1/ui/ask + /ui/poll  (grounded válasz)
#   3) /api/v1/ui/tts  a válasz -> WAV
# Ez a teljes lánc, amit a UI mikrofon-gombja bejár.
set -u
ADAPTER=${ADAPTER:-http://127.0.0.1:8088}
DRIVE=${DRIVE:-/storage/drives/engineering/}
USERID=${USERID:-demo}
WAV=${1:-$HOME/stt_probe_en.wav}
DRIVE_B64=$(printf '%s' "$DRIVE" | base64 -w0 | tr '+/' '-_' | tr -d '=')
H_DRIVE="VVS-Drive: $DRIVE_B64"
H_USER="VVS-User: $USERID"

echo "=== 1. /ui/stt  ($(basename "$WAV")) ==="
python3 - "$WAV" > /tmp/stt_body.json <<'PY'
import base64, json, sys
data = open(sys.argv[1], "rb").read()
json.dump({"audio_b64": base64.b64encode(data).decode(),
           "content_type": "audio/wav", "lang": "English"}, sys.stdout)
PY
start=$(date +%s%N)
sttout=$(curl -s -m 300 -X POST "$ADAPTER/api/v1/ui/stt" -H 'Content-Type: application/json' \
         -H "$H_DRIVE" -H "$H_USER" --data-binary @/tmp/stt_body.json)
end=$(date +%s%N)
echo "  ${sttout}"
echo "  ($(( (end-start)/1000000 )) ms)"

QUESTION=$(printf '%s' "$sttout" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("text",""))')
if [ -z "$QUESTION" ]; then echo "  ABORT: üres átirat"; exit 1; fi

echo
echo "=== 2. a felismert kérdés végigfut a normál grounded pipeline-on ==="
echo "  kérdés: $QUESTION"
askout=$(curl -s -m 60 -X POST "$ADAPTER/api/v1/ui/ask" -H 'Content-Type: application/json' \
         -H "$H_DRIVE" -H "$H_USER" -d "$(python3 -c 'import json,sys; print(json.dumps({"query": sys.argv[1]}))' "$QUESTION")")
job=$(printf '%s' "$askout" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("job_id",""))')
if [ -z "$job" ]; then echo "  ABORT: nincs job_id: $askout"; exit 1; fi
ANSWER=""
for i in $(seq 1 12); do
  poll=$(curl -s -m 60 -X POST "$ADAPTER/api/v1/ui/poll" -H 'Content-Type: application/json' \
         -H "$H_DRIVE" -H "$H_USER" -d "{\"job_id\":\"$job\",\"timeout\":25}")
  status=$(printf '%s' "$poll" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("status",""))' 2>/dev/null)
  [ "$status" = "done" ] && { ANSWER=$(printf '%s' "$poll" | python3 -c '
import json,sys
d=json.load(sys.stdin); r=d.get("result") or d
print((r.get("answer") or "").strip())
conf=(r.get("confidence") or {})
sys.stderr.write("  confidence: %s%% %s | backend: %s | hits: %d\n" % (
    conf.get("score"), conf.get("band"), r.get("backend"), len(r.get("hits") or [])))
'); break; }
done
if [ -z "$ANSWER" ]; then echo "  ABORT: nem lett kész a válasz"; exit 1; fi
echo "  válasz: $(printf '%s' "$ANSWER" | head -c 300)"

echo
echo "=== 3. /ui/tts  a válasz felolvasása ==="
python3 - > /tmp/tts_body.json <<PY
import json
print(json.dumps({"text": """$(printf '%s' "$ANSWER" | head -c 600 | tr '"' "'")""", "lang": "English"}))
PY
start=$(date +%s%N)
code=$(curl -s -o /tmp/tts_out.wav -w '%{http_code}' -m 180 -X POST "$ADAPTER/api/v1/ui/tts" \
       -H 'Content-Type: application/json' -H "$H_DRIVE" -H "$H_USER" --data-binary @/tmp/tts_body.json)
end=$(date +%s%N)
echo "  HTTP $code, $(wc -c </tmp/tts_out.wav) bájt, fejléc='$(head -c4 /tmp/tts_out.wav)', $(( (end-start)/1000000 )) ms"

echo
echo "=== 4. magyar kör (TTS hu hang) ==="
cat > /tmp/tts_hu.json <<'PY'
{"text": "A flotta rendelkezésre állása 2026 áprilisában 99,85 százalék volt.", "lang": "Hungarian"}
PY
code=$(curl -s -o /tmp/tts_hu.wav -w '%{http_code}' -m 180 -X POST "$ADAPTER/api/v1/ui/tts" \
       -H 'Content-Type: application/json' -H "$H_DRIVE" -H "$H_USER" --data-binary @/tmp/tts_hu.json)
echo "  HTTP $code, $(wc -c </tmp/tts_hu.wav) bájt, fejléc='$(head -c4 /tmp/tts_hu.wav)'"
