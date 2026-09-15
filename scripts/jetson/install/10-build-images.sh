#!/usr/bin/env bash
# ViVeSec AI Box - build the three application images on the box.
#
# Build contexts differ and are not interchangeable:
#   adapter -> the adapter/ directory
#   rag     -> the repository root (the image needs poc/ as well as rag_service/)
#   ui      -> a staging directory holding the pre-built Nitro bundle
#
# The previously running images are tagged :prev-<timestamp> before the new
# ones take the :latest tag, so a rollback is a single docker tag away.
#
#   SOURCE_DIR=/home/aibox/vivesec_iabox_app UI_BUNDLE=/home/aibox/ui-output.tgz \
#     bash 10-build-images.sh
set -euo pipefail

source_dir="${SOURCE_DIR:?SOURCE_DIR is required}"
ui_bundle="${UI_BUNDLE:?UI_BUNDLE is required}"
stamp="$(date +%Y%m%d-%H%M%S)"
ui_stage="${UI_STAGE:-/data/app/ui-src}"

[[ -d $source_dir ]] || { echo "Missing source directory: $source_dir" >&2; exit 1; }
[[ -r $ui_bundle ]] || { echo "Missing UI bundle: $ui_bundle" >&2; exit 1; }
for required in adapter/Dockerfile rag_service/Dockerfile poc \
                scripts/jetson/Dockerfile.ui-runtime; do
  [[ -e "$source_dir/$required" ]] || {
    echo "Missing from the source tree: $required" >&2
    exit 1
  }
done

tag_previous() {
  local image=$1
  if docker image inspect "$image:latest" >/dev/null 2>&1; then
    docker tag "$image:latest" "$image:prev-$stamp"
    echo "ROLLBACK_TAG $image:prev-$stamp"
  fi
}

cd "$source_dir"

tag_previous vivesec-adapter
docker build -f adapter/Dockerfile -t vivesec-adapter:latest adapter

tag_previous vivesec-rag
docker build -f rag_service/Dockerfile -t vivesec-rag:latest .

# The Jetson never runs the web toolchain: the bundle arrives pre-built.
rm -rf "$ui_stage"
install -d -m 0755 "$ui_stage"
tar -xzf "$ui_bundle" -C "$ui_stage"
[[ -f "$ui_stage/.output/server/index.mjs" ]] || {
  echo "The UI bundle does not contain .output/server/index.mjs" >&2
  exit 1
}
cp "$source_dir/scripts/jetson/Dockerfile.ui-runtime" "$ui_stage/Dockerfile"
tag_previous vivesec-ui
docker build -t vivesec-ui:latest "$ui_stage"

for image in vivesec-rag vivesec-adapter vivesec-ui; do
  printf 'IMAGE %s:latest id=%s\n' "$image" \
    "$(docker image inspect --format '{{.Id}}' "$image:latest")"
done
echo "AIBOX_IMAGES_BUILT stamp=$stamp"
