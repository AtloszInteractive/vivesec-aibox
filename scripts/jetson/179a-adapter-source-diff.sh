#!/usr/bin/env bash
# Read-only: which adapter files differ between the running image and ~/adapter-f1.
set -eu
SRC=${SRC:-$HOME/adapter-f1}
live=$(docker inspect -f '{{.Config.Image}}' vivesec-adapter)
echo "live image: $live"
cd "$SRC"
new=$(sha256sum ./*.py Dockerfile | sed 's#\./##' | awk '{print $2, $1}' | sort)
old=$(docker run --rm --entrypoint sh "$live" -c 'cd /app/adapter && sha256sum ./*.py Dockerfile 2>/dev/null' \
      | sed 's#\./##' | awk '{print $2, $1}' | sort)
echo "--- changed or new files ---"
diff <(echo "$old") <(echo "$new") | awk '/^[<>]/ {print $1, $2}'
echo "--- only in the running image (would disappear) ---"
comm -23 <(echo "$old" | awk '{print $1}') <(echo "$new" | awk '{print $1}')
