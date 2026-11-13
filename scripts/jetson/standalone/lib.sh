#!/usr/bin/env bash
# Shared helpers of the standalone-mode scripts (E27). Sourced, never run.

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

split_list() { tr ', ' '\n\n' <<<"${1:-}" | sed '/^$/d'; }

valid_dns() {
  [[ ${#1} -le 253 ]] &&
    [[ $1 =~ ^[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*$ ]]
}

valid_ipv4() {
  local octet
  [[ $1 =~ ^([0-9]{1,3})\.([0-9]{1,3})\.([0-9]{1,3})\.([0-9]{1,3})$ ]] || return 1
  for octet in "${BASH_REMATCH[@]:1}"; do
    [[ $octet =~ ^(0|[1-9][0-9]*)$ ]] && ((octet <= 255)) || return 1
  done
}

# The box's names from AIBOX_TLS_DNS (default: the host name) and AIBOX_TLS_IPS,
# validated and lower-cased into dns_names / ip_names.
read_box_names() {
  dns_names=()
  ip_names=()
  local item
  while IFS= read -r item; do
    item=${item,,}
    valid_dns "$item" || die "invalid host name in AIBOX_TLS_DNS: $item"
    dns_names+=("$item")
  done < <(split_list "${AIBOX_TLS_DNS:-$(hostname -s 2>/dev/null || hostname)}")
  ((${#dns_names[@]})) || die "AIBOX_TLS_DNS is empty: at least one host name is needed"
  while IFS= read -r item; do
    valid_ipv4 "$item" || die "invalid IPv4 address in AIBOX_TLS_IPS: $item"
    ip_names+=("$item")
  done < <(split_list "${AIBOX_TLS_IPS:-}")
}
