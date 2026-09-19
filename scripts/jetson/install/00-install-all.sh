#!/usr/bin/env bash
# ViVeSec AI Box - installation orchestrator (JetPack 6 / L4T R36.4.4).
#
# Runs the provisioning chain phase by phase on a freshly flashed Jetson AGX
# Orin. Every phase is idempotent at the phase level: a completed phase is
# recorded and can be skipped with --resume. Nothing is destroyed without an
# explicit identity check (see the nvme phase).
#
#   sudo bash 00-install-all.sh --check              # preflight only, no changes
#   sudo bash 00-install-all.sh                      # full installation
#   sudo bash 00-install-all.sh --resume             # continue after a failure
#   sudo bash 00-install-all.sh --only deploy        # re-run a single phase
#   sudo bash 00-install-all.sh --from images        # run from a phase onwards
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
JETSON_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
SCRIPTS_DIR="$(cd "$JETSON_DIR/.." && pwd)"

STATE_DIR=/var/lib/aibox-install
LOG_DIR=/var/log/aibox-install
STATE_FILE="$STATE_DIR/completed-phases"

PHASES=(nvme base base-verify harden storage ollama models models-verify images deploy stack-verify audit)

config_file="$SCRIPT_DIR/install.conf"
mode=run
from_phase=""
only_phase=""
resume=0
declare -a skip_phases=()

usage() {
  cat <<'USAGE'
Usage: sudo bash 00-install-all.sh [options]

  --config FILE   Parameter file (default: install.conf next to this script)
  --check         Run the non-destructive preflight checks only
  --list          List the phases and exit
  --from PHASE    Start at PHASE and run everything after it
  --only PHASE    Run exactly one phase
  --skip PHASE    Skip a phase (repeatable)
  --resume        Skip phases already recorded as completed
  -h, --help      This help

Phases: nvme base base-verify harden storage ollama models models-verify
        images deploy stack-verify audit

The harden phase needs TS_AUTH_KEY in the environment (pass it on the sudo
command line: sudo TS_AUTH_KEY=tskey-auth-... bash 00-install-all.sh) and
AIBOX_ADMIN_KEYS_FILE in install.conf. It blocks until a NEW key-based SSH
login is confirmed with 05-harden-host.sh --confirm-ssh.
USAGE
}

log() { printf '[%s] %s\n' "$(date +%H:%M:%S)" "$*"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

valid_phase() {
  local candidate=$1 phase
  for phase in "${PHASES[@]}"; do
    [[ $phase == "$candidate" ]] && return 0
  done
  return 1
}

while [[ $# -gt 0 ]]; do
  case $1 in
    --config) config_file="${2:?--config needs a file}"; shift 2 ;;
    --check) mode=check; shift ;;
    --list) printf '%s\n' "${PHASES[@]}"; exit 0 ;;
    --from) from_phase="${2:?--from needs a phase}"; shift 2 ;;
    --only) only_phase="${2:?--only needs a phase}"; shift 2 ;;
    --skip) skip_phases+=("${2:?--skip needs a phase}"); shift 2 ;;
    --resume) resume=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; die "Unknown argument: $1" ;;
  esac
done

[[ -n $from_phase ]] && { valid_phase "$from_phase" || die "Unknown phase: $from_phase"; }
[[ -n $only_phase ]] && { valid_phase "$only_phase" || die "Unknown phase: $only_phase"; }
for phase in "${skip_phases[@]:-}"; do
  [[ -z $phase ]] && continue
  valid_phase "$phase" || die "Unknown phase: $phase"
done

[[ $EUID -eq 0 ]] || die "Run as root (sudo bash $0)."
[[ -r $config_file ]] || die "Missing parameter file: $config_file (copy install.conf.example)."

# shellcheck disable=SC1090
source "$config_file"

: "${AIBOX_HOSTNAME:?AIBOX_HOSTNAME missing from $config_file}"
: "${NVME_DEVICE:?NVME_DEVICE missing from $config_file}"
: "${NVME_MODEL:?NVME_MODEL missing from $config_file}"
: "${NVME_SERIAL:?NVME_SERIAL missing from $config_file}"
: "${NVME_SIZE_BYTES:?NVME_SIZE_BYTES missing from $config_file}"
: "${OLLAMA_VERSION:?OLLAMA_VERSION missing from $config_file}"
: "${EMBED_MODEL:?EMBED_MODEL missing from $config_file}"
: "${GEN_MODEL:?GEN_MODEL missing from $config_file}"
: "${SOURCE_DIR:?SOURCE_DIR missing from $config_file}"
: "${UI_BUNDLE:?UI_BUNDLE missing from $config_file}"
[[ $NVME_SERIAL == CHANGE-ME ]] && die "NVME_SERIAL is still the placeholder in $config_file."

install -d -m 0750 "$STATE_DIR" "$LOG_DIR"
log_file="$LOG_DIR/install-$(date +%Y%m%d-%H%M%S).log"
exec > >(tee -a "$log_file") 2>&1
log "Log file: $log_file"
log "Box: $AIBOX_HOSTNAME  Source: $SOURCE_DIR"

# ---------------------------------------------------------------------------
# Phases
# ---------------------------------------------------------------------------

phase_nvme() {
  EXPECTED_NVME_MODEL="$NVME_MODEL" bash "$SCRIPTS_DIR/provision_jetson_nvme.sh" \
    "$NVME_DEVICE" "$NVME_SERIAL" "$NVME_SIZE_BYTES"
}

phase_base() {
  bash "$JETSON_DIR/10b-base-jp6.sh"
}

phase_base_verify() {
  bash "$JETSON_DIR/verify-jp6-base.sh"
}

phase_harden() {
  AIBOX_HOSTNAME="$AIBOX_HOSTNAME" \
  AIBOX_SSH_USER="${AIBOX_SSH_USER:-aibox}" \
  AIBOX_ADMIN_KEYS_FILE="${AIBOX_ADMIN_KEYS_FILE:-}" \
  AIBOX_REMOTE_ACCESS="${AIBOX_REMOTE_ACCESS:-tailscale}" \
  AIBOX_TS_TAGS="${AIBOX_TS_TAGS:-tag:aibox}" \
    bash "$SCRIPT_DIR/05-harden-host.sh"
}

phase_storage() {
  bash "$JETSON_DIR/30-app-storage-jp6.sh"
}

phase_ollama() {
  OLLAMA_VERSION="$OLLAMA_VERSION" bash "$JETSON_DIR/20-ollama-jp6.sh"
}

phase_models() {
  bash "$SCRIPT_DIR/20-pull-models.sh" "$EMBED_MODEL" "$GEN_MODEL"
}

phase_models_verify() {
  bash "$JETSON_DIR/verify-ollama-jp6.sh" "${EMBED_MODEL%%:*}"
  bash "$JETSON_DIR/verify-ollama-generation-jp6.sh" "$GEN_MODEL"
}

phase_images() {
  SOURCE_DIR="$SOURCE_DIR" UI_BUNDLE="$UI_BUNDLE" bash "$SCRIPT_DIR/10-build-images.sh"
}

phase_deploy() {
  bash "$JETSON_DIR/40-app-deploy-jp6.sh"
}

phase_stack_verify() {
  python3 "$JETSON_DIR/verify-stack-jp6.py"
}

phase_audit() {
  AUDIT_CONTINUE_ON_FAIL="${AUDIT_CONTINUE_ON_FAIL:-0}" \
  AIBOX_HOSTNAME="$AIBOX_HOSTNAME" \
  AIBOX_SSH_USER="${AIBOX_SSH_USER:-aibox}" \
  AIBOX_REMOTE_ACCESS="${AIBOX_REMOTE_ACCESS:-tailscale}" \
  OLLAMA_VERSION="$OLLAMA_VERSION" \
  EMBED_MODEL="$EMBED_MODEL" \
  GEN_MODEL="$GEN_MODEL" \
    bash "$SCRIPT_DIR/90-acceptance-audit.sh"
  SOURCE_DIR="$SOURCE_DIR" AIBOX_HOSTNAME="$AIBOX_HOSTNAME" \
    bash "$SCRIPT_DIR/95-manifest.sh"
}

# ---------------------------------------------------------------------------
# Preflight (--check): nothing is written, nothing is installed
# ---------------------------------------------------------------------------

if [[ $mode == check ]]; then
  log "Preflight: NVMe identity"
  EXPECTED_NVME_MODEL="$NVME_MODEL" bash "$SCRIPTS_DIR/provision_jetson_nvme.sh" \
    "$NVME_DEVICE" "$NVME_SERIAL" "$NVME_SIZE_BYTES" --check || \
    die "NVMe preflight failed - check the model, serial and size in $config_file."
  log "Preflight: source tree"
  for required in adapter/Dockerfile rag_service/Dockerfile poc \
                  scripts/jetson/Dockerfile.ui-runtime; do
    [[ -e "$SOURCE_DIR/$required" ]] || die "Missing from SOURCE_DIR: $required"
  done
  [[ -r $UI_BUNDLE ]] || die "Missing UI bundle: $UI_BUNDLE"
  log "Preflight: host hardening inputs"
  [[ -n ${AIBOX_ADMIN_KEYS_FILE:-} ]] || die "AIBOX_ADMIN_KEYS_FILE missing from $config_file (administrators' SSH public keys)."
  AIBOX_ADMIN_KEYS_FILE="$AIBOX_ADMIN_KEYS_FILE" AIBOX_SSH_USER="${AIBOX_SSH_USER:-aibox}" \
    bash "$SCRIPT_DIR/05-harden-host.sh" --check
  [[ ${AIBOX_REMOTE_ACCESS:-tailscale} != tailscale || -n ${TS_AUTH_KEY:-} || -r ${TS_AUTH_KEY_FILE:-/nonexistent} ]] || \
    log "TS_AUTH_KEY / TS_AUTH_KEY_FILE is not set - the harden phase will install Tailscale but cannot join the tailnet."
  log "Preflight: platform"
  if findmnt -nro SOURCE /data >/dev/null 2>&1; then
    bash "$JETSON_DIR/10b-base-jp6.sh" --check
  else
    log "/data is not mounted yet - the platform check runs after the nvme phase."
  fi
  echo "AIBOX_PREFLIGHT_OK box=$AIBOX_HOSTNAME"
  exit 0
fi

# ---------------------------------------------------------------------------
# Phase selection and execution
# ---------------------------------------------------------------------------

declare -a selected=()
if [[ -n $only_phase ]]; then
  selected=("$only_phase")
else
  started=0
  [[ -z $from_phase ]] && started=1
  for phase in "${PHASES[@]}"; do
    [[ $started -eq 0 && $phase == "$from_phase" ]] && started=1
    [[ $started -eq 1 ]] && selected+=("$phase")
  done
fi

touch "$STATE_FILE"
for phase in "${selected[@]}"; do
  skip=0
  for skipped in "${skip_phases[@]:-}"; do
    [[ $phase == "$skipped" ]] && skip=1
  done
  if [[ $skip -eq 1 ]]; then
    log "--- PHASE $phase: skipped on request"
    continue
  fi
  if [[ $resume -eq 1 ]] && awk '{print $2}' "$STATE_FILE" | grep -Fxq "$phase"; then
    log "--- PHASE $phase: already completed, skipped (--resume)"
    continue
  fi
  log "=== PHASE $phase: start"
  "phase_${phase//-/_}"
  printf '%s %s\n' "$(date -Is)" "$phase" >> "$STATE_FILE"
  log "=== PHASE $phase: OK"
done

echo "AIBOX_INSTALL_COMPLETE box=$AIBOX_HOSTNAME phases=${#selected[@]} log=$log_file"
