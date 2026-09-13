#!/usr/bin/env bash
set -euo pipefail

[[ $EUID -eq 0 ]] || { echo "Run as root." >&2; exit 1; }
[[ $(findmnt -nro SOURCE /) == /dev/mmcblk0p1 ]] || {
  echo "Unexpected root filesystem." >&2
  exit 1
}
[[ $(findmnt -nro SOURCE /data) == /dev/nvme0n1p1 ]] || {
  echo "Expected /data on /dev/nvme0n1p1." >&2
  exit 1
}

for directory in app rag adapter generated sessions jobs feedback; do
  install -d -o aibox -g aibox -m 0750 "/data/$directory"
done
install -d -o root -g root -m 0700 /data/pki

echo "APP_STORAGE_READY root=/dev/mmcblk0p1 data=/dev/nvme0n1p1"