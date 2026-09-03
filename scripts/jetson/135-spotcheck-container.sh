#!/usr/bin/env bash
# Run e2e_spotcheck against a named disposable adapter container.
#   bash 135-spotcheck-container.sh [CONTAINER]
set -e
NAME="${1:-vivesec-adapter-thinktest}"
docker inspect "$NAME" >/dev/null
sed "s/\"vivesec-adapter\"/\"$NAME\"/" ~/demo-corpus/e2e_spotcheck.py > ~/e2e_spotcheck_run.py
cd ~/demo-corpus
python3 -u ~/e2e_spotcheck_run.py
