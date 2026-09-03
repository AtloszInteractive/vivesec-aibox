#!/usr/bin/env bash
# Times extract_pages on the timed-out documents inside the pagefix container,
# using a throwaway copy of the files (nothing is written to any index).
set -eu

SRC=/home/aibox/govdocs-eval/selected
WORK=/home/aibox/extract-probe
CONTAINER=${1:-vivesec-rag-pagefix}

rm -rf "$WORK"
mkdir -p "$WORK"
for f in 101/101676.pdf 101/101696.pdf 101/101706.pdf 102/102797.txt 101/101613.txt; do
  if [ -f "$SRC/$f" ]; then
    cp "$SRC/$f" "$WORK/$(basename "$f")"
  else
    echo "missing in corpus: $SRC/$f"
  fi
done
ls -la "$WORK"

docker exec "$CONTAINER" mkdir -p /tmp/probe
for f in "$WORK"/*; do
  docker cp "$f" "$CONTAINER:/tmp/probe/$(basename "$f")"
done
docker cp /home/aibox/time_extract.py "$CONTAINER:/tmp/time_extract.py"

echo "=== $CONTAINER"
docker exec -i "$CONTAINER" python /tmp/time_extract.py
