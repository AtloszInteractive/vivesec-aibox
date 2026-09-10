#!/usr/bin/env bash
# Is the delete storm still running right now?
set -u
date
echo "--- last 15 index operations, with timestamps ---"
docker logs -t --tail 3000 vivesec-adapter 2>&1 | grep -E 'index/(drop|upsert)' | tail -15
echo
echo "--- drop/tree calls in the last 3 minutes ---"
docker logs --since 3m vivesec-adapter 2>&1 | grep -c 'index/drop/tree'
echo "--- drop/tree calls in the last 60 seconds ---"
docker logs --since 60s vivesec-adapter 2>&1 | grep -c 'index/drop/tree'
echo "--- total log lines in the last 3 minutes ---"
docker logs --since 3m vivesec-adapter 2>&1 | wc -l
echo
echo "--- first and last drop seen in the whole log ---"
docker logs -t vivesec-adapter 2>&1 | grep 'index/drop/tree' | head -1
docker logs -t vivesec-adapter 2>&1 | grep 'index/drop/tree' | tail -1
echo
echo "--- total drops in the whole log ---"
docker logs vivesec-adapter 2>&1 | grep -c 'index/drop/tree'
echo
echo "--- current stats ---"
curl -s http://127.0.0.1:8090/stats
echo
