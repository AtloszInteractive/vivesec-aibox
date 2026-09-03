#!/usr/bin/env bash
# Miért bukik az /ui/stt a UI proxyján át, miközben az adapteren közvetlenül megy?
set -u
UI=${UI:-http://127.0.0.1:8080}
AD=${AD:-http://127.0.0.1:8088}
WAV="$HOME/stt_probe_fleet.wav"

python3 - "$WAV" > /tmp/stt_body.json <<'PY'
import base64, json, sys
print(json.dumps({"audio_b64": base64.b64encode(open(sys.argv[1],"rb").read()).decode(),
                  "content_type": "audio/wav", "lang": "English"}))
PY
echo "body: $(wc -c </tmp/stt_body.json) bájt"

echo
echo "=== A) közvetlenül az adapternek (kontroll) ==="
curl -s -m 300 -X POST "$AD/api/v1/ui/stt" -H 'Content-Type: application/json' \
     --data-binary @/tmp/stt_body.json | head -c 200; echo

echo
echo "=== B) UI proxyn át, ALAPÉRTELMEZETT curl (küld Expect: 100-continue-t) ==="
curl -s -m 300 -X POST "$UI/api/v1/ui/stt" -H 'Content-Type: application/json' \
     --data-binary @/tmp/stt_body.json | head -c 200; echo

echo
echo "=== C) UI proxyn át, Expect fejléc NÉLKÜL (ezt küldi a böngésző) ==="
curl -s -m 300 -X POST "$UI/api/v1/ui/stt" -H 'Content-Type: application/json' -H 'Expect:' \
     --data-binary @/tmp/stt_body.json | head -c 200; echo

echo
echo "=== D) kisebb test a proxyn át (méret-hatás kizárása) ==="
curl -s -m 60 -X POST "$UI/api/v1/ui/stt" -H 'Content-Type: application/json' -H 'Expect:' \
     -d '{"audio_b64":"","content_type":"audio/wav"}' | head -c 200; echo

echo
echo "=== E) UI konténer utolsó logsorai ==="
docker logs --tail 20 vivesec-ui 2>&1 | sed 's/^/  /'
