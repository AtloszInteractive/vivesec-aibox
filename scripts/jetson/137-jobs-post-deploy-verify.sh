#!/usr/bin/env bash
set -eu

echo "=== adapter ==="
docker inspect -f 'image={{.Image}} restart={{.HostConfig.RestartPolicy.Name}} network={{.HostConfig.NetworkMode}}' vivesec-adapter
docker inspect -f '{{range .Mounts}}{{println .Source "->" .Destination}}{{end}}' vivesec-adapter
docker exec vivesec-adapter test -f /app/adapter/jobstore.py
docker exec vivesec-adapter test -f /app/adapter/scheduler.py
docker exec vivesec-adapter python -m py_compile /app/adapter/jobstore.py /app/adapter/scheduler.py /app/adapter/service.py

echo
echo "=== health and jobs ==="
curl -fsS -m 20 -X POST http://127.0.0.1:8088/api/v1/status \
  -H 'Content-Type: application/json' -d '{}' \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); print("ui_ready=%s fs_ready=%s voice=%s" % (d.get("ui_ready"),d.get("fs_ready"),d.get("voice")))'
curl -fsS -m 20 http://127.0.0.1:8088/api/v1/ui/jobs \
  -H 'VVS-Drive: L3N0b3JhZ2UvZHJpdmVzL2VuZ2luZWVyaW5nLw' \
  -H 'VVS-User: demo' \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); print("jobs=%d states=%s" % (len(d.get("jobs",[])),[j.get("status") for j in d.get("jobs",[])]))'
echo "job_files=$(docker exec vivesec-adapter sh -c 'find /data/jobs -type f -name "*.json" | wc -l')"

echo
echo "=== UI ==="
grep -Rqs 'Background jobs' "$HOME/ui-app/.output/public" "$HOME/ui-app/.output/server"
echo "bundle_files=$(find "$HOME/ui-app/.output" -type f | wc -l)"
code=$(curl -s -o /dev/null -w '%{http_code}' -m 20 http://127.0.0.1:8080/)
[ "$code" = "200" ]
echo "ui_http=$code"

echo "POST_DEPLOY_VERIFY=PASS"