#!/usr/bin/env bash
# Did the ViVeSecBox file channel come back after the adapter restart?
set -u
for i in 1 2 3 4 5 6; do
  printf '%s  ' "$(date +%H:%M:%S)"
  curl -s --max-time 8 http://127.0.0.1:80/api/v1/status | grep -o '"ws_fs":[^}]*}' || echo "(no answer)"
  sleep 10
done
echo
echo "--- ws-fs lines in the adapter log ---"
docker logs --tail 200 vivesec-adapter 2>&1 | grep -Ei 'ws-fs|channel' | tail -15
echo
echo "--- any tracebacks since start ---"
docker logs vivesec-adapter 2>&1 | grep -c 'Traceback' || true
