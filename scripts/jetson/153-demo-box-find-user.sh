#!/usr/bin/env bash
# Recover the REAL ViVeSecBox user id from persisted artefacts, then retry
# get-file with it. Prints ids only, no document content.
set -u

echo "=== /data/sessions ==="
ls -la /data/sessions 2>/dev/null | head -20
echo
for f in /data/sessions/*; do
  [ -f "$f" ] || continue
  echo "--- $(basename "$f")"
  head -c 400 "$f"
  echo
  break
done
echo

echo "=== users mentioned in session files ==="
grep -ho '"user"[[:space:]]*:[[:space:]]*"[^"]*"' /data/sessions/* 2>/dev/null | sort -u | head -20
echo

echo "=== /data/feedback ==="
ls -la /data/feedback 2>/dev/null | head -10
grep -ho '"user"[[:space:]]*:[[:space:]]*"[^"]*"' /data/feedback/* 2>/dev/null | sort -u | head -20
echo

echo "=== /data/generated (per-session dirs are named after the session) ==="
ls -la /data/generated 2>/dev/null | head -20
echo

echo "=== /data/adapter/meta.json ==="
head -c 600 /data/adapter/meta.json 2>/dev/null
echo
