#!/usr/bin/env bash
# Van-e linux/arm64 (aarch64) variánsa a szóba jöhető beszédmotor-image-eknek?
# Csak MANIFESTET kérdez le, nem húz le semmit.
set -u

check() {
  local ref="$1"
  local out
  out=$(docker manifest inspect "$ref" 2>&1)
  if [ $? -ne 0 ]; then
    printf '  %-58s NINCS/ELÉRHETETLEN\n' "$ref"
    return
  fi
  local arches
  arches=$(echo "$out" | grep -o '"architecture": *"[a-z0-9]*"' | sed 's/.*"\([a-z0-9]*\)"$/\1/' | sort -u | tr '\n' ' ')
  if echo "$arches" | grep -qw arm64; then
    printf '  %-58s ARM64 ✔  (%s)\n' "$ref" "$arches"
  else
    printf '  %-58s csak: %s\n' "$ref" "$arches"
  fi
}

echo "=== STT jelöltek ==="
for r in \
  "fedirz/faster-whisper-server:latest-cpu" \
  "ghcr.io/speaches-ai/speaches:latest-cpu" \
  "onerahmet/openai-whisper-asr-webservice:latest" \
  "ghcr.io/ggml-org/whisper.cpp:main" \
  "python:3.11-slim" ; do check "$r"; done

echo
echo "=== TTS jelöltek ==="
for r in \
  "rhasspy/wyoming-piper:latest" \
  "lscr.io/linuxserver/piper:latest" \
  "artibex/piper-http:latest" ; do check "$r"; done

echo
echo "=== szabad portok 8100-8110 között ==="
for p in $(seq 8100 8110); do
  ss -ltn 2>/dev/null | awk 'NR>1{print $4}' | grep -q ":$p\$" || echo "  $p szabad"
done
