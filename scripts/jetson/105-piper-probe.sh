#!/usr/bin/env bash
# Telepíthető-e a Piper (TTS) arm64-en, és van-e benne HTTP szerver?
# Eldobható konténerben próbál, semmit nem hagy maga után.
set -u
echo "=== piper-tts pip-próba python:3.11-slim (arm64) alatt ==="
docker run --rm python:3.11-slim bash -c '
  set -e
  pip install --no-cache-dir -q piper-tts 2>&1 | tail -5
  python - <<PY
import importlib.metadata as m
print("piper-tts verzió:", m.version("piper-tts"))
import importlib.util as u
print("piper.http_server:", "VAN" if u.find_spec("piper.http_server") else "NINCS")
try:
    import piper
    print("piper import: OK")
except Exception as e:
    print("piper import HIBA:", e)
PY
' 2>&1 | tail -20
