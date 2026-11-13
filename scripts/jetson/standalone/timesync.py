#!/usr/bin/env python3
"""Time synchronisation of a standalone box with the customer's time source (E27 S8).

    timesync.py probe [SERVER ...]   ask the servers directly (SNTP), print offset and stratum
    timesync.py configure [--force]  point chrony or systemd-timesyncd at AIBOX_NTP_SERVERS only
    timesync.py status               client, servers, synchronised?, live offsets
    timesync.py check                status; exit 1 unless synchronised within AIBOX_TIME_MAX_OFFSET

TOTP codes are valid for 30 s (one step of tolerance either way), so the box
clock must follow the customer's internal time source. Which one that is, is
an open question to the customer: AIBOX_NTP_SERVERS (host names or addresses;
the domain controllers usually serve time). `none` = deliberately no network
time (the clock is set by hand and `check` fails as a reminder).

configure probes every server first and refuses unreachable or unsynchronised
ones (stratum 0/16, alarm) unless --force. It removes every internet pool: only
the configured servers are used; the box does not serve time itself.
AIBOX_TIME_CLIENT: auto (chrony when installed, else systemd-timesyncd), chrony,
timesyncd; unknown values mean auto.
"""
import argparse
import os
import re
import shutil
import socket
import struct
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kitlib  # noqa: E402
from kitlib import KitError, log, run  # noqa: E402

NTP_DELTA = 2208988800
CHRONY_CONF = "/etc/chrony/chrony.conf"
# Sorts after vendor drop-ins (Jetson: nv-fallback-ntp.conf sets internet pools).
TIMESYNCD_DROPIN = "/etc/systemd/timesyncd.conf.d/zz-aibox.conf"
MARK = "# Written by the ViVeSec AI Box installation kit (timesync.py)."


def settings(values=None):
    s = kitlib.load_settings() if values is None else values
    raw = (s.get("AIBOX_NTP_SERVERS") or "").replace(",", " ").split()
    servers = []
    if [r.lower() for r in raw] == ["none"]:
        servers = None
    else:
        for r in raw:
            if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9.:-]{0,252})", r) or ".." in r:
                raise KitError("AIBOX_NTP_SERVERS: bad server %r" % r)
            servers.append(r)
    client = (s.get("AIBOX_TIME_CLIENT") or "auto").strip().lower()
    note = None
    if client not in ("auto", "chrony", "timesyncd"):
        note, client = "unknown AIBOX_TIME_CLIENT %r: using auto" % client, "auto"
    try:
        max_offset = float((s.get("AIBOX_TIME_MAX_OFFSET") or "5").strip())
    except ValueError:
        raise KitError("AIBOX_TIME_MAX_OFFSET must be seconds")
    if not 0.05 <= max_offset <= 30:
        raise KitError("AIBOX_TIME_MAX_OFFSET must be 0.05-30 seconds (TOTP allows 30)")
    return {"servers": servers, "client": client, "note": note, "max_offset": max_offset}


# ---------------------------------------------------------------- SNTP

def to_ntp(t):
    t += NTP_DELTA
    return struct.pack("!II", int(t), int((t % 1) * (1 << 32)))


def from_ntp(data):
    sec, frac = struct.unpack("!II", data)
    return sec - NTP_DELTA + frac / float(1 << 32)


def sntp(server, timeout=2.0, port=123):
    """One SNTP exchange: offset (server - box), delay, stratum, leap."""
    info = socket.getaddrinfo(server, port, 0, socket.SOCK_DGRAM)[0]
    with socket.socket(info[0], socket.SOCK_DGRAM) as sock:
        sock.settimeout(timeout)
        t1 = time.time()
        sent = to_ntp(t1)
        packet = bytes([0x23]) + bytes(39) + sent
        sock.sendto(packet, info[4])
        data, _ = sock.recvfrom(512)
        t4 = time.time()
    if len(data) < 48:
        raise KitError("short answer")
    leap, mode, stratum = data[0] >> 6, data[0] & 7, data[1]
    if mode != 4 or data[24:32] != sent:
        raise KitError("not an answer to this request")
    if stratum == 0 or stratum >= 16 or leap == 3:
        raise KitError("the server is not synchronised itself (stratum %d, leap %d)" % (stratum, leap))
    t2, t3 = from_ntp(data[32:40]), from_ntp(data[40:48])
    return {"server": server, "offset": ((t2 - t1) + (t3 - t4)) / 2, "delay": (t4 - t1) - (t3 - t2),
            "stratum": stratum, "leap": leap}


def probe_all(servers, port=123):
    results = []
    for server in servers:
        try:
            r = sntp(server, port=port)
            r["ok"] = True
        except (OSError, KitError) as e:
            r = {"server": server, "ok": False, "error": str(e) or e.__class__.__name__}
        results.append(r)
    return results


def print_probe(results):
    for r in results:
        if r["ok"]:
            print("%-30s offset %+8.3f s  delay %6.3f s  stratum %d" % (r["server"], r["offset"], r["delay"],
                                                                     r["stratum"]))
        else:
            print("%-30s UNUSABLE: %s" % (r["server"], r["error"]))


# ---------------------------------------------------------------- clients

def chrony_conf(servers):
    lines = [MARK, "# Only the customer's internal time sources; the box does not serve time."]
    lines += ["server %s iburst" % s for s in servers or []]
    lines += ["driftfile /var/lib/chrony/chrony.drift",
              "makestep 1.0 3",
              "rtcsync",
              "port 0",
              "cmdport 0",
              "logdir /var/log/chrony", ""]
    return "\n".join(lines)


def timesyncd_conf(servers):
    return "%s\n[Time]\nNTP=%s\nFallbackNTP=\n" % (MARK, " ".join(servers or []))


def pick_client(cfg):
    has_chrony = bool(shutil.which("chronyd") or os.path.exists("/usr/sbin/chronyd"))
    if cfg["client"] == "chrony" and not has_chrony:
        raise KitError("chrony is not installed (offline box: install the chrony .deb of the kit)")
    if cfg["client"] == "chrony" or (cfg["client"] == "auto" and has_chrony):
        return "chrony"
    return "timesyncd"


def write_file(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if os.path.exists(path) and not os.path.exists(path + ".pre-aibox"):
        with open(path) as f:
            old = f.read()
        if MARK not in old:
            shutil.copy2(path, path + ".pre-aibox")
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.write(text)
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)


def synchronised(client):
    if client == "chrony":
        code, out, _ = run(["chronyc", "-n", "tracking"], check=False)
        fields = dict(line.split(":", 1) for line in out.splitlines() if ":" in line)
        leap = fields.get("Leap status ", fields.get("Leap status", "")).strip()
        return code == 0 and leap == "Normal"
    _, out, _ = run(["timedatectl", "show", "-p", "NTPSynchronized", "--value"], check=False)
    return out.strip() == "yes"


def cmd_configure(cfg, force=False):
    kitlib.require_root()
    if cfg["note"]:
        log("WARNING " + cfg["note"])
    servers = cfg["servers"]
    if servers is not None and not servers:
        raise KitError("AIBOX_NTP_SERVERS is not set. The customer's time source is an open question: "
                       "set it (e.g. the domain controllers), or 'none' for a hand-set clock")
    if servers:
        results = probe_all(servers)
        print_probe(results)
        if not all(r["ok"] for r in results) and not force:
            raise KitError("unusable time servers (fix the list, or --force to configure anyway)")
    client = pick_client(cfg)
    if client == "chrony":
        write_file(CHRONY_CONF, chrony_conf(servers))
        if os.path.exists(TIMESYNCD_DROPIN):
            os.remove(TIMESYNCD_DROPIN)
        run(["systemctl", "disable", "--now", "systemd-timesyncd"], check=False)
        unit = "chrony" if os.path.exists("/lib/systemd/system/chrony.service") else "chronyd"
        run(["systemctl", "enable", unit], check=False)
        run(["systemctl", "restart", unit])
    else:
        write_file(TIMESYNCD_DROPIN, timesyncd_conf(servers))
        run(["timedatectl", "set-ntp", "true" if servers else "false"])
        if servers:
            run(["systemctl", "restart", "systemd-timesyncd"])
    log("CONFIGURED %s with %s" % (client, " ".join(servers) if servers else "no time source"))
    if not servers:
        log("WARNING no network time: keep the clock right by hand (TOTP needs it within 30 s)")
        return
    deadline = time.time() + 90
    while time.time() < deadline:
        if synchronised(client):
            log("SYNCHRONISED")
            return
        time.sleep(3)
    raise KitError("%s did not synchronise within 90 s (timesync.py status)" % client)


def cmd_status(cfg, check=False):
    client = pick_client(cfg)
    servers = cfg["servers"]
    synced = synchronised(client)
    print("client       %s" % client)
    print("servers      %s" % ("none (hand-set clock)" if servers is None else " ".join(servers) or "NOT SET"))
    print("synchronised %s" % ("yes" if synced else "no"))
    print("box time     %s" % time.strftime("%Y-%m-%d %H:%M:%S %z"))
    problems = []
    if cfg["note"]:
        problems.append(cfg["note"])
    if not servers:
        problems.append("no time source configured (AIBOX_NTP_SERVERS)")
    else:
        results = probe_all(servers)
        print_probe(results)
        good = [r for r in results if r["ok"]]
        if not good:
            problems.append("no time server answers")
        elif min(abs(r["offset"]) for r in good) > cfg["max_offset"]:
            problems.append("the box clock is %.1f s off (limit %.1f s)"
                            % (min(abs(r["offset"]) for r in good), cfg["max_offset"]))
        if not synced:
            problems.append("%s is not synchronised" % client)
    for p in problems:
        print(("FAIL " if check else "WARNING ") + p)
    if check and not problems:
        print("TIME_OK")
    return 1 if check and problems else 0


def main(argv):
    p = argparse.ArgumentParser(prog="timesync.py", description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd")
    sub.add_parser("probe").add_argument("servers", nargs="*")
    sub.add_parser("configure").add_argument("--force", action="store_true")
    sub.add_parser("status")
    sub.add_parser("check")
    args = p.parse_args(argv[1:])
    if not args.cmd:
        print(__doc__)
        return 2
    cfg = settings()
    if args.cmd == "probe":
        servers = args.servers or cfg["servers"] or []
        if not servers:
            raise KitError("no servers given and AIBOX_NTP_SERVERS is not set")
        results = probe_all(servers)
        print_probe(results)
        return 0 if all(r["ok"] for r in results) else 1
    if args.cmd == "configure":
        cmd_configure(cfg, args.force)
        return 0
    return cmd_status(cfg, check=args.cmd == "check")


if __name__ == "__main__":
    sys.exit(kitlib.main_guard(main, sys.argv))
