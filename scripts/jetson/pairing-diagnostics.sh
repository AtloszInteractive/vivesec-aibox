#!/usr/bin/env bash
# Read-only pairing diagnostics for a ViVeSec AI Box.
#
# Run on the AI Box:   bash pairing-diagnostics.sh
# No sudo is required and nothing is modified. No secrets are printed.
set -u

line() { printf '\n=== %s ===\n' "$1"; }

line "IDENTITY"
printf 'hostname   : %s\n' "$(hostname)"
printf 'lan_ipv4   : %s\n' "$(hostname -I | tr ' ' '\n' | grep -E '^[0-9]+\.' | tr '\n' ' ')"
printf 'boot_time  : %s\n' "$(uptime -s)"
printf 'uptime     : %s\n' "$(uptime -p)"

line "ADAPTER VERSION (must match the fixed build)"
docker exec vivesec-adapter sha256sum /app/adapter/service.py /app/adapter/discovery.py 2>/dev/null \
  || echo 'adapter container not reachable'
cat <<'EXPECTED'
expected service.py   9851a8ff4ff6cfa83d3cc8fc870ee70014bfc96f4c5e2d01995e1f8f7a2798aa
expected discovery.py 8ccc8fbe415321b386ed1f521b48f423869f34d30c05cfed2d90539f0558d57c
EXPECTED

line "CONTAINERS"
docker ps --format '{{.Names}}\t{{.Status}}\t{{.Image}}' 2>/dev/null || echo 'docker not reachable'

line "SSDP LISTENER (UDP 1900 must be present)"
ss -lunH | grep 1900 || echo 'NO UDP 1900 LISTENER — discovery is down'

line "ADVERTISED LOCATION (must be this box's LAN IP, never 127.0.0.1)"
docker logs vivesec-adapter 2>&1 | grep -i 'Discovery' | tail -2 || true

line "SSDP ERRORS SINCE START"
docker logs vivesec-adapter 2>&1 | grep -iE 'SSDP (disabled|unavailable)' | tail -5 \
  || echo 'none'

line "LIVE M-SEARCH SELF-TEST (the box answers its own search)"
python3 - <<'PY'
import socket, time
GROUP, PORT = "239.255.255.250", 1900
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)
sock.settimeout(0.5)
sock.sendto((
    "M-SEARCH * HTTP/1.1\r\n"
    f"HOST: {GROUP}:{PORT}\r\n"
    'MAN: "ssdp:discover"\r\n'
    "MX: 2\r\n"
    "ST: urn:vivesecbox-com:device:AIBox:1\r\n\r\n"
).encode("ascii"), (GROUP, PORT))
found = 0
deadline = time.monotonic() + 5
while time.monotonic() < deadline:
    try:
        payload, address = sock.recvfrom(4096)
    except socket.timeout:
        continue
    text = payload.decode("utf-8", "replace")
    if "ViVeSec" not in text:
        continue
    found += 1
    for item in text.split("\r\n"):
        if item.upper().startswith(("LOCATION", "USN", "ST:")):
            print(f"  {address[0]} | {item}")
print("replies:", found)
PY

line "PORT EXPOSURE (80 and 8080 public, 8090 and 11434 loopback only)"
ss -lntH | awk '{print $4}' | grep -E ':(80|443|8080|8090|11434)$' | sort -u

line "SERVICE HEALTH"
printf 'adapter :80   http=%s\n' "$(curl -sS -o /dev/null -w '%{http_code}' --max-time 8 http://127.0.0.1:80/api/v1/status)"
printf 'ui      :8080 http=%s\n' "$(curl -sS -o /dev/null -w '%{http_code}' --max-time 8 http://127.0.0.1:8080/)"
printf 'rag     :8090 http=%s\n' "$(curl -sS -o /dev/null -w '%{http_code}' --max-time 8 http://127.0.0.1:8090/health)"

line "PAIRING STATE"
docker exec vivesec-adapter sh -c '
  test -s /data/pki/device_uuid && echo "device_uuid : present (stable USN)" || echo "device_uuid : MISSING"
  test -e /data/pki/initialized && echo "pairing     : PAIRED" || echo "pairing     : pending (port 443 stays closed until commit)"
' 2>/dev/null || echo 'adapter container not reachable'

line "DID THE VIVESECBOX EVER REACH US? (last 2 hours)"
docker logs --since 2h vivesec-adapter 2>&1 \
  | grep -E '"(GET|POST) /api/v1/(init|status)' | tail -15 \
  || echo 'no init/status requests logged'

cat <<'HINT'

=== HOW TO READ THIS ===
1. No UDP 1900 listener            -> discovery is down on the AI Box.
2. LOCATION shows 127.0.0.1        -> old adapter build, redeploy the fixed one.
3. Self-test replies: 0            -> the responder is not answering.
4. Self-test fine, but no init/status requests after pressing Initialize
                                   -> the M-SEARCH never reached us, or the reply
                                      never reached the ViVeSecBox: check that both
                                      devices sit on the SAME L2 segment (one switch,
                                      no router, no Wi-Fi isolation, no VLAN border).

To capture the actual exchange, run this BEFORE pressing Initialize:
  sudo tcpdump -n -i any -A 'udp port 1900 or tcp port 80'
HINT
