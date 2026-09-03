#!/usr/bin/env bash
# Production redeploy, step 1 of 3: back up the live index and build the image.
# Nothing running is touched -- if the build fails, production is unchanged.
set -eu

TS=$(date +%Y%m%d-%H%M)

echo "=== 1. back up the live index ==="
docker exec vivesec-rag python -c "
import sqlite3
src = sqlite3.connect('/data/rag_index.db')
dst = sqlite3.connect('/data/rag_index.prefix-$TS.db')
with dst:
    src.backup(dst)
print('  integrity :', dst.execute('pragma integrity_check').fetchone()[0])
print('  documents :', dst.execute('select count(*) from documents').fetchone()[0])
print('  chunks    :', dst.execute('select count(*) from chunks').fetchone()[0])
dst.close(); src.close()
"
ls -lh /data/rag/rag_index.prefix-$TS.db | sed 's/^/  /'

echo
echo "=== 2. source checksums going into the image ==="
cd /home/aibox/rag-build
sha256sum rag_service/extract.py rag_service/sqlite_store.py rag_service/store.py \
          rag_service/query_split.py | sed 's/^/  /'

echo
echo "=== 3. build vivesec-rag:latest ==="
docker build -f rag_service/Dockerfile -t vivesec-rag:new . 2>&1 | tail -3

echo
echo "=== 4. unit tests inside the new image ==="
for t in query_split_test extract_pages_test; do
  printf '  %-22s ' "$t"
  docker run --rm --entrypoint python vivesec-rag:new "/app/rag_service/$t.py" 2>&1 | tail -1
done

echo
echo "built as vivesec-rag:new -- production still runs the old image."
