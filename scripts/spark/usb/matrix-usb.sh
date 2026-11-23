#!/bin/bash
# Matrix AI on a DGX Spark from a USB stick (E07). The Spark needs internet:
# the images come from the signed update channel named in matrix.conf.
#
# The stick holds this script, matrix.conf and one matrix-kit-<version>.tgz
# (the release kit of that channel, from pack_release.py):
#   sudo bash /media/<user>/<stick>/matrix-usb.sh install    first installation (or again)
#   sudo bash .../matrix-usb.sh admin                        local administrator (web page), temporary password
#   sudo bash .../matrix-usb.sh ids                          ViVeSecBox users the box knows, and refused ones
#   sudo bash .../matrix-usb.sh admin-vvs <VVS-User>         that ViVeSecBox user administers the box
#   sudo bash .../matrix-usb.sh map <VVS-User> <user>        let a ViVeSecBox user in as an existing <user>
#   sudo bash .../matrix-usb.sh status
# A ViVeSecBox user's first visit creates its own analyst without rooms
# (MATRIX_TUNNEL_AUTOCREATE); an administrator gives it rooms.
# Later versions come through the administration page (Updates tab).
# Logs: /var/log/vivesec-matrix-usb/ on the Spark, copied to logs/ on the stick.
set -euo pipefail

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
CONF=/etc/vivesec-matrix/matrix.conf
KIT_DIR=/opt/vivesec-matrix-kit
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
  *) sed -n '2,16p' "$0"; exit 2 ;;
esac
