#!/usr/bin/env bash
# Does a published image actually match the source tree we were given?
#
# We hit this once already: 0.92 on the Hub predated the ACL commit. Rather than
# trusting tags, hash every application .py inside the image and compare the set
# against the checked-out repo.
#
#   bash image_vs_source.sh sseres/pageindexes-rag-service:0.93 ~/pi_src
set -eu
IMAGE="${1:?usage: image_vs_source.sh <image> <source-dir>}"
SRC="${2:?usage: image_vs_source.sh <image> <source-dir>}"

hash_image() {
  docker run --rm --entrypoint sh "$IMAGE" -c \
    'cd /app && find app -name "*.py" -type f | xargs sha256sum' \
    | awk '{print $2"\t"$1}' | LC_ALL=C sort
}

hash_source() {
  cd "$SRC" && find app -name "*.py" -type f | xargs sha256sum \
    | awk '{print $2"\t"$1}' | LC_ALL=C sort
}

hash_image > /tmp/img.txt
hash_source > /tmp/src.txt

echo "image  : $IMAGE  ($(wc -l < /tmp/img.txt) py fajl)"
echo "forras : $SRC  ($(wc -l < /tmp/src.txt) py fajl)"
echo
echo "--- CSAK az image-ben ---"
LC_ALL=C comm -23 <(cut -f1 /tmp/img.txt) <(cut -f1 /tmp/src.txt) | sed 's/^/  + /'
echo "--- CSAK a forrasban ---"
LC_ALL=C comm -13 <(cut -f1 /tmp/img.txt) <(cut -f1 /tmp/src.txt) | sed 's/^/  - /'
echo "--- ELTERO TARTALOM ---"
LC_ALL=C join -t "$(printf '\t')" /tmp/img.txt /tmp/src.txt \
  | awk -F'\t' '$2 != $3 {print "  ~ "$1}'

