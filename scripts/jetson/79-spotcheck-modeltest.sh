#!/usr/bin/env bash
# Run e2e_spotcheck against the disposable model-test adapter container.
set -e
sed 's/"vivesec-adapter"/"vivesec-adapter-modeltest"/' ~/demo-corpus/e2e_spotcheck.py > ~/e2e_spotcheck_35b.py
cd ~/demo-corpus
python3 ~/e2e_spotcheck_35b.py
