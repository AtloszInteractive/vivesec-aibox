#!/usr/bin/env bash
set -euo pipefail

device="${1:?usage: $0 DEVICE EXPECTED_SERIAL EXPECTED_SIZE_BYTES [--check]}"
expected_model="AFOX SSD ME300-512GN"
expected_serial="${2:?missing expected serial}"
expected_size="${3:?missing expected size in bytes}"
mode="${4:-}"
mountpoint=/data

if [[ $mode != --check && $EUID -ne 0 ]]; then
  echo "Run as root." >&2
  exit 1
fi

if [[ ! -b $device || $device != /dev/nvme0n1 ]]; then
  echo "Refusing unexpected target device: $device" >&2
  exit 1
fi

actual_model="$(lsblk -dn -o MODEL "$device" | sed 's/[[:space:]]*$//')"
actual_serial="$(lsblk -dn -o SERIAL "$device" | sed 's/[[:space:]]*$//')"
actual_size="$(lsblk -bdn -o SIZE "$device")"

if [[ $actual_model != "$expected_model" || $actual_serial != "$expected_serial" || $actual_size != "$expected_size" ]]; then
  printf 'Device identity mismatch: model=%q serial=%q size=%q\n' \
    "$actual_model" "$actual_serial" "$actual_size" >&2
  exit 1
fi

root_source="$(findmnt -nro SOURCE /)"
root_disk="/dev/$(lsblk -ndo PKNAME "$root_source")"
if [[ $root_source == "$device" || $root_disk == "$device" ]]; then
  echo "Refusing to erase the root device: $device" >&2
  exit 1
fi

if lsblk -nrpo MOUNTPOINTS "$device" | grep -q '[^[:space:]]'; then
  echo "Refusing to erase a mounted device: $device" >&2
  exit 1
fi

if grep -Eq "^[^#].*[[:space:]]${mountpoint//\//\/}[[:space:]]" /etc/fstab; then
  echo "Refusing to replace an existing $mountpoint entry in /etc/fstab." >&2
  exit 1
fi

printf 'NVME_PREFLIGHT_OK device=%s model=%q serial=%q size=%s root=%s\n' \
  "$device" "$actual_model" "$actual_serial" "$actual_size" "$root_source"
if [[ $mode == --check ]]; then
  exit 0
fi

wipefs --all "$device"
parted --script "$device" mklabel gpt mkpart data ext4 0% 100%
partprobe "$device"
udevadm settle

partition="${device}p1"
mkfs.ext4 -F -L data "$partition"
uuid="$(blkid -s UUID -o value "$partition")"

mkdir -p "$mountpoint"
cp --archive /etc/fstab "/etc/fstab.pre-data-$(date +%Y%m%d-%H%M%S)"
printf 'UUID=%s %s ext4 defaults,nofail,x-systemd.device-timeout=10 0 2\n' \
  "$uuid" "$mountpoint" >> /etc/fstab
mount "$mountpoint"

install -d -o aibox -g aibox "$mountpoint/models" "$mountpoint/app" "$mountpoint/documents"
install -d -o root -g root "$mountpoint/docker"

echo "NVME_PROVISIONED device=$device partition=$partition uuid=$uuid mountpoint=$mountpoint"