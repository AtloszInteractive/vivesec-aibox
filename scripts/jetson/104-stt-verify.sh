#!/usr/bin/env bash
# STT valódi beszéd-teszt: a ~/-ba feltöltött WAV-okat átiratja és időt mér.
# Használat: bash 104-stt-verify.sh [port]
set -u
PORT=${1:-8101}
URL="http://127.0.0.1:$PORT/v1/audio/transcriptions"

echo "=== modell-lista ==="
curl -s -m 10 "http://127.0.0.1:$PORT/v1/models" | head -c 300; echo

for f in "$HOME"/stt_probe_*.wav "$HOME"/stt_probe_*.webm; do
  [ -f "$f" ] || continue
  name=$(basename "$f")
  bytes=$(wc -c <"$f")
  lang=en
  case "$name" in *_hu*) lang=hu ;; esac
  start=$(date +%s%N)
  out=$(curl -s -m 300 -X POST "$URL" -F "file=@$f" -F "language=$lang" -F "response_format=json")
  end=$(date +%s%N)
  secs=$(( (end - start) / 100000000 ))
  echo "--- $name ($bytes bájt, lang=$lang, ${secs:0:-1}.${secs: -1} s)"
  echo "    $out"
done
