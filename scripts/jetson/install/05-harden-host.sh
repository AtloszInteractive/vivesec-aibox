#!/usr/bin/env bash
# ViVeSec AI Box - host hardening and remote administration channel.
#
# Two things must be true before a box leaves the workshop: nobody can log in
# with a password, and we can still reach the box after it is installed at the
# customer. This phase delivers both without touching the ViVeSecBox pairing
# path (SSDP :1900, HTTP :80, mTLS :443 are not changed).
#
#   sudo bash 05-harden-host.sh --check                     # report, change nothing
#   sudo AIBOX_ADMIN_KEYS_FILE=/home/aibox/admin_keys.pub \
#        TS_AUTH_KEY=tskey-auth-... bash 05-harden-host.sh  # apply
#   sudo bash 05-harden-host.sh --confirm-ssh               # from a NEW key-based login
#   sudo bash 05-harden-host.sh --rollback-ssh              # drop the sshd hardening
#
# SSH safety: the hardening is armed, then the script waits for a confirmation
# that a NEW key-based login works. Without confirmation inside the grace
# window the drop-in is removed and password login comes back; a transient
# systemd timer does the same if this script is killed. The user's own
# ~/.ssh/authorized_keys is never deleted.
#
# Tailscale rules that protect pairing: no exit node, no subnet routes, no
# MagicDNS. An exit node would move the default route to tailscale0 and the
# SSDP LOCATION would advertise a tailnet address.
set -euo pipefail

ssh_user="${AIBOX_SSH_USER:-aibox}"
keys_file="${AIBOX_ADMIN_KEYS_FILE:-}"
keys_dir=/etc/ssh/authorized_keys.d
dropin=/etc/ssh/sshd_config.d/10-aibox-hardening.conf
confirm_flag=/run/aibox-ssh-confirmed
deadman_unit=aibox-ssh-deadman
grace_seconds="${AIBOX_SSH_GRACE_SECONDS:-900}"
remote_access="${AIBOX_REMOTE_ACCESS:-tailscale}"
ts_tags="${AIBOX_TS_TAGS:-tag:aibox}"
ts_hostname="${AIBOX_HOSTNAME:-$(hostname)}"
ts_auth_key="${TS_AUTH_KEY:-}"
mode="${1:-}"

log() { printf '[%s] %s\n' "$(date +%H:%M:%S)" "$*"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

sshd_reload() {
  sshd -t
  if systemctl list-unit-files ssh.service >/dev/null 2>&1 \
     && systemctl is-active ssh.service >/dev/null 2>&1; then
    systemctl reload ssh.service
  else
    systemctl reload sshd.service
  fi
}

sshd_value() { sshd -T 2>/dev/null | awk -v k="$1" '$1 == k {print $2; exit}'; }

cgnat_regex='^100\.(6[4-9]|[7-9][0-9]|1[01][0-9]|12[0-7])\.'

default_route_report() {
  local route dev src
  route="$(ip -o route get 203.0.113.1 2>/dev/null || true)"
  dev="$(sed -nE 's/.* dev ([^ ]+).*/\1/p' <<<"$route")"
  src="$(sed -nE 's/.* src ([^ ]+).*/\1/p' <<<"$route")"
  printf '%s %s\n' "${dev:-?}" "${src:-?}"
}

default_route_ok() {
  read -r dev src <<<"$(default_route_report)"
  [[ $dev != tailscale0 ]] && ! grep -qE "$cgnat_regex" <<<"$src"
}

report_state() {
  echo "-- SSH ------------------------------------------------------------------"
  if command -v sshd >/dev/null; then
    printf '  password auth   : %s\n' "$(sshd_value passwordauthentication)"
    printf '  kbd-interactive : %s\n' "$(sshd_value kbdinteractiveauthentication)"
    printf '  root login      : %s\n' "$(sshd_value permitrootlogin)"
    printf '  authorized keys : %s\n' "$(sshd_value authorizedkeysfile)"
    printf '  hardening dropin: %s\n' "$([[ -f $dropin ]] && echo present || echo absent)"
    if [[ -s $keys_dir/$ssh_user ]]; then
      printf '  admin keys      : %s key(s) in %s\n' \
        "$(grep -cE '^(ssh-|sk-|ecdsa-)' "$keys_dir/$ssh_user")" "$keys_dir/$ssh_user"
    else
      printf '  admin keys      : none in %s\n' "$keys_dir/$ssh_user"
    fi
  else
    echo "  openssh-server not installed"
  fi
  echo "-- Remote access --------------------------------------------------------"
  if command -v tailscale >/dev/null; then
    local st
    st="$(tailscale status --json 2>/dev/null || echo '{}')"
    printf '  tailscale       : %s %s\n' \
      "$(jq -r '.BackendState // "not running"' <<<"$st")" \
      "$(jq -r '.Self.DNSName // ""' <<<"$st")"
    printf '  key expiry      : %s\n' \
      "$(jq -r 'if .Self.KeyExpiry == null then "disabled (good)" else .Self.KeyExpiry + "  <- disable in the admin console" end' <<<"$st")"
  else
    echo "  tailscale       : not installed"
  fi
  read -r dev src <<<"$(default_route_report)"
  printf '  default route   : dev %s src %s (%s)\n' "$dev" "$src" \
    "$(default_route_ok && echo 'LAN, good' || echo 'NOT on the LAN - pairing would break')"
}

# --- subcommands -----------------------------------------------------------
case $mode in
  --check)
    report_state
    if [[ -n $keys_file ]]; then
      [[ -r $keys_file ]] || die "AIBOX_ADMIN_KEYS_FILE not readable: $keys_file"
      n="$(grep -cE '^(ssh-|sk-|ecdsa-)' "$keys_file" || true)"
      [[ $n -ge 1 ]] || die "No public keys found in $keys_file"
      echo "  keys to install : $n from $keys_file"
    fi
    echo "AIBOX_HARDEN_CHECK_OK"
    exit 0
    ;;
  --confirm-ssh)
    [[ $EUID -eq 0 ]] || die "Run as root."
    [[ -f $dropin ]] || die "No hardening drop-in is armed ($dropin missing)."
    touch "$confirm_flag"
    systemctl stop "$deadman_unit.timer" >/dev/null 2>&1 || true
    systemctl stop "$deadman_unit.service" >/dev/null 2>&1 || true
    echo "AIBOX_SSH_CONFIRMED key-based login confirmed, hardening kept"
    exit 0
    ;;
  --rollback-ssh)
    [[ $EUID -eq 0 ]] || die "Run as root."
    rm -f "$dropin" "$confirm_flag"
    systemctl stop "$deadman_unit.timer" >/dev/null 2>&1 || true
    sshd_reload
    echo "AIBOX_SSH_ROLLBACK drop-in removed, sshd reloaded (admin key file kept)"
    exit 0
    ;;
  "") ;;
  *) die "Unknown argument: $mode (expected --check | --confirm-ssh | --rollback-ssh)" ;;
esac

[[ $EUID -eq 0 ]] || die "Run as root (sudo bash $0)."
id "$ssh_user" >/dev/null 2>&1 || die "SSH user does not exist: $ssh_user"
export DEBIAN_FRONTEND=noninteractive

for pkg in openssh-server jq curl ca-certificates; do
  dpkg-query -W "$pkg" >/dev/null 2>&1 || { apt-get update; apt-get install -y "$pkg"; }
done
systemctl enable --now ssh.service >/dev/null 2>&1 || systemctl enable --now sshd.service

# --- 0. network services the box does not need --------------------------------
# rpcbind (pulled in by nfs-common on the L4T image) listens on 0.0.0.0:111.
for unit in rpcbind.socket rpcbind.service; do
  if systemctl list-unit-files "$unit" 2>/dev/null | grep -q "^$unit"; then
    systemctl disable --now "$unit" >/dev/null 2>&1 || true
  fi
done
log "rpcbind: $(systemctl is-active rpcbind.socket 2>/dev/null || echo absent)"

# --- 1. admin keys in a root-owned file --------------------------------------
# A compromised service running as the login user must not be able to add keys,
# so sshd reads only this root-owned file. Existing user keys are carried over.
install -d -m 0755 "$keys_dir"
target="$keys_dir/$ssh_user"
user_home="$(getent passwd "$ssh_user" | cut -d: -f6)"
tmp_keys="$(mktemp)"
trap 'rm -f "$tmp_keys"' EXIT
{
  [[ -n $keys_file ]] && cat "$keys_file"
  [[ -s $target ]] && cat "$target"
  [[ -s $user_home/.ssh/authorized_keys ]] && cat "$user_home/.ssh/authorized_keys"
  true
} | grep -E '^(ssh-|sk-|ecdsa-)' | awk '!seen[$1" "$2]++' > "$tmp_keys" || true
key_count="$(wc -l < "$tmp_keys")"
[[ $key_count -ge 1 ]] || die "No admin public key available: set AIBOX_ADMIN_KEYS_FILE (a file with the administrators' public keys)."
while IFS= read -r line; do
  printf '%s\n' "$line" > "$tmp_keys.one"
  ssh-keygen -l -f "$tmp_keys.one" >/dev/null 2>&1 || die "Invalid public key line: ${line:0:40}..."
done < "$tmp_keys"
rm -f "$tmp_keys.one"
install -o root -g root -m 0644 "$tmp_keys" "$target"
log "Admin keys: $key_count key(s) installed in $target (root-owned)"

# --- 2. sshd hardening drop-in ---------------------------------------------
# 10- prefix: sshd takes the first occurrence, and 50-cloud-init.conf sets
# PasswordAuthentication yes on stock Ubuntu images.
previous_dropin=""
if [[ -f $dropin ]]; then
  previous_dropin="$(mktemp)"
  cp "$dropin" "$previous_dropin"
fi
cat > "$dropin" <<CONF
# ViVeSec AI Box hardening - managed by 05-harden-host.sh
PubkeyAuthentication yes
AuthorizedKeysFile $keys_dir/%u
PasswordAuthentication no
KbdInteractiveAuthentication no
ChallengeResponseAuthentication no
PermitEmptyPasswords no
PermitRootLogin no
AllowUsers $ssh_user
MaxAuthTries 3
LoginGraceTime 30
X11Forwarding no
CONF
chmod 0644 "$dropin"
if ! sshd -t 2>/tmp/aibox-sshd-t.err; then
  rm -f "$dropin"
  [[ -n $previous_dropin ]] && cp "$previous_dropin" "$dropin"
  die "sshd rejected the hardening drop-in, nothing changed: $(cat /tmp/aibox-sshd-t.err)"
fi

# Dead man's switch: revert unless a new key login is confirmed in time.
rm -f "$confirm_flag"
systemctl stop "$deadman_unit.timer" >/dev/null 2>&1 || true
systemctl stop "$deadman_unit.service" >/dev/null 2>&1 || true
systemctl reset-failed "$deadman_unit.service" >/dev/null 2>&1 || true
systemd-run --unit="$deadman_unit" --on-active="$grace_seconds" --quiet \
  --description="Revert AI Box sshd hardening if key login is not confirmed" \
  /bin/bash -c "test -e $confirm_flag || { rm -f $dropin; systemctl reload ssh.service 2>/dev/null || systemctl reload sshd.service; logger -t aibox 'sshd hardening reverted: key login not confirmed'; }"
sshd_reload
log "sshd hardening ARMED (password login off). Grace window: ${grace_seconds}s."

cat <<MSG

  >>> CONFIRM NOW from a NEW terminal (do not reuse this session):
  >>>   ssh -o PasswordAuthentication=no $ssh_user@$(hostname -I | awk '{print $1}')
  >>>   sudo bash $(readlink -f "$0") --confirm-ssh
  >>> If this does not happen within ${grace_seconds}s the hardening is reverted automatically.

MSG

if [[ ${AIBOX_SSH_NOWAIT:-0} != 1 ]]; then
  waited=0
  while [[ ! -e $confirm_flag ]]; do
    if [[ ! -f $dropin ]]; then
      die "sshd hardening was reverted by the grace timer - key login was not confirmed."
    fi
    if [[ $waited -ge $grace_seconds ]]; then
      rm -f "$dropin"; sshd_reload
      systemctl stop "$deadman_unit.timer" >/dev/null 2>&1 || true
      die "No confirmation within ${grace_seconds}s - hardening reverted, password login restored."
    fi
    sleep 5; waited=$((waited + 5))
    (( waited % 60 == 0 )) && log "waiting for --confirm-ssh (${waited}/${grace_seconds}s)"
  done
  log "Key-based login confirmed; hardening kept."
fi

# --- 3. remote administration channel (Tailscale) --------------------------
ts_state="skipped"
if [[ $remote_access == tailscale ]]; then
  if ! command -v tailscale >/dev/null; then
    # shellcheck disable=SC1091
    . /etc/os-release
    curl -fsSL "https://pkgs.tailscale.com/stable/ubuntu/${VERSION_CODENAME}.noarmor.gpg" \
      -o /usr/share/keyrings/tailscale-archive-keyring.gpg
    curl -fsSL "https://pkgs.tailscale.com/stable/ubuntu/${VERSION_CODENAME}.tailscale-keyring.list" \
      -o /etc/apt/sources.list.d/tailscale.list
    apt-get update
    apt-get install -y tailscale
  fi
  systemctl enable --now tailscaled
  backend="$(tailscale status --json 2>/dev/null | jq -r '.BackendState // ""')"
  if [[ -n ${TS_AUTH_KEY_FILE:-} && -z $ts_auth_key ]]; then
    ts_auth_key="$(tr -d '[:space:]' < "$TS_AUTH_KEY_FILE")"
  fi
  if [[ $backend != Running ]]; then
    if [[ -z $ts_auth_key ]]; then
      log "Tailscale installed but not joined: rerun with TS_AUTH_KEY=tskey-auth-... (pre-authorized, tagged $ts_tags)."
    else
      # --reset drops any previous prefs; routes/DNS stay off on purpose (see header).
      tailscale up --reset --auth-key="$ts_auth_key" --hostname="$ts_hostname" \
        --advertise-tags="$ts_tags" --accept-dns=false --accept-routes=false \
        --advertise-exit-node=false --ssh=false --timeout=90s
      unset ts_auth_key
    fi
  else
    # Already joined: re-assert the pairing-safe prefs without re-authenticating.
    tailscale set --accept-dns=false --accept-routes=false --advertise-exit-node=false \
      --exit-node= --advertise-routes= >/dev/null 2>&1 || true
  fi
  st="$(tailscale status --json 2>/dev/null || echo '{}')"
  backend="$(jq -r '.BackendState // ""' <<<"$st")"
  if [[ $backend == Running ]]; then
    ts_state="online:$(jq -r '.Self.DNSName' <<<"$st")"
    if [[ $(jq -r '.Self.KeyExpiry // "null"' <<<"$st") != null ]]; then
      log "WARNING: Tailscale key expiry is ENABLED ($(jq -r .Self.KeyExpiry <<<"$st")). Disable it for this node in the admin console or remote access is lost when it expires."
    fi
  else
    ts_state="installed:$backend"
  fi
fi

# --- 4. the pairing guard: the default route must still be the LAN ---------
default_route_ok || die "Default route is on the tailnet ($(default_route_report)) - SSDP would advertise a tailnet address. Run: tailscale set --exit-node= --accept-routes=false"

echo
report_state
echo "AIBOX_HARDEN_OK ssh=key-only user=$ssh_user keys=$key_count remote=$ts_state route=$(default_route_report | tr ' ' '/')"
