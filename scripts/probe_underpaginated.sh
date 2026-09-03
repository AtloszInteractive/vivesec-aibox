#!/usr/bin/env bash
# For the PDFs the gold set expects multiple pages from, compare what pdfminer
# reports directly with what our extractor ends up with. Read-only probe.
set -eu

SRC=/home/aibox/govdocs-eval/selected
CONTAINER=${1:-vivesec-rag-pagefix}

FILES="102/102977.pdf 100/100187.pdf 102/102219.pdf 102/102937.pdf 101/101849.pdf 100/100591.pdf"

docker exec "$CONTAINER" mkdir -p /tmp/probe
docker cp /home/aibox/probe_pdf_pages.py "$CONTAINER:/tmp/probe_pdf_pages.py"

for f in $FILES; do
  base=$(basename "$f")
  if [ ! -f "$SRC/$f" ]; then
    echo "=== $base : NOT IN CORPUS"
    continue
  fi
  docker cp "$SRC/$f" "$CONTAINER:/tmp/probe/$base" >/dev/null
  echo "=================================================== $base"
  docker exec -i "$CONTAINER" python /tmp/probe_pdf_pages.py "/tmp/probe/$base" 2>&1 | grep -vE 'RuntimeWarning|warn\('
done
