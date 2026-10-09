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

# Release identity (E01): adapter/ and rag_service/ are stamped from VERSION and
# git; a delivered tree without .git keeps the stamp it was shipped with.
build_field() {
  python3 -c 'import json, sys; print(json.load(open(sys.argv[1])).get(sys.argv[2]) or "")' "$1" "$2"
}
python3 "$source_dir/scripts/release/build_info.py" stamp
for stamped in adapter/build_info.json rag_service/build_info.json; do
  [[ -f "$source_dir/$stamped" ]] || { echo "Missing release stamp: $stamped" >&2; exit 1; }
done
release_version=$(build_field "$source_dir/adapter/build_info.json" version)
release_label=$(build_field "$source_dir/adapter/build_info.json" label)
release_commit=$(build_field "$source_dir/adapter/build_info.json" commit)
version_labels=(--label "vivesec.version=$release_label" --label "vivesec.commit=$release_commit")
echo "RELEASE $release_label commit=${release_commit:-unknown}"

cd "$source_dir"

tag_previous vivesec-adapter
docker build "${version_labels[@]}" -f adapter/Dockerfile -t vivesec-adapter:latest adapter

# The repository-root .dockerignore (written for the UI image) excludes poc/,
# so the rag image is built from a staging tree holding exactly its two inputs.
rag_stage="${RAG_STAGE:-/data/app/rag-src}"
rm -rf "$rag_stage"
install -d -m 0755 "$rag_stage"
cp -a "$source_dir/poc" "$source_dir/rag_service" "$rag_stage/"
find "$rag_stage" -name '__pycache__' -type d -prune -exec rm -rf {} +
tag_previous vivesec-rag
docker build "${version_labels[@]}" -f "$rag_stage/rag_service/Dockerfile" -t vivesec-rag:latest "$rag_stage"

# The Jetson never runs the web toolchain: the bundle arrives pre-built.
rm -rf "$ui_stage"
install -d -m 0755 "$ui_stage"
tar -xzf "$ui_bundle" -C "$ui_stage"
[[ -f "$ui_stage/.output/server/index.mjs" ]] || {
  echo "The UI bundle does not contain .output/server/index.mjs" >&2
  exit 1
}
# One release = one calendar version on every component; a UI bundle built for
# another release is refused instead of producing a mixed installation.
ui_version_file="$ui_stage/.output/public/version.json"
[[ -f $ui_version_file ]] || {
  echo "The UI bundle carries no version.json; rebuild it from this release" >&2
  exit 1
}
ui_version=$(build_field "$ui_version_file" version)
if [[ $ui_version != "$release_version" ]]; then
  echo "UI bundle version $ui_version does not match the source release $release_version" >&2
  exit 1
fi
ui_commit=$(build_field "$ui_version_file" commit)
[[ $ui_commit == "$release_commit" ]] ||
  echo "WARNING: UI bundle commit ${ui_commit:-unknown} differs from the source commit ${release_commit:-unknown}"
cp "$source_dir/scripts/jetson/Dockerfile.ui-runtime" "$ui_stage/Dockerfile"
tag_previous vivesec-ui
docker build --label "vivesec.version=$(build_field "$ui_version_file" label)" \
  --label "vivesec.commit=$ui_commit" -t vivesec-ui:latest "$ui_stage"

for image in vivesec-rag vivesec-adapter vivesec-ui; do
  printf 'IMAGE %s:latest id=%s version=%s\n' "$image" \
    "$(docker image inspect --format '{{.Id}}' "$image:latest")" \
    "$(docker image inspect --format '{{index .Config.Labels "vivesec.version"}}' "$image:latest")"
done
echo "AIBOX_IMAGES_BUILT stamp=$stamp release=$release_label"
