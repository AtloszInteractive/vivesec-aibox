#!/usr/bin/env bash
# Restore a file inside a running container from its own image, byte for byte.
#
# `docker cp` writes into the container's writable layer, so a restart does NOT
# undo it -- the pristine copy has to come from the image itself.
#
#   bash restore_file_from_image.sh vivesec-rag vivesec-rag:latest /app/rag_service/extract.py
set -eu
CONTAINER="${1:?usage: restore_file_from_image.sh <container> <image> <path-in-container>}"
IMAGE="${2:?}"
TARGET="${3:?}"

tmp_container="$(docker create "$IMAGE" /bin/true)"
trap 'docker rm -f "$tmp_container" >/dev/null 2>&1 || true' EXIT

work="$(mktemp -d)"
docker cp "${tmp_container}:${TARGET}" "${work}/pristine"
echo "image-beli meret : $(wc -c < "${work}/pristine") byte"

docker cp "${work}/pristine" "${CONTAINER}:${TARGET}"
# `wc -c < path` would redirect on the host, so read the file inside the container.
echo "konteneres meret : $(docker exec "$CONTAINER" wc -c "$TARGET" | awk '{print $1}') byte"
rm -rf "$work"
