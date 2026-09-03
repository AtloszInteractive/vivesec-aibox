#!/usr/bin/env bash
# Chat-mode live E2E on the demo box (adapter on :80, fleet drive).
set -eu
export ADAPTER_URL=http://127.0.0.1:80
export E2E_DRIVE=/storage/drives/aiboxdev/
export E2E_USER=chat.e2e.demo.20260818
export E2E_TURNS='What was the fleet availability in April 2026?|And in May 2026?|Summarize in one sentence what you have told me so far.'
python3 -u ~/95_chat_e2e.py
