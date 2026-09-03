#!/usr/bin/env bash
# Jetson AIBox provisioning — step 10: base packages + Ollama (native, GPU)
#
# Target: JetPack 5.1.2 / L4T R35.4.1 / Ubuntu 20.04 (factory image, Path B).
# Run as root:   sudo bash 10-base-and-ollama.sh
#
# Idempotent-ish: re-running re-installs/updates the same packages.
set -euo pipefail

echo "[10] apt update + base packages (curl, pip, jq, zstd, ca-certificates)"
apt-get update
apt-get install -y --no-install-recommends \
  curl ca-certificates python3-pip jq zstd

echo "[10] Install Ollama (install.sh detects JetPack 5 and installs the CUDA arm64 build)"
curl -fsSL https://ollama.com/install.sh | sh

echo "[10] Versions:"
python3 --version
ollama --version || true

echo "[10] Ollama service status:"
systemctl is-active ollama || true

echo "[10] done."
