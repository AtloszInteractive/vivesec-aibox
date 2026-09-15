#!/usr/bin/env bash
# ViVeSec AI Box - delivery manifest.
#
# Records exactly what is installed on the box: platform versions, image ids,
# model ids and the fingerprint of the delivered source tree. Written to
# /data/app/MANIFEST.txt so the state at handover can be proved later.
#
#   SOURCE_DIR=/home/aibox/vivesec_iabox_app bash 95-manifest.sh
set -euo pipefail

source_dir="${SOURCE_DIR:-}"
box_name="${AIBOX_HOSTNAME:-$(hostname)}"
output="${MANIFEST_PATH:-/data/app/MANIFEST.txt}"

tree_fingerprint() {
  local directory=$1
  [[ -d $directory ]] || { echo "n/a"; return; }
  find "$directory" -type f -name '*.py' -print0 |
    sort -z | xargs -0 sha256sum | sha256sum | awk '{print $1}'
}

{
  echo "ViVeSec AI Box - delivery manifest"
  echo "box:          $box_name"
  echo "generated:    $(date -Is)"
  echo
  echo "[platform]"
  echo "l4t:          $(head -n 1 /etc/nv_tegra_release)"
  echo "kernel:       $(uname -r)"
  echo "architecture: $(dpkg --print-architecture)"
  echo "root:         $(findmnt -nro SOURCE,FSTYPE /)"
  echo "data:         $(findmnt -nro SOURCE,FSTYPE,SIZE /data)"
  echo "docker:       $(docker version --format '{{.Server.Version}}') root=$(docker info --format '{{.DockerRootDir}}')"
  echo "ollama:       $(curl -fsS http://127.0.0.1:11434/api/version | jq -r .version)"
  echo
  echo "[images]"
  for image in vivesec-rag vivesec-adapter vivesec-ui; do
    printf '%-18s %s  created=%s\n' "$image:latest" \
      "$(docker image inspect --format '{{.Id}}' "$image:latest")" \
      "$(docker image inspect --format '{{.Created}}' "$image:latest")"
  done
  echo
  echo "[rollback tags]"
  docker image ls --format '{{.Repository}}:{{.Tag}} {{.ID}}' |
    grep -E '^vivesec-(rag|adapter|ui):prev-' | sort || echo "none"
  echo
  echo "[models]"
  ollama list
  echo
  echo "[source fingerprint]"
  if [[ -n $source_dir && -d $source_dir ]]; then
    echo "source_dir:   $source_dir"
    echo "adapter:      $(tree_fingerprint "$source_dir/adapter")"
    echo "rag_service:  $(tree_fingerprint "$source_dir/rag_service")"
    echo "poc:          $(tree_fingerprint "$source_dir/poc")"
  else
    echo "source_dir:   not available at manifest time"
  fi
  echo
  echo "[containers]"
  docker ps --format '{{.Names}}\t{{.Image}}\t{{.Status}}'
} > "$output"

chmod 0644 "$output"
echo "AIBOX_MANIFEST_WRITTEN path=$output"
