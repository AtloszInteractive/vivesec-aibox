#!/usr/bin/env bash
# J1b — Install Docker + NVIDIA container runtime on JetPack 5 (L4T R35.4.1) and
# point Docker's storage at the NVMe (/data/docker) so images survive an eMMC
# reflash and don't wear the small eMMC. Default runtime = nvidia (appliance).
#
# Run as root:  sudo bash 40-docker-nvidia.sh
set -euo pipefail

DATA_ROOT="/data/docker"
DAEMON_JSON="/etc/docker/daemon.json"
DOCKER_USER="aibox"

echo "[40] Installing docker.io + nvidia-container-toolkit (from the Jetson r35.4 repo)"
apt-get update
apt-get install -y --no-install-recommends \
  docker.io nvidia-container-toolkit

echo "[40] Stopping docker to relocate its storage to ${DATA_ROOT}"
systemctl stop docker || true
mkdir -p "$DATA_ROOT"
mkdir -p /etc/docker

echo "[40] Writing ${DAEMON_JSON} (data-root on NVMe, default runtime = nvidia)"
cat > "$DAEMON_JSON" <<JSON
{
  "data-root": "${DATA_ROOT}",
  "default-runtime": "nvidia",
  "runtimes": {
    "nvidia": {
      "path": "nvidia-container-runtime",
      "runtimeArgs": []
    }
  }
}
JSON

echo "[40] Adding ${DOCKER_USER} to the docker group (effective on next login)"
usermod -aG docker "$DOCKER_USER" || true

echo "[40] Enabling + restarting docker"
systemctl daemon-reload
systemctl enable docker
systemctl restart docker
sleep 3

echo "[40] ---- verification ----"
echo "[40] docker version:"; docker version --format '{{.Server.Version}}' 2>/dev/null || docker --version
echo "[40] storage / runtime:"; docker info 2>/dev/null | grep -E 'Docker Root Dir|Default Runtime|Runtimes' || true
echo "[40] GPU-runtime smoke (ubuntu via default nvidia runtime):"
docker run --rm ubuntu echo "container-ok" || echo "WARN: container run failed (review above)"
echo "[40] done."
