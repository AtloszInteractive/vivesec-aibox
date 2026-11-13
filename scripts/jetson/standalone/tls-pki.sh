#!/usr/bin/env bash
# ViVeSec AI Box - standalone mode (E27 S2): the box's own certificate authority.
#
# The box has no internal CA to rely on, so it creates its own at installation.
# The CA certificate is installed on the client machines by hand; the CA key
# never leaves the box. The CA carries critical name constraints: it can only
# vouch for the box's own names and addresses, so even a stolen CA key cannot
# impersonate any other site to the clients that trust it.
#
#   bash tls-pki.sh init           # new CA + server certificate (refuses if a CA exists)
#   bash tls-pki.sh renew          # new server key + certificate from the existing CA
#   bash tls-pki.sh show           # CA fingerprint, names and expiry dates
#   bash tls-pki.sh check          # exit 1 when renewal is due or the files do not match
#   bash tls-pki.sh init --new-ca  # replace the CA: every client must install the new one
#
# Parameters (environment):
#   AIBOX_TLS_DIR            output directory                      (default /data/tls)
#   AIBOX_TLS_DNS            host names, comma/space separated     (default: hostname)
#   AIBOX_TLS_IPS            IPv4 addresses, comma/space separated (default: none)
#   AIBOX_TLS_CA_PERMIT_IPS  IPv4 subnets (CIDR) the CA may certify besides the
#                            configured addresses, so an address change needs
#                            no new CA on the clients. Fixed when the CA is
#                            created. Default: the /24 of each AIBOX_TLS_IPS
#                            address; "none": the configured addresses only.
#   AIBOX_TLS_RENEW_DAYS     `check` fails when the server certificate expires
#                            within this many days                 (default 30)
#
# Layout under AIBOX_TLS_DIR:
#   ca/ca.key  ca/ca.crt           0700 directory, key 0600 - never copied anywhere
#   server/server.key  server.crt  mounted read-only into the HTTPS front
#   public/aibox-ca.cer            DER copy of the CA certificate for the clients;
#                                  its SHA-256 file hash is the CA fingerprint
#
# Works with OpenSSL 1.1.1 and 3.x.
set -euo pipefail

CA_DAYS=3650
SERVER_DAYS=825
CA_MIN_DAYS=180

tls_dir="${AIBOX_TLS_DIR:-/data/tls}"
renew_days="${AIBOX_TLS_RENEW_DAYS:-30}"
ca_dir="$tls_dir/ca"
server_dir="$tls_dir/server"
public_dir="$tls_dir/public"

work=""
trap 'if [[ -n $work ]]; then rm -rf "$work"; fi' EXIT

# shellcheck source=lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

log() { printf '[tls-pki] %s\n' "$*"; }

# CIDR -> "address/netmask", the form OpenSSL expects in name constraints.
cidr_to_constraint() {
  local address=${1%/*} bits=${1#*/} mask="" i part
  [[ $1 == */* ]] || bits=32
  valid_ipv4 "$address" || return 1
  [[ $bits =~ ^[0-9]{1,2}$ ]] && ((bits <= 32)) || return 1
  for i in 0 1 2 3; do
    if ((bits >= 8)); then part=255; bits=$((bits - 8))
    else part=$(((256 - (1 << (8 - bits))) % 256)); bits=0
    fi
    mask+="${mask:+.}$part"
  done
  printf '%s/%s\n' "$address" "$mask"
}

read_names() {
  local item constraint ip
  read_box_names
  ca_permit=()
  case ${AIBOX_TLS_CA_PERMIT_IPS:-} in
    none) ;;
    "")
      # The box's address may change (DHCP, re-cabling): by default the CA may
      # certify its /24, so a new address needs only `renew`, not a new CA on
      # every client.
      for ip in "${ip_names[@]}"; do
        constraint=$(cidr_to_constraint "${ip%.*}.0/24")
        [[ " ${ca_permit[*]} " == *" $constraint "* ]] || ca_permit+=("$constraint")
      done ;;
    *)
      while IFS= read -r item; do
        constraint=$(cidr_to_constraint "$item") || die "invalid subnet in AIBOX_TLS_CA_PERMIT_IPS: $item"
        ca_permit+=("$constraint")
      done < <(split_list "$AIBOX_TLS_CA_PERMIT_IPS") ;;
  esac
}

subject_alt_names() {
  local out="" name
  for name in "${dns_names[@]}"; do out+="${out:+,}DNS:$name"; done
  for name in "${ip_names[@]}"; do out+="${out:+,}IP:$name"; done
  printf '%s' "$out"
}

name_constraints() {
  local out="critical" name
  for name in "${dns_names[@]}"; do out+=",permitted;DNS:$name"; done
  for name in "${ip_names[@]}"; do out+=",permitted;IP:$name/255.255.255.255"; done
  for name in "${ca_permit[@]}"; do out+=",permitted;IP:$name"; done
  # Without any IP subtree the CA could certify every address; 0.0.0.0/32
  # leaves it none (and an IPv6 address never matches an IPv4 subtree).
  [[ $out == *"permitted;IP:"* ]] || out+=",permitted;IP:0.0.0.0/255.255.255.255"
  printf '%s' "$out"
}

random_serial() { printf '0x%s' "$(openssl rand -hex 16)"; }

fingerprint() {  # SHA-256 of the DER certificate, colon separated
  openssl x509 -in "$1" -noout -fingerprint -sha256 | sed 's/^.*=//'
}

extension_values() {  # the GeneralNames of one extension, one per line
  openssl x509 -in "$1" -noout -ext "$2" | sed '1d' | tr ',' '\n' |
    sed -e 's/^ *//' -e 's/ *$//' -e '/^$/d' -e '/^Permitted:$/d' -e 's/^IP Address:/IP:/'
}

write_ca_config() {
  cat >"$1" <<EOF
[req]
distinguished_name = dn
prompt = no
[dn]
O = ViVeSec AI Box
CN = ViVeSec AI Box CA ${dns_names[0]} $(date -u +%Y-%m-%d)
[v3_ca]
basicConstraints = critical, CA:TRUE, pathlen:0
keyUsage = critical, keyCertSign, cRLSign
subjectKeyIdentifier = hash
nameConstraints = $(name_constraints)
EOF
}

write_server_config() {
  cat >"$1" <<EOF
[req]
distinguished_name = dn
prompt = no
[dn]
O = ViVeSec AI Box
CN = ${dns_names[0]}
[v3_server]
basicConstraints = critical, CA:FALSE
keyUsage = critical, digitalSignature
extendedKeyUsage = serverAuth
subjectKeyIdentifier = hash
authorityKeyIdentifier = keyid
subjectAltName = $(subject_alt_names)
EOF
}

new_key() { (umask 077 && openssl genpkey -algorithm EC -pkeyopt ec_paramgen_curve:P-256 -out "$1" 2>/dev/null); }

make_dir() { mkdir -p "$2" && chmod "$1" "$2"; }

# The key is written by a umask-077 subshell, so it is never readable by others
# even for a moment.
put_file() { (umask 077 && cp "$2" "$3") && chmod "$1" "$3"; }

issue_ca() {
  local work=$1
  write_ca_config "$work/ca.cnf"
  new_key "$work/ca.key"
  openssl req -x509 -new -sha256 -days "$CA_DAYS" -key "$work/ca.key" -set_serial "$(random_serial)" \
    -config "$work/ca.cnf" -extensions v3_ca -out "$work/ca.crt"
}

issue_server() {
  local work=$1 ca_crt=$2 ca_key=$3
  write_server_config "$work/server.cnf"
  new_key "$work/server.key"
  openssl req -new -sha256 -key "$work/server.key" -config "$work/server.cnf" -out "$work/server.csr"
  openssl x509 -req -sha256 -days "$SERVER_DAYS" -in "$work/server.csr" \
    -CA "$ca_crt" -CAkey "$ca_key" -set_serial "$(random_serial)" \
    -extfile "$work/server.cnf" -extensions v3_server -out "$work/server.crt" 2>/dev/null
  # A name outside the CA's constraints fails here, before anything is replaced.
  openssl verify -CAfile "$ca_crt" -purpose sslserver "$work/server.crt" >/dev/null 2>&1 ||
    die "the new server certificate does not verify against the CA (a name outside its constraints? the CA permits: $(extension_values "$ca_crt" nameConstraints | paste -sd' ' -))"
}

install_server() {
  local work=$1 name
  make_dir 0700 "$server_dir"
  for name in server.key server.crt; do
    if [[ -f $server_dir/$name ]]; then cp -p "$server_dir/$name" "$server_dir/$name.prev"; fi
  done
  put_file 0600 "$work/server.key" "$server_dir/server.key.new"
  put_file 0644 "$work/server.crt" "$server_dir/server.crt.new"
  mv -f "$server_dir/server.key.new" "$server_dir/server.key"
  mv -f "$server_dir/server.crt.new" "$server_dir/server.crt"
}

publish_ca() {
  make_dir 0755 "$public_dir"
  openssl x509 -in "$ca_dir/ca.crt" -outform DER -out "$public_dir/aibox-ca.cer.new"
  chmod 0644 "$public_dir/aibox-ca.cer.new"
  mv -f "$public_dir/aibox-ca.cer.new" "$public_dir/aibox-ca.cer"
}

cmd_init() {
  local new_ca=${1:-} stamp
  read_names
  if [[ -e $ca_dir/ca.key || -e $ca_dir/ca.crt ]] && [[ $new_ca != --new-ca ]]; then
    die "a CA already exists in $ca_dir; use 'renew' for a new server certificate, or 'init --new-ca' to replace the CA (every client must then install the new one)"
  fi
  work=$(mktemp -d)
  issue_ca "$work"
  issue_server "$work" "$work/ca.crt" "$work/ca.key"
  make_dir 0700 "$ca_dir"
  if [[ -e $ca_dir/ca.crt ]]; then
    stamp=$(date -u +%Y%m%d-%H%M%S)
    mv -f "$ca_dir/ca.crt" "$ca_dir/ca.crt.replaced-$stamp"
    if [[ -e $ca_dir/ca.key ]]; then mv -f "$ca_dir/ca.key" "$ca_dir/ca.key.replaced-$stamp"; fi
  fi
  put_file 0600 "$work/ca.key" "$ca_dir/ca.key"
  put_file 0644 "$work/ca.crt" "$ca_dir/ca.crt"
  install_server "$work"
  publish_ca
  log "CA and server certificate created in $tls_dir"
  cmd_show
}

cmd_renew() {
  read_names
  [[ -f $ca_dir/ca.key && -f $ca_dir/ca.crt ]] || die "no CA in $ca_dir; run 'init' first"
  work=$(mktemp -d)
  issue_server "$work" "$ca_dir/ca.crt" "$ca_dir/ca.key"
  install_server "$work"
  publish_ca
  log "server certificate renewed; reload the HTTPS front: bash https-front.sh deploy"
  cmd_show
}

days_left() {
  local end
  end=$(openssl x509 -in "$1" -noout -enddate | sed 's/^notAfter=//')
  echo $((($(date -u -d "$end" +%s) - $(date -u +%s)) / 86400))
}

expiry() {
  printf '%s (%s days)' "$(openssl x509 -in "$1" -noout -enddate | sed 's/^notAfter=//')" "$(days_left "$1")"
}

cmd_show() {
  local fp
  [[ -f $ca_dir/ca.crt ]] || die "no CA in $ca_dir"
  fp=$(fingerprint "$ca_dir/ca.crt")
  echo "CA certificate      : $ca_dir/ca.crt"
  echo "  subject           : $(openssl x509 -in "$ca_dir/ca.crt" -noout -subject | sed 's/^subject= *//')"
  echo "  expires           : $(expiry "$ca_dir/ca.crt")"
  echo "  permitted names   : $(extension_values "$ca_dir/ca.crt" nameConstraints | paste -sd' ' -)"
  echo "  SHA-256 (Firefox) : $fp"
  echo "  SHA-256 (Windows) : ${fp//:/}"
  if [[ -f $server_dir/server.crt ]]; then
    echo "Server certificate  : $server_dir/server.crt"
    echo "  names             : $(extension_values "$server_dir/server.crt" subjectAltName | paste -sd' ' -)"
    echo "  expires           : $(expiry "$server_dir/server.crt")"
  fi
  echo "Client download     : http://<box>/aibox-ca.cer - compare its SHA-256 with the value above"
}

cmd_check() {
  local problems=0 left want have file
  read_names
  for file in "$ca_dir/ca.crt" "$ca_dir/ca.key" "$server_dir/server.crt" "$server_dir/server.key" \
              "$public_dir/aibox-ca.cer"; do
    [[ -f $file ]] || { echo "FAIL missing $file"; problems=1; }
  done
  ((problems == 0)) || return 1
  if ! openssl verify -CAfile "$ca_dir/ca.crt" -purpose sslserver "$server_dir/server.crt" >/dev/null 2>&1; then
    echo "FAIL server certificate does not verify against the CA"; problems=1
  fi
  if [[ $(openssl pkey -in "$server_dir/server.key" -pubout 2>/dev/null) != \
        "$(openssl x509 -in "$server_dir/server.crt" -noout -pubkey)" ]]; then
    echo "FAIL server key does not match the server certificate"; problems=1
  fi
  if [[ $(openssl x509 -in "$ca_dir/ca.crt" -outform DER | openssl dgst -sha256 -r | cut -d' ' -f1) != \
        "$(openssl dgst -sha256 -r "$public_dir/aibox-ca.cer" | cut -d' ' -f1)" ]]; then
    echo "FAIL the published aibox-ca.cer is not the current CA"; problems=1
  fi
  want=$(subject_alt_names | tr ',' '\n' | sort | paste -sd' ' -)
  have=$(extension_values "$server_dir/server.crt" subjectAltName | sort | paste -sd' ' -)
  if [[ $want != "$have" ]]; then
    echo "FAIL server certificate names ($have) differ from the configuration ($want); run renew"; problems=1
  fi
  left=$(days_left "$server_dir/server.crt")
  if ((left <= renew_days)); then
    echo "FAIL server certificate expires in $left days; run renew"; problems=1
  fi
  left=$(days_left "$ca_dir/ca.crt")
  if ((left <= CA_MIN_DAYS)); then
    echo "FAIL CA expires in $left days; plan a new CA (init --new-ca)"; problems=1
  fi
  ((problems == 0)) || return 1
  echo "OK tls: CA and server certificate valid, server certificate expires in $(days_left "$server_dir/server.crt") days"
}

command -v openssl >/dev/null || die "openssl is required"
case ${1:-} in
  init) cmd_init "${2:-}" ;;
  renew) cmd_renew ;;
  show) cmd_show ;;
  check) cmd_check ;;
  *) sed -n '2,34p' "$0"; exit 2 ;;
esac
