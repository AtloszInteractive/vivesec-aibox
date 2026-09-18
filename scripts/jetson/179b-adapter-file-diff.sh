#!/usr/bin/env bash
# Read-only: show WHAT differs in the named adapter files between the running
# image and the staged sources (not just that they differ).
set -eu
SRC=${SRC:-$HOME/adapter-f1}
live=$(docker inspect -f '{{.Config.Image}}' vivesec-adapter)
tmp=$(mktemp -d)
cid=$(docker create "$live")
docker cp "$cid:/app/adapter" "$tmp/live" >/dev/null
docker rm "$cid" >/dev/null
for f in "$@"; do
  echo "=== $f ==="
  diff -u "$tmp/live/$f" "$SRC/$f" | sed -n '1,80p' || true
  echo
done
rm -rf "$tmp"
