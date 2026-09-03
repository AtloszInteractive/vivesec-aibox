#!/usr/bin/env bash
# Ollama upgrade on the Jetson (JetPack 5 / L4T R35) with backup + rollback.
#
#   sudo bash 116-ollama-upgrade.sh                 # upgrade to $VER
#   sudo bash 116-ollama-upgrade.sh --rollback FILE # restore a backup tarball
#
# Needed because qwen3.8:27b requires Ollama >= 0.32.12 (this box ran 0.30.10).
# The release ships the runtime in TWO archives: the generic arm64 one (bin+lib)
# and a jetpack5 add-on that only carries lib/ollama/cuda_jetpack5.
set -euo pipefail

VER="${OLLAMA_VER:-v0.32.15}"
BASE_URL="https://github.com/ollama/ollama/releases/download/$VER"
A_MAIN="ollama-linux-arm64.tar.zst"
A_JP5="ollama-linux-arm64-jetpack5.tar.zst"
STAGE="${STAGE:-/home/aibox/ollama-upgrade}"
TS=$(date +%Y%m%d-%H%M%S)

[ "$(id -u)" = "0" ] || { echo "run with sudo"; exit 1; }

wait_api() {
  for _ in $(seq 1 60); do
    curl -sf http://127.0.0.1:11434/api/version >/dev/null && return 0
    sleep 2
  done
  return 1
}

restore() { # backup tarball
  systemctl stop ollama
  rm -rf /usr/local/lib/ollama
  tar -I zstd -xf "$1" -C /
  systemctl start ollama
}

if [ "${1:-}" = "--rollback" ]; then
  BK="${2:?usage: --rollback /path/to/ollama-backup-*.tar.zst}"
  echo "[rollback] from $BK"
  restore "$BK"
  wait_api && echo "[rollback] server $(curl -s http://127.0.0.1:11434/api/version)" || { echo "API did not come up"; exit 1; }
  exit 0
fi

echo "[1/6] preflight"
echo "  current server: $(curl -s http://127.0.0.1:11434/api/version || echo unreachable)   target: $VER"
mkdir -p "$STAGE"
for a in "$A_MAIN" "$A_JP5"; do
  [ -s "$STAGE/$a" ] || { echo "  downloading $a"; curl -fL --retry 3 -o "$STAGE/$a" "$BASE_URL/$a"; }
done

echo "[2/6] unpack to staging and verify the binary runs"
rm -rf "$STAGE/new"; mkdir -p "$STAGE/new"
tar -I zstd -xf "$STAGE/$A_MAIN" -C "$STAGE/new"
tar -I zstd -xf "$STAGE/$A_JP5"  -C "$STAGE/new"
[ -x "$STAGE/new/bin/ollama" ] || { echo "no bin/ollama in the archive"; exit 1; }
[ -d "$STAGE/new/lib/ollama/cuda_jetpack5" ] || { echo "jetpack5 CUDA libs missing"; exit 1; }
"$STAGE/new/bin/ollama" --version 2>&1 | head -2

echo "[3/6] backup current install -> $STAGE/ollama-backup-$TS.tar.zst"
tar -I zstd -cf "$STAGE/ollama-backup-$TS.tar.zst" \
    /usr/local/bin/ollama /usr/local/lib/ollama
ls -la "$STAGE/ollama-backup-$TS.tar.zst"

echo "[4/6] stop service and install"
systemctl stop ollama
rm -rf /usr/local/lib/ollama
cp -a "$STAGE/new/bin/ollama" /usr/local/bin/ollama
cp -a "$STAGE/new/lib/ollama" /usr/local/lib/ollama
chown -R root:root /usr/local/lib/ollama /usr/local/bin/ollama

echo "[5/6] start and verify"
systemctl start ollama
wait_api || { echo "API DID NOT COME UP -> rolling back"; restore "$STAGE/ollama-backup-$TS.tar.zst"; exit 1; }
echo "  server now: $(curl -s http://127.0.0.1:11434/api/version)"
ollama list

echo "[6/6] smoke: the production model must still generate on the GPU"
curl -s http://127.0.0.1:11434/api/generate \
  -d '{"model":"qwen3.6:35b","prompt":"Say OK.","stream":false,"think":false,"options":{"num_predict":8}}' \
  | head -c 400
echo; echo
ollama ps
echo
echo "DONE. Rollback if anything looks wrong:"
echo "  sudo bash $0 --rollback $STAGE/ollama-backup-$TS.tar.zst"
