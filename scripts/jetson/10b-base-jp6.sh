#!/usr/bin/env bash
set -euo pipefail

expected_l4t_release="R36"
expected_l4t_revision="4.4"
expected_cuda_package="6.2.1+b38"
expected_container_package="6.2.1+b38"
data_mount="/data"
docker_root="/data/docker"
mode="${1:-}"

release_file=/etc/nv_tegra_release
[[ -r $release_file ]] || { echo "Missing $release_file" >&2; exit 1; }
release_line="$(head -n 1 "$release_file")"
[[ $release_line == *"# $expected_l4t_release "* && $release_line == *"REVISION: $expected_l4t_revision"* ]] || {
  echo "Unsupported L4T release: $release_line" >&2
  exit 1
}
[[ $(dpkg --print-architecture) == arm64 ]] || { echo "Expected arm64." >&2; exit 1; }

root_source="$(findmnt -nro SOURCE /)"
data_source="$(findmnt -nro SOURCE "$data_mount")"
data_fstype="$(findmnt -nro FSTYPE "$data_mount")"
[[ $root_source == /dev/mmcblk0p1 ]] || { echo "Unexpected root source: $root_source" >&2; exit 1; }
[[ $data_source == /dev/nvme0n1p1 && $data_fstype == ext4 ]] || {
  echo "Expected ext4 NVMe at $data_mount, got $data_source ($data_fstype)." >&2
  exit 1
}

cuda_candidate="$(apt-cache policy nvidia-cuda | awk '/Candidate:/ {print $2; exit}')"
container_candidate="$(apt-cache policy nvidia-container | awk '/Candidate:/ {print $2; exit}')"
[[ $cuda_candidate == "$expected_cuda_package" ]] || {
  echo "Unexpected nvidia-cuda candidate: $cuda_candidate" >&2
  exit 1
}
[[ $container_candidate == "$expected_container_package" ]] || {
  echo "Unexpected nvidia-container candidate: $container_candidate" >&2
  exit 1
}

printf 'JP6_PREFLIGHT_OK l4t=%s revision=%s root=%s data=%s cuda=%s container=%s\n' \
  "$expected_l4t_release" "$expected_l4t_revision" "$root_source" "$data_source" \
  "$cuda_candidate" "$container_candidate"
[[ $mode == --check ]] && exit 0

[[ $EUID -eq 0 ]] || { echo "Run as root." >&2; exit 1; }

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y \
  "nvidia-cuda=$expected_cuda_package" \
  "nvidia-container=$expected_container_package" \
  nvidia-container-toolkit \
  ca-certificates \
  curl \
  jq \
  zstd

if ! dpkg-query -W docker-ce >/dev/null 2>&1; then
  systemctl start nv-install-docker.service
fi
command -v docker >/dev/null || { echo "Docker CE installation failed." >&2; exit 1; }

install -d -m 0711 "$docker_root"
install -d -m 0755 /etc/docker
if [[ -f /etc/docker/daemon.json ]]; then
  cp --archive /etc/docker/daemon.json "/etc/docker/daemon.json.pre-jp6-$(date +%Y%m%d-%H%M%S)"
fi

systemctl stop docker
nvidia-ctk runtime configure --runtime=docker
python3 - <<'PY'
import json
from pathlib import Path

path = Path("/etc/docker/daemon.json")
config = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
config.update({
    "data-root": "/data/docker",
    "log-driver": "json-file",
    "log-opts": {"max-file": "3", "max-size": "10m"},
})
path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
PY

systemctl daemon-reload
systemctl reset-failed docker.service docker.socket
systemctl enable --now docker.socket
systemctl enable --now docker.service
usermod -aG docker aibox

docker_root_actual="$(docker info --format '{{.DockerRootDir}}')"
[[ $docker_root_actual == "$docker_root" ]] || {
  echo "Unexpected Docker root: $docker_root_actual" >&2
  exit 1
}

# Factory default is MODE_30W, which halves LLM generation speed. Switching to
# MAXN (mode 0) changes the core count, so nvpmodel insists on rebooting right
# away - that is left to the operator (the audit enforces the result):
#   printf 'YES\n' | sudo nvpmodel -m 0     # reboots immediately
if ! nvpmodel -q 2>/dev/null | grep -q '^NV Power Mode: MAXN'; then
  echo "POWER_MODE_NOT_MAXN current=$(nvpmodel -q 2>/dev/null | head -n 1) -> run: printf 'YES\\n' | sudo nvpmodel -m 0"
fi

echo "JP6_BASE_INSTALLED docker_root=$docker_root_actual"