#!/usr/bin/env bash
# Block until a nohup'd measurement writes its final line, then show the tail.
# Usage: bash 120-wait-log.sh <logfile> <final-marker> [max_minutes]
set -u
LOG="${1:?logfile}"
MARK="${2:-report:}"
MAX="${3:-30}"
END=$(( $(date +%s) + MAX * 60 ))
while [ "$(date +%s)" -lt "$END" ]; do
  grep -q "$MARK" "$LOG" 2>/dev/null && break
  sleep 15
done
echo "== tail of $LOG =="
tail -40 "$LOG"
