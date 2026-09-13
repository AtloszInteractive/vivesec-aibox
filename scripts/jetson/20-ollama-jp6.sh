#!/usr/bin/env bash
set -euo pipefail

ollama_version="${OLLAMA_VERSION:-0.34.0}"
release_file=/etc/nv_tegra_release
models_dir=/data/ollama/models

[[ $EUID -eq 0 ]] || { echo "Run as root." >&2; exit 1; }
[[ -r $release_file ]] || { echo "Missing $release_file" >&2; exit 1; }
release_line="$(head -n 1 "$release_file")"
[[ $release_line == *"# R36 "* && $release_line == *"REVISION: 4.4"* ]] || {
  echo "Unsupported L4T release: $release_line" >&2
  exit 1
}
[[ $(findmnt -nro SOURCE /data) == /dev/nvme0n1p1 ]] || {
  echo "Expected /data on /dev/nvme0n1p1." >&2
  exit 1
}

installer="$(mktemp)"
trap 'rm -f "$installer"' EXIT
curl -fsSL https://ollama.com/install.sh -o "$installer"
OLLAMA_VERSION="$ollama_version" sh "$installer"

install -d -o ollama -g ollama -m 0750 /data/ollama "$models_dir"
install -d -m 0755 /etc/systemd/system/ollama.service.d
cat > /etc/systemd/system/ollama.service.d/10-aibox.conf <<EOF
[Service]
Environment="OLLAMA_HOST=127.0.0.1:11434"
Environment="OLLAMA_MODELS=$models_dir"
EOF

systemctl daemon-reload
systemctl enable ollama.service
systemctl restart ollama.service

for _ in $(seq 1 30); do
  if curl -fsS http://127.0.0.1:11434/api/version >/dev/null; then
    server_version="$(curl -fsS http://127.0.0.1:11434/api/version | jq -r .version)"
    [[ $server_version == "$ollama_version" ]] || {
      echo "Unexpected Ollama server version: $server_version" >&2
      exit 1
    }
    echo "OLLAMA_JP6_INSTALLED version=$server_version models=$models_dir"
    exit 0
  fi
  sleep 1
done

echo "Ollama API did not become ready." >&2
systemctl status ollama.service --no-pager >&2 || true
exit 1