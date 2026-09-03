#!/usr/bin/env bash
# Vocabulary build cost on the small production corpus and on the large
# evaluation corpus, to size the bound in the store.
set -eu
docker cp ~/diacritics_vocab_cost.py vivesec-rag-diacritics:/tmp/
echo "=== demo/production index: 157 docs, 1323 chunks ==="
docker exec vivesec-rag-diacritics python /tmp/diacritics_vocab_cost.py

echo
echo "=== evaluation index: 240 docs, ~45k chunks ==="
docker cp ~/reaccent.py vivesec-rag-pagefix2:/app/rag_service/
docker cp ~/diacritics_vocab_cost.py vivesec-rag-pagefix2:/tmp/
docker exec vivesec-rag-pagefix2 python /tmp/diacritics_vocab_cost.py /data/rag_index_pagefix2.db
