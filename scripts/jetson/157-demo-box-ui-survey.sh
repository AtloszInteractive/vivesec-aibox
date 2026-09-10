#!/usr/bin/env bash
# Read-only survey of the demo box before the UI refresh.
set -u
echo "=== containers ==="
docker ps --format '{{.Names}}\t{{.Image}}\t{{.Status}}' | sed 's/^/  /'
echo
echo "=== adapter status ==="
curl -s -m 10 -X POST http://127.0.0.1:80/api/v1/status -H 'Content-Type: application/json' -d '{}' \
  | python3 -c "import json,sys; d=json.load(sys.stdin); print('  ok=%s ui_ready=%s fs_ready=%s' % (d.get('ok'), d.get('ui_ready'), d.get('fs_ready')))" 2>/dev/null \
  || echo "  (status parse failed)"
echo
echo "=== adapter capabilities (does the deployed backend have today's work?) ==="
for f in scope.py jobstore.py scheduler.py; do
  if docker exec vivesec-adapter test -f /app/adapter/$f 2>/dev/null; then echo "  $f PRESENT"; else echo "  $f MISSING"; fi
done
echo -n "  /ui/jobs -> "
curl -s -o /dev/null -w '%{http_code}\n' -m 10 http://127.0.0.1:80/api/v1/ui/jobs -H 'VVS-User: probe' -H 'VVS-Drive: x'
echo -n "  /ui/scope -> "
curl -s -o /dev/null -w '%{http_code}\n' -m 10 http://127.0.0.1:80/api/v1/ui/scope -H 'VVS-User: probe' -H 'VVS-Drive: x'
echo
echo "=== running UI bundle: does it contain the job panel? ==="
cd /tmp && rm -f p.html a.txt s*.js
curl -s -m 10 http://127.0.0.1:8080/ -o p.html
grep -o '/assets/[^"]*\.js' p.html | sort -u > a.txt
i=0
while read -r a; do
  i=$((i + 1))
  curl -s -m 20 "http://127.0.0.1:8080$a" -o "s$i.js"
  jobs=$(grep -a -c 'Background jobs' "s$i.js" || true)
  hu=$(grep -a -c 'Háttérfeladatok' "s$i.js" || true)
  expl=$(grep -a -c 'Retrieved passages' "s$i.js" || true)
  echo "  $a  backgroundJobs=$jobs hu_translation=$hu explorer=$expl ($(wc -c < "s$i.js") bytes)"
done < a.txt
echo
echo "=== ui image + deploy dirs ==="
docker inspect -f '  image={{.Config.Image}}' vivesec-ui 2>/dev/null || echo "  (no vivesec-ui)"
docker images vivesec-ui --format '  tag={{.Tag}} id={{.ID}} created={{.CreatedSince}}' | head -8
ls -d ~/ui-src ~/adapter-src ~/rag-build 2>/dev/null | sed 's/^/  /'
echo
echo "=== disk ==="
df -h / /data 2>/dev/null | sed 's/^/  /'
