#!/bin/bash
# Matrix AI on a DGX Spark from a USB stick (E07). The Spark needs internet:
# the images come from the signed update channel named in matrix.conf.
#
# The stick (exFAT or ext4) holds this script, matrix.conf, one matrix-kit-<version>.tgz
# (the release kit of that channel, from pack_release.py) and matrix-models/
# (the models that kit pins, from pack_models.py):
#   sudo bash /media/<user>/<stick>/matrix-usb.sh install    first installation (or again)
#   sudo bash .../matrix-usb.sh models                       only copy the models (then: Updates tab)
#   sudo bash .../matrix-usb.sh own-ollama                   switch an installed box to its own Ollama
#   sudo bash .../matrix-usb.sh admin                        local administrator (web page), temporary password
#   sudo bash .../matrix-usb.sh ids                          ViVeSecBox users the box knows, and refused ones
#   sudo bash .../matrix-usb.sh admin-vvs <VVS-User>         that ViVeSecBox user administers the box
#   sudo bash .../matrix-usb.sh map <VVS-User> <user>        let a ViVeSecBox user in as an existing <user>
#   sudo bash .../matrix-usb.sh status
# A ViVeSecBox user's first visit creates its own analyst in the default rooms
# (MATRIX_TUNNEL_AUTOCREATE, MATRIX_TUNNEL_DEFAULT_DRIVES). The owner of the
# ViVeSecBox becomes the administrator on its own visit (MATRIX_TUNNEL_OWNER_ADMIN);
# admin-vvs is only needed when that is off or for a further administrator.
# Later versions come through the administration page (Updates tab); a version
# with new models needs `models` from its stick first.
# Logs: /var/log/vivesec-matrix-usb/ on the Spark, copied to logs/ on the stick.
set -euo pipefail

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
CONF=/etc/vivesec-matrix/matrix.conf
KIT_DIR=/opt/vivesec-matrix-kit
OLLAMA_DIR=/var/lib/vivesec-matrix-ollama
INCOMING=/var/lib/vivesec-matrix-ollama.incoming
MANAGE=(docker exec vivesec-auth python /app/auth_service/manage.py)
LOG=

die() { echo "ERROR: $*" >&2; exit 1; }
[ "$(id -u)" -eq 0 ] || die "run with sudo"

log_to() {
  mkdir -p /var/log/vivesec-matrix-usb
  LOG="/var/log/vivesec-matrix-usb/$1-$(date +%Y%m%d-%H%M%S).log"
  exec > >(tee -a "$LOG") 2>&1
  echo "log: $LOG"
}

copy_log() {
  # The stick may be read-only or gone; the log stays on the Spark anyway.
  [ -n "$LOG" ] && mkdir -p "$HERE/logs" 2>/dev/null && cp "$LOG" "$HERE/logs/" 2>/dev/null || true
}

ensure_nginx() {
  [ -x /usr/sbin/nginx ] && return
  echo "== nginx (binary only: the tunnel front runs its own instance)"
  # The packaged service would take port 80, which belongs to the Dokumentum AI.
  systemctl mask nginx.service
  apt-get update -q
  DEBIAN_FRONTEND=noninteractive apt-get install -y -q nginx-core
}

blob_size() { stat -c %s "$1" 2>/dev/null || echo -1; }

stage_models() {
  # The stick's models onto the disk, where the installer checks each file against the
  # signed kit (sha256) before Ollama sees it. Blobs the box holds already are skipped.
  local src="$HERE/matrix-models" blob name size need=0 free done_=0
  [ -d "$src/manifests" ] && [ -d "$src/blobs" ] || return 1
  echo "== models: $src -> $INCOMING"
  mkdir -p "$INCOMING/blobs"
  chmod 700 "$INCOMING"
  rm -rf "$INCOMING/manifests"
  cp -rP "$src/manifests" "$INCOMING/manifests"
  for blob in "$src"/blobs/sha256-*; do
    [ -f "$blob" ] && [ ! -L "$blob" ] || continue
    name=${blob##*/}
    size=$(blob_size "$blob")
    [ "$(blob_size "$OLLAMA_DIR/blobs/$name")" = "$size" ] || [ "$(blob_size "$INCOMING/blobs/$name")" = "$size" ] \
      || need=$((need + size))
  done
  free=$(df --output=avail -B1 "$INCOMING" | tail -1)
  [ "$need" -lt "$free" ] || die "$INCOMING has $((free >> 30)) GB free, the models need $((need >> 30)) GB"
  for blob in "$src"/blobs/sha256-*; do
    [ -f "$blob" ] && [ ! -L "$blob" ] || continue
    name=${blob##*/}
    size=$(blob_size "$blob")
    if [ "$(blob_size "$OLLAMA_DIR/blobs/$name")" = "$size" ] || [ "$(blob_size "$INCOMING/blobs/$name")" = "$size" ]; then
      continue
    fi
    echo "   $name ($((size >> 20)) MB)"
    cp "$blob" "$INCOMING/blobs/.$name.part"
    mv "$INCOMING/blobs/.$name.part" "$INCOMING/blobs/$name"
    done_=$((done_ + size))
  done
  sync
  echo "== models copied ($((done_ >> 20)) MB); the installer checks them against the kit"
}

models_present() {
  [ -d "$HERE/matrix-models/manifests" ] || [ -d "$INCOMING/manifests" ] \
    || [ -d "$OLLAMA_DIR/manifests/registry.ollama.ai" ]
}

set_conf() {
  if grep -q "^$1=" "$CONF"; then
    sed -i "s|^$1=.*|$1=$2|" "$CONF"
  else
    printf '%s=%s\n' "$1" "$2" >> "$CONF"
  fi
}

own_ollama() {
  [ -f "$CONF" ] || die "$CONF missing: install first"
  # A rollback reinstalls the previous version with this file: switch only with the models
  # at hand, so the release that brings them can start the own Ollama.
  models_present || die "no models on the stick (matrix-models/) nor on the box: they come with the release"
  cp -p "$CONF" "$CONF.bak-$(date +%Y%m%d-%H%M%S)"
  set_conf MATRIX_OLLAMA own
  set_conf MATRIX_OLLAMA_URL http://127.0.0.1:11435
  echo "== $CONF: MATRIX_OLLAMA=own on 127.0.0.1:11435 (previous file kept as $CONF.bak-*)"
  echo "Next: sudo bash $0 install (or: $0 models, then approve the release on the Updates tab)"
}

install_all() {
  log_to install
  trap 'copy_log; rm -rf "${WORK:-/nonexistent}"' EXIT
  local kits=("$HERE"/matrix-kit-*.tgz)
  [ ${#kits[@]} -eq 1 ] && [ -f "${kits[0]}" ] || die "exactly one matrix-kit-<version>.tgz must be next to this script"
  docker info >/dev/null 2>&1 || die "docker does not answer"
  ensure_nginx
  if [ -f "$CONF" ]; then
    echo "== $CONF exists, kept"
  else
    [ -f "$HERE/matrix.conf" ] || die "matrix.conf missing next to this script"
    install -D -m 600 "$HERE/matrix.conf" "$CONF"
    echo "== $CONF installed from the stick"
  fi
  grep -q '^MATRIX_UPDATE=on' "$CONF" || die "$CONF needs MATRIX_UPDATE=on and the channel (MATRIX_UPDATE_URL)"
  if ! stage_models; then
    grep -q '^MATRIX_OLLAMA=own' "$CONF" \
      && echo "WARNING: no matrix-models/ on the stick; the installer stops unless the box holds the release's models"
  fi
  # The updater runs from a scratch copy of the kit; it checks the kit against the
  # signed release before anything is installed.
  WORK=$(mktemp -d /var/tmp/matrix-bootstrap.XXXXXX)
  tar -xzf "${kits[0]}" -C "$WORK" src/matrix_updater wheels
  mkdir "$WORK/lib"
  for wheel in "$WORK"/wheels/*.whl; do
    python3 -c 'import sys, zipfile; zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])' "$wheel" "$WORK/lib"
  done
  echo "== ${kits[0]##*/}: signed check, images from the channel, install"
  PYTHONPATH="$WORK/lib" PYTHONDONTWRITEBYTECODE=1 \
    python3 -S -B "$WORK/src/matrix_updater/updater.py" bootstrap "${kits[0]}"
  python3 "$KIT_DIR/src/scripts/spark/spark_install.py" status
  echo
  echo "MATRIX_INSTALLED. Next: sudo bash $0 admin"
}

admin() {
  local password
  if "${MANAGE[@]}" list-users | grep -q '"username": "admin"'; then
    die "the admin user exists already (reset: docker exec -it vivesec-auth python /app/auth_service/manage.py reset-password admin --temporary)"
  fi
  password=$(python3 -c 'import secrets; print(secrets.token_urlsafe(12))')
  printf '%s\n' "$password" | docker exec -i vivesec-auth python /app/auth_service/manage.py \
    create-user admin --role admin --temporary --password-stdin >/dev/null
  (umask 077; printf 'admin %s\n' "$password" > /root/matrix-admin-password.txt)
  echo "admin created; its temporary password is in /root/matrix-admin-password.txt (sudo cat ...)."
  echo "Sign in on this Spark at http://127.0.0.1:8080 and set a new password."
}

ids() {
  # The ViVeSecBox users the box knows (newest first), then identities it refused.
  local map users
  map=$("${MANAGE[@]}" tunnel-map show)
  users=$("${MANAGE[@]}" list-users)
  MAP="$map" USERS="$users" python3 - <<'EOF'
import json, os, time
mapping = json.loads(os.environ["MAP"])["users"]
users = {u["username"]: u for u in json.loads(os.environ["USERS"])}
rows = []
for vvs, name in mapping.items():
    u = users.get(name, {})
    rows.append((u.get("created_at") or 0, vvs, name, ",".join(u.get("roles") or []) or "-",
                 len(u.get("drives") or []), u.get("status", "missing")))
print("%-19s  %-28s  %-16s  %-14s  %5s  %s" % ("created", "VVS-User", "user", "roles", "rooms", "status"))
for created, vvs, name, roles, rooms, status in sorted(rows, reverse=True):
    stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(created)) if created else "-"
    print("%-19s  %-28s  %-16s  %-14s  %5d  %s" % (stamp, vvs, name, roles, rooms, status))
EOF
  echo "-- refused in the last 7 days:"
  journalctl -u vivesec-matrix-tunnel --since -7d --no-pager -o cat \
    | sed -n 's/.*identity not accepted: VVS-User=//p' | sort | uniq -c | sort -rn
}

case "${1:-}" in
  install) install_all ;;
  models)
    log_to models
    trap copy_log EXIT
    stage_models || die "no matrix-models/ (manifests/, blobs/) next to this script"
    echo "Next: approve the release on the Updates tab (or: sudo bash $0 install)"
    ;;
  own-ollama) own_ollama ;;
  admin) admin ;;
  ids) ids ;;
  admin-vvs)
    [ $# -eq 2 ] || die "usage: admin-vvs <VVS-User>"
    "${MANAGE[@]}" tunnel-map promote "$2"
    ;;
  map)
    [ $# -eq 3 ] || die "usage: map <VVS-User> <user>"
    "${MANAGE[@]}" tunnel-map set "$2" "$3"
    ;;
  status) python3 "$KIT_DIR/src/scripts/spark/spark_install.py" status; "${MANAGE[@]}" tunnel-map show ;;
  *) sed -n '2,22p' "$0"; exit 2 ;;
esac
