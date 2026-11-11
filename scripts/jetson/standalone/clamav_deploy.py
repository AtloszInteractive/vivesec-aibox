#!/usr/bin/env python3
"""Virus scanner for uploads on a standalone box (E27 S5).

    python3 clamav_deploy.py deploy    (re)start vivesec-clamav, wait for clamd, scan EICAR
    python3 clamav_deploy.py status    container, clamd answer, signature database version
    python3 clamav_deploy.py test      scan the EICAR test string (must be found)

clamd runs in its own container (`clamav/clamav-debian`, which has an arm64
build and ships a signature database), published on 127.0.0.1 only: the
adapter streams each upload to it (`INSTREAM`, ADAPTER_CLAMD) while the upload
waits in quarantine. Nothing else on the network can reach it.

The limits are set so that every upload the box accepts is scanned whole:
StreamMaxLength, MaxFileSize and MaxScanSize are at least AIBOX_CLAMAV_MAX_MB,
and AlertExceedsMax turns anything beyond them into a finding instead of an
unscanned "OK" (fail closed).

On an offline box freshclam stays off (AIBOX_CLAMAV_FRESHCLAM=off, the default)
and the database is the one in the image; updates come with the image (S8:
from USB). Only docker is needed (no sudo). A failed start brings the previous
container back.

Settings: AIBOX_CLAMAV_IMAGE (clamav/clamav-debian:1.4), AIBOX_CLAMAV_PORT
(3310), AIBOX_CLAMAV_MEMORY (3g), AIBOX_CLAMAV_MAX_MB (300),
AIBOX_CLAMAV_FRESHCLAM (off | on).
"""
import json
import os
import shlex
import socket
import struct
import subprocess
import sys
import time

CONTAINER = "vivesec-clamav"
# The standard antivirus test string, split so this file is not flagged itself.
EICAR = (b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$" + b"EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*")


def settings(env=None):
    env = env if env is not None else os.environ
    port = (env.get("AIBOX_CLAMAV_PORT") or "3310").strip()
    if not port.isdigit() or not 0 < int(port) < 65536:
        raise SystemExit("AIBOX_CLAMAV_PORT must be a port number")
    max_mb = (env.get("AIBOX_CLAMAV_MAX_MB") or "300").strip()
    if not max_mb.isdigit() or not 1 <= int(max_mb) <= 4000:
        raise SystemExit("AIBOX_CLAMAV_MAX_MB must be a number of megabytes (1-4000)")
    memory = (env.get("AIBOX_CLAMAV_MEMORY") or "3g").strip()
    if not memory[:-1].isdigit() or memory[-1].lower() not in "mg":
        raise SystemExit("AIBOX_CLAMAV_MEMORY must look like 3g or 2500m")
    freshclam = (env.get("AIBOX_CLAMAV_FRESHCLAM") or "off").strip().lower()
    if freshclam not in ("on", "off"):
        raise SystemExit("AIBOX_CLAMAV_FRESHCLAM must be on or off")
    return {"image": (env.get("AIBOX_CLAMAV_IMAGE") or "clamav/clamav-debian:1.4").strip(),
            "port": int(port), "max_mb": int(max_mb), "memory": memory,
            "freshclam": freshclam == "on"}


def run_args(config):
    """The `docker run` arguments of the clamd container (tested)."""
    limit = "%dM" % config["max_mb"]
    scan = "%dM" % max(config["max_mb"] * 4, 1024)
    args = ["docker", "run", "-d", "--name", CONTAINER, "--restart", "unless-stopped",
            "--network", "bridge", "-p", "127.0.0.1:%d:3310" % config["port"],
            "--cap-drop", "ALL", "--cap-add", "CHOWN", "--cap-add", "SETUID",
            "--cap-add", "SETGID", "--cap-add", "DAC_OVERRIDE", "--cap-add", "FOWNER",
            "--security-opt", "no-new-privileges", "--memory", config["memory"],
            "--pids-limit", "256",
            "-e", "CLAMD_CONF_StreamMaxLength=" + limit,
            "-e", "CLAMD_CONF_MaxFileSize=" + limit,
            "-e", "CLAMD_CONF_MaxScanSize=" + scan,
            "-e", "CLAMD_CONF_AlertExceedsMax=yes",
            "-e", "CLAMD_CONF_ConcurrentDatabaseReload=no",
            "-e", "CLAMAV_NO_MILTERD=true"]
    if not config["freshclam"]:
        args += ["-e", "CLAMAV_NO_FRESHCLAMD=true"]
    return args + [config["image"]]


def command(*args):
    result = subprocess.run(args, text=True, capture_output=True)
    if result.returncode:
        raise RuntimeError("Command failed: %s\n%s" % (shlex.join(args[:3]), result.stderr[-1800:]))
    return result.stdout.strip()


def inspect(name):
    try:
        return json.loads(command("docker", "inspect", name))[0]
    except RuntimeError:
        return None


def clamd_call(port, payload, timeout=30):
    with socket.create_connection(("127.0.0.1", port), timeout=timeout) as sock:
        sock.sendall(payload)
        data = b""
        while b"\0" not in data:
            part = sock.recv(4096)
            if not part:
                break
            data += part
    return data.split(b"\0", 1)[0].decode("utf-8", "replace").strip()


def scan(port, data):
    return clamd_call(port, b"zINSTREAM\0" + struct.pack(">I", len(data)) + data
                      + struct.pack(">I", 0), timeout=60)


def ready(port, seconds=300):
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            if clamd_call(port, b"zPING\0", timeout=5) == "PONG":
                return True
        except OSError:
            pass
        time.sleep(3)
    return False


def check_eicar(port):
    reply = scan(port, EICAR)
    if not reply.endswith("FOUND"):
        raise RuntimeError("clamd did not find the EICAR test string: %r" % reply)
    clean = scan(port, b"plain text, nothing to see")
    if not clean.endswith("OK"):
        raise RuntimeError("clamd flagged a clean text: %r" % clean)
    return reply


def deploy():
    config = settings()
    stamp = time.strftime("%Y%m%d-%H%M%S")
    if subprocess.run(["docker", "image", "inspect", config["image"]],
                      capture_output=True).returncode:
        print("PULL " + config["image"], flush=True)
        command("docker", "pull", "-q", config["image"])
    previous = inspect(CONTAINER)
    parked = None
    if previous:
        parked = CONTAINER + "-prev-" + stamp
        command("docker", "rename", CONTAINER, parked)
        command("docker", "stop", "-t", "20", parked)
    try:
        command(*run_args(config))
        print("WAITING for clamd to load its database ...", flush=True)
        if not ready(config["port"]):
            raise RuntimeError("clamd did not answer PING within 5 minutes")
        print("EICAR " + check_eicar(config["port"]), flush=True)
    except BaseException:
        logs = subprocess.run(["docker", "logs", "--tail", "30", CONTAINER],
                              text=True, capture_output=True)
        print((logs.stdout + logs.stderr)[-3000:], flush=True)
        subprocess.run(["docker", "rm", "-f", CONTAINER], capture_output=True)
        if parked:
            command("docker", "rename", parked, CONTAINER)
            command("docker", "start", CONTAINER)
            print("ROLLED_BACK: the previous vivesec-clamav runs again", flush=True)
        raise
    print("CLAMAV_DEPLOYED %s on 127.0.0.1:%d (%s)" % (
        config["image"], config["port"], clamd_call(config["port"], b"zVERSION\0")), flush=True)
    if parked:
        print("previous container kept stopped: %s" % parked, flush=True)


def status():
    config = settings()
    container = inspect(CONTAINER)
    out = {"container": None, "clamd": None, "version": None}
    if container:
        out["container"] = {"image": container["Config"]["Image"],
                            "state": container["State"]["Status"],
                            "ports": container["HostConfig"].get("PortBindings")}
    try:
        out["clamd"] = clamd_call(config["port"], b"zPING\0", timeout=5)
        out["version"] = clamd_call(config["port"], b"zVERSION\0", timeout=5)
    except OSError as e:
        out["clamd"] = "unreachable: %s" % e
    print(json.dumps(out, indent=2))


def main(argv):
    if len(argv) != 2 or argv[1] not in ("deploy", "status", "test"):
        print(__doc__)
        return 2
    if argv[1] == "deploy":
        deploy()
    elif argv[1] == "status":
        status()
    else:
        print(check_eicar(settings()["port"]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
