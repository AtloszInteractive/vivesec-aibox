#!/usr/bin/env bash
# Mit tud a piper beépített HTTP szervere? (kell-e saját wrapper)
set -u
docker run --rm python:3.11-slim bash -c '
  pip install --no-cache-dir -q piper-tts >/dev/null 2>&1
  echo "=== http_server --help ==="
  python -m piper.http_server --help 2>&1 | head -40
  echo
  echo "=== download_voices --help ==="
  python -m piper.download_voices --help 2>&1 | head -15
' 2>&1 | tail -60
