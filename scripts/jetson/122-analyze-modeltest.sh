#!/usr/bin/env bash
# Run the analyze e2e proof against the disposable model-test adapter (:8089).
# 86_analyze_e2e.py hardcodes the production port, so patch a copy.
set -e
PORT="${1:-8089}"
DRIVE="${2:-/storage/drives/engineering/}"
WANT="${3:-Manual}"
sed "s|http://127.0.0.1:8088|http://127.0.0.1:$PORT|" \
    ~/86_analyze_e2e.py > ~/86_analyze_modeltest.py
python3 -u ~/86_analyze_modeltest.py "$DRIVE" "$WANT"
