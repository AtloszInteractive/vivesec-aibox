#!/usr/bin/env bash
# Hungarian language check on the demo box after the model swap.
set -eu
export ADAPTER_URL=http://127.0.0.1:80
export E2E_DRIVE=/storage/drives/aiboxdev/
export E2E_USER=chat.hu.20260818
export E2E_TURNS='Mennyi volt a flotta rendelkezesre allasa 2026 aprilisaban?'
python3 -u ~/95_chat_e2e.py
