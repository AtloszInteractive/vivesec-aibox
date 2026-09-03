#!/usr/bin/env python3
"""Passive SSDP sniffer: joins the multicast group alongside the adapter's own
responder (SO_REUSEADDR lets both receive) and prints who searches for what."""
import socket
import struct
import sys
import time

GROUP = "239.255.255.250"
PORT = 1900

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
sock.bind(("", PORT))
mreq = struct.pack("4sl", socket.inet_aton(GROUP), socket.INADDR_ANY)
sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
sock.settimeout(1.0)

print("[sniff] listening on %s:%d — press the Initialize button now" % (GROUP, PORT))
sys.stdout.flush()
deadline = time.time() + int(sys.argv[1] if len(sys.argv) > 1 else 120)
seen = 0
while time.time() < deadline:
    try:
        data, addr = sock.recvfrom(4096)
    except socket.timeout:
        continue
    text = data.decode("utf-8", "replace")
    first = text.split("\r\n", 1)[0]
    st = ""
    for line in text.split("\r\n"):
        low = line.lower()
        if low.startswith("st:") or low.startswith("nt:"):
            st = line.strip()
    seen += 1
    print("[%s] from %s:%s | %s | %s" % (time.strftime("%H:%M:%S"), addr[0], addr[1], first, st))
    sys.stdout.flush()
print("[sniff] done, %d datagram(s)" % seen)
