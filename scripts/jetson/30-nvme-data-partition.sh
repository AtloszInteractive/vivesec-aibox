#!/usr/bin/env bash
# J1a — Carve a 256 GiB ext4 data partition on the NVMe and move the Ollama
# models onto it. The remaining free space is left UNALLOCATED on purpose so the
# ViVeSec encrypted (LUKS) volume can be carved later at the size they need,
# with no ext4 shrink / data move required.
#
# Run as root:  sudo bash 30-nvme-data-partition.sh
set -euo pipefail

DISK="/dev/nvme0n1"
PART="${DISK}p1"
DATA_SIZE="256GiB"     # end position of p1 (start = 1MiB)
MOUNT="/data"
LABEL="aibox-data"
OLLAMA_DST="${MOUNT}/ollama/models"
OLLAMA_SRC="/usr/share/ollama/.ollama/models"

echo "[30] Pre-flight safety checks on ${DISK}"
[ -b "$DISK" ] || { echo "ERROR: $DISK not found"; exit 1; }
# Refuse if the disk already has any partition or is mounted anywhere.
if lsblk -no NAME "$DISK" | grep -q "${DISK##*/}p"; then
  echo "ERROR: $DISK already has partitions. Aborting (manual review needed)."
  lsblk "$DISK"; exit 1
fi
if mount | grep -q "$DISK"; then
  echo "ERROR: something on $DISK is mounted. Aborting."; exit 1
fi

echo "[30] Creating GPT label + ${DATA_SIZE} partition (rest left free for ViVeSec LUKS)"
parted -s -a optimal "$DISK" mklabel gpt
parted -s -a optimal "$DISK" mkpart "$LABEL" ext4 1MiB "$DATA_SIZE"
partprobe "$DISK"
sleep 1
udevadm settle || true

echo "[30] Formatting ${PART} as ext4 (label ${LABEL})"
mkfs.ext4 -F -L "$LABEL" "$PART"

UUID="$(blkid -s UUID -o value "$PART")"
echo "[30] ${PART} UUID = ${UUID}"

echo "[30] Mounting at ${MOUNT} and adding fstab entry (by UUID)"
mkdir -p "$MOUNT"
if ! grep -q "$UUID" /etc/fstab; then
  echo "UUID=${UUID}  ${MOUNT}  ext4  defaults,noatime  0  2" >> /etc/fstab
fi
mount "$MOUNT"

echo "[30] Moving Ollama models to ${OLLAMA_DST}"
systemctl stop ollama || true
mkdir -p "$OLLAMA_DST"
if [ -d "$OLLAMA_SRC" ] && [ -n "$(ls -A "$OLLAMA_SRC" 2>/dev/null || true)" ]; then
  cp -a "${OLLAMA_SRC}/." "${OLLAMA_DST}/"
  rm -rf "${OLLAMA_SRC}"
fi
chown -R ollama:ollama "${MOUNT}/ollama"

echo "[30] Pointing the ollama service at the new models dir via systemd override"
mkdir -p /etc/systemd/system/ollama.service.d
cat > /etc/systemd/system/ollama.service.d/override.conf <<CONF
[Service]
Environment="OLLAMA_MODELS=${OLLAMA_DST}"
CONF
systemctl daemon-reload
systemctl start ollama
sleep 2

echo "[30] ---- verification ----"
echo "[30] lsblk:"; lsblk "$DISK"
echo "[30] df ${MOUNT}:"; df -h "$MOUNT"
echo "[30] ollama service: $(systemctl is-active ollama)"
echo "[30] ollama models dir in use:"; systemctl show ollama -p Environment | tr ' ' '\n' | grep OLLAMA_MODELS || true
echo "[30] ollama list:"; ollama list || true
echo "[30] done."
