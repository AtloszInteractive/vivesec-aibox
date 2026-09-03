#!/usr/bin/env bash
# OLLAMA_NUM_PARALLEL A/B-hez: systemd drop-in beállítása + ollama újraindítás.
# Az agent nem tud sudózni a boxon -> ezt a user futtatja:
#     sudo bash ~/99-ollama-parallel.sh 2      # 2 párhuzamos slot
#     sudo bash ~/99-ollama-parallel.sh 4      # 4 párhuzamos slot
#     sudo bash ~/99-ollama-parallel.sh off     # vissza alapértelmezettre (drop-in törlés)
# Az újraindítás kirakja a modellt a VRAM-ból -> a következő kérés hidegindít (~30-40s).
set -euo pipefail

DROPIN_DIR=/etc/systemd/system/ollama.service.d
DROPIN=$DROPIN_DIR/10-parallel.conf
ARG="${1:-}"

if [ -z "$ARG" ]; then
  echo "Használat: sudo bash $0 <N|off>"; exit 2
fi

if [ "$ARG" = "off" ]; then
  rm -f "$DROPIN"
  echo "drop-in törölve: $DROPIN"
else
  mkdir -p "$DROPIN_DIR"
  cat > "$DROPIN" <<EOF
[Service]
Environment="OLLAMA_NUM_PARALLEL=$ARG"
EOF
  echo "beírva -> $DROPIN:"; cat "$DROPIN"
fi

systemctl daemon-reload
systemctl restart ollama

# Megvárjuk, míg feláll az API.
for i in $(seq 1 30); do
  if curl -sf http://127.0.0.1:11434/api/version >/dev/null 2>&1; then break; fi
  sleep 1
done

echo "== ollama version =="; curl -s http://127.0.0.1:11434/api/version; echo
echo "== effektív env (a service-ből) =="
systemctl show ollama -p Environment | tr ' ' '\n' | grep -i OLLAMA_NUM_PARALLEL || echo "OLLAMA_NUM_PARALLEL=<default>"
echo "== betöltött modellek =="; curl -s http://127.0.0.1:11434/api/ps; echo
echo "KÉSZ. Most futtatható a 98_concurrency_bench.py."
