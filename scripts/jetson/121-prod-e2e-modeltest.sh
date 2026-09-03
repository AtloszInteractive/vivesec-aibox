#!/usr/bin/env bash
# Run prod_e2e_verify against the disposable model-test adapter (:8089).
# The script hardcodes the production port, so patch a copy, never the original.
set -e
PORT="${1:-8089}"
sed "s|http://127.0.0.1:8088|http://127.0.0.1:$PORT|" \
    ~/demo-corpus/prod_e2e_verify.py > ~/prod_e2e_modeltest.py
cd ~/demo-corpus
python3 -u ~/prod_e2e_modeltest.py
