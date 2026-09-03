#!/usr/bin/env bash
# Read-only inventory before the model A/B: which measurement scripts are on the
# box, and the exact env the production adapter runs with.
set -u
echo "== demo-corpus measurement scripts =="
ls -1 ~/demo-corpus/*.py 2>/dev/null
echo
echo "== helper scripts in HOME =="
ls -1 ~/*.py ~/*.sh 2>/dev/null | head -40
echo
echo "== production adapter env (model related) =="
docker inspect vivesec-adapter --format '{{range .Config.Env}}{{println .}}{{end}}' \
  | grep -iE 'MODEL|THINK|NUM_|AGENT|CHAT|STT|TTS|PORT|TLS' || true
echo
echo "== containers =="
docker ps --format '{{.Names}}\t{{.Status}}'
