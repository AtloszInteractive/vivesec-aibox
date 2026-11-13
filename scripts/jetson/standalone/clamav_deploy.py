#!/usr/bin/env python3
"""Virus scanner for uploads on a standalone box (E27 S5, offline updates S8).

    python3 clamav_deploy.py deploy             (re)start vivesec-clamav, wait for clamd, scan EICAR
    python3 clamav_deploy.py status             container, clamd answer, signature database version
    python3 clamav_deploy.py test               scan the EICAR test string (must be found)
    python3 clamav_deploy.py fetch-defs DIR     connected machine: download signed .cvd files into DIR
    python3 clamav_deploy.py update-defs DIR    offline box: install the .cvd files of DIR (e.g. a USB stick)

clamd runs in its own container (`clamav/clamav-debian`, which has an arm64
build and ships a signature database), published on 127.0.0.1 only: the
adapter streams each upload to it (`INSTREAM`, ADAPTER_CLAMD) while the upload
waits in quarantine. Nothing else on the network can reach it. clamd writes
each stream to a temporary file before scanning it; /tmp of the container is a
tmpfs, so upload contents never reach the plaintext docker disk.

The limits are set so that every upload the box accepts is scanned whole:
StreamMaxLength, MaxFileSize and MaxScanSize are at least AIBOX_CLAMAV_MAX_MB,
and AlertExceedsMax turns anything beyond them into a finding instead of an
unscanned "OK" (fail closed).

The signature database lives outside the container (AIBOX_CLAMAV_DB_DIR,
seeded from the image on the first deploy), so it survives a redeploy and can
be updated offline: `fetch-defs` runs freshclam on a connected machine into an
empty directory (which yields whole, signed .cvd files); the directory travels
on a USB stick; `update-defs` checks the ClamAV signature of every file
(sigtool, in a throwaway container without network), refuses an older version
than the installed one, swaps the files, reloads clamd, checks the new version
and EICAR, and puts the previous files back if anything fails. On an offline
box freshclam stays off (AIBOX_CLAMAV_FRESHCLAM=off, the default). Only docker
is needed (no sudo). A failed start brings the previous container back.

Settings: AIBOX_CLAMAV_IMAGE (clamav/clamav-debian:1.4), AIBOX_CLAMAV_PORT
(3310), AIBOX_CLAMAV_MEMORY (3g), AIBOX_CLAMAV_MAX_MB (300),
AIBOX_CLAMAV_FRESHCLAM (off | on), AIBOX_CLAMAV_DB_DIR (/var/lib/aibox/clamav).
"""
import hashlib
import json
import os
import re
import shlex
import socket
import struct
import subprocess
import sys
import time

CONTAINER = "vivesec-clamav"
DATABASES = ("main", "daily", "bytecode")
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
    db_dir = (env.get("AIBOX_CLAMAV_DB_DIR") or "/var/lib/aibox/clamav").strip().rstrip("/")
    if not db_dir.startswith("/") or ".." in db_dir.split("/") or db_dir.count("/") < 2:
        raise SystemExit("AIBOX_CLAMAV_DB_DIR must be an absolute path below a top-level directory")
    return {"image": (env.get("AIBOX_CLAMAV_IMAGE") or "clamav/clamav-debian:1.4").strip(),
            "port": int(port), "max_mb": int(max_mb), "memory": memory,
            "freshclam": freshclam == "on", "db_dir": db_dir}


def run_args(config):
    """The `docker run` arguments of the clamd container (tested)."""
    limit = "%dM" % config["max_mb"]
    scan = "%dM" % max(config["max_mb"] * 4, 1024)
    tmp = "%dm" % max(config["max_mb"] * 2, 512)
    args = ["docker", "run", "-d", "--name", CONTAINER, "--restart", "unless-stopped",
            "--network", "bridge", "-p", "127.0.0.1:%d:3310" % config["port"],
            "--cap-drop", "ALL", "--cap-add", "CHOWN", "--cap-add", "SETUID",
            "--cap-add", "SETGID", "--cap-add", "DAC_OVERRIDE", "--cap-add", "FOWNER",
            "--security-opt", "no-new-privileges", "--memory", config["memory"],
            "--pids-limit", "256",
            "--tmpfs", "/tmp:rw,nosuid,nodev,mode=1777,size=" + tmp,
            "-v", config["db_dir"] + ":/var/lib/clamav",
            "-e", "CLAMD_CONF_TemporaryDirectory=/tmp",
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


# ---------------------------------------------------------------- signature database

def host_dir(path, what):
    path = os.path.abspath(path)
    if any(c in path for c in ":,\n") or not os.path.isdir(path):
        raise SystemExit("%s must be an existing directory without ':' or ',' in its path: %s" % (what, path))
    return path


def helper(config, script, mounts, args=(), network="none"):
    """`sh -c script` in a throwaway container of the clamav image."""
    cmd = ["docker", "run", "--rm", "--network", network, "--entrypoint", "sh"]
    for mount in mounts:
        cmd += ["-v", mount]
    return command(*(cmd + [config["image"], "-c", script, "helper"] + list(args)))


def seed_db(config):
    """First deploy: copy the image's database into AIBOX_CLAMAV_DB_DIR."""
    script = ('[ -e /db/main.cvd ] && exit 0; cp -p /var/lib/clamav/*.cvd /db/; '
              'cp -p /var/lib/clamav/*.cld /db/ 2>/dev/null || true; '
              'chown -R clamav:clamav /db; chmod 755 /db; echo SEEDED')
    out = helper(config, script, [config["db_dir"] + ":/db"])
    if out:
        print("%s %s from the image" % (out, config["db_dir"]), flush=True)


def parse_sigtool(text):
    """{file: {"version", "build", "signed"}} from `sigtool --info` sections."""
    out, current = {}, None
    for line in text.splitlines():
        if line.startswith("== "):
            current = line[3:].strip()
            out[current] = {"version": None, "build": None, "signed": False}
        elif current:
            key, _, value = line.partition(":")
            if key.strip() == "Version" and value.strip().isdigit():
                out[current]["version"] = int(value.strip())
            elif key.strip() == "Build time":
                out[current]["build"] = value.strip()
            elif line.strip() == "Verification OK.":
                out[current]["signed"] = True
    return out


def sig_info(config, directory, names):
    if not names:
        return {}
    script = 'for f in "$@"; do echo "== $f"; sigtool --info "/d/$f" 2>&1 || true; done'
    return parse_sigtool(helper(config, script, [directory + ":/d:ro"], names))


def installed(config):
    """Database files in AIBOX_CLAMAV_DB_DIR (base -> file name)."""
    out = {}
    for base in DATABASES:
        for ext in (".cld", ".cvd"):
            if os.path.exists(os.path.join(config["db_dir"], base + ext)):
                out[base] = base + ext
    return out


def plan_update(new, current, allow_older=False):
    """Files to install: signed, newer than the installed ones (tested)."""
    unsigned = sorted(name for name, info in new.items() if not info["signed"] or not info["version"])
    if unsigned:
        raise SystemExit("signature check failed, nothing installed: %s" % ", ".join(unsigned))
    plan = []
    for name, info in sorted(new.items()):
        old = current.get(name[:-4])
        if old and old["version"]:
            if info["version"] < old["version"] and not allow_older:
                raise SystemExit("%s is version %d, older than the installed %d (--allow-older)"
                                 % (name, info["version"], old["version"]))
            if info["version"] == old["version"]:
                continue
        plan.append(name)
    return plan


SWAP = r'''set -e
rm -rf /db/.prev; mkdir /db/.prev
for f in "$@"; do b=${f%.cvd}
  for o in /db/$b.cvd /db/$b.cld; do [ -e "$o" ] && cp -p "$o" /db/.prev/; done
  cp "/in/$f" "/db/.$f.new"
done
for f in "$@"; do b=${f%.cvd}; rm -f "/db/$b.cld"; mv "/db/.$f.new" "/db/$f"; done
chown -R clamav:clamav /db'''

UNDO = r'''for f in "$@"; do b=${f%.cvd}; rm -f "/db/$b.cvd" "/db/$b.cld"; done
cp -p /db/.prev/* /db/ 2>/dev/null || true
chown -R clamav:clamav /db'''


def daily_version(port):
    try:
        parts = clamd_call(port, b"zVERSION\0", timeout=10).split("/")
        return int(parts[1]) if len(parts) > 2 and parts[1].isdigit() else None
    except OSError:
        return None


def reload_and_check(config, expect_daily):
    port = config["port"]
    if clamd_call(port, b"zRELOAD\0", timeout=30) != "RELOADING":
        raise RuntimeError("clamd refused to reload")
    deadline = time.time() + 300
    while time.time() < deadline:
        time.sleep(3)
        version = daily_version(port)
        if version is not None and (expect_daily is None or version == expect_daily):
            return check_eicar(port)
    raise RuntimeError("clamd did not load daily version %s within 5 minutes" % expect_daily)


def update_defs(source, allow_older=False):
    config = settings()
    source = host_dir(source, "the definitions directory")
    names = [base + ".cvd" for base in DATABASES if os.path.isfile(os.path.join(source, base + ".cvd"))]
    ignored = sorted(n for n in os.listdir(source)
                     if re.search(r"\.(cld|cud|cvd|cdiff|hdb|ndb|ldb)$", n) and n not in names)
    if ignored:
        print("IGNORED (only signed main/daily/bytecode .cvd files are installed): %s" % " ".join(ignored))
    if not names:
        raise SystemExit("no main.cvd, daily.cvd or bytecode.cvd in %s" % source)
    new = sig_info(config, source, names)
    for name in names:
        new.setdefault(name, {"version": None, "build": None, "signed": False})
    current_files = installed(config)
    current = {base: info for base, info in
               ((n.rsplit(".", 1)[0], i) for n, i in sig_info(config, config["db_dir"],
                                                               list(current_files.values())).items())}
    plan = plan_update(new, current, allow_older)
    for name in names:
        print("%-13s %s  version %s (%s)%s" % (name, "signed" if new[name]["signed"] else "UNSIGNED",
                                              new[name]["version"], new[name]["build"],
                                              "" if name in plan else "  - already installed"), flush=True)
    if not plan:
        print("UP_TO_DATE")
        return
    mounts = [config["db_dir"] + ":/db", source + ":/in:ro"]
    helper(config, SWAP, mounts, plan)
    running = (inspect(CONTAINER) or {}).get("State", {}).get("Running")
    expect = new["daily.cvd"]["version"] if "daily.cvd" in plan else None
    try:
        if running:
            print("RELOAD " + reload_and_check(config, expect), flush=True)
    except BaseException:
        helper(config, UNDO, [config["db_dir"] + ":/db"], plan)
        if running:
            try:
                clamd_call(config["port"], b"zRELOAD\0", timeout=30)
            except OSError:
                pass
        print("ROLLED_BACK: the previous definitions are in place again", flush=True)
        raise
    version = clamd_call(config["port"], b"zVERSION\0", timeout=10) if running else "clamd not running"
    print("DEFS_UPDATED %s (%s)" % (" ".join(plan), version), flush=True)


FETCH = r'''set -e
rm -rf /out/.fetch; mkdir /out/.fetch; chown clamav:clamav /out/.fetch
freshclam --stdout --datadir=/out/.fetch --config-file=/etc/clamav/freshclam.conf
for b in main daily bytecode; do [ -f /out/.fetch/$b.cvd ] || { echo "freshclam left no $b.cvd" >&2; exit 3; }; done
mv -f /out/.fetch/main.cvd /out/.fetch/daily.cvd /out/.fetch/bytecode.cvd /out/
rm -rf /out/.fetch
chown "$1" /out/main.cvd /out/daily.cvd /out/bytecode.cvd'''


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def fetch_defs(target):
    config = settings()
    os.makedirs(target, exist_ok=True)
    target = host_dir(target, "the target directory")
    owner = "%d:%d" % (os.getuid(), os.getgid()) if hasattr(os, "getuid") else "0:0"
    print("FETCH with freshclam (%s) into %s" % (config["image"], target), flush=True)
    helper(config, FETCH, [target + ":/out"], [owner], network="bridge")
    names = [base + ".cvd" for base in DATABASES]
    info = sig_info(config, target, names)
    bad = [n for n in names if not info.get(n, {}).get("signed")]
    if bad:
        raise SystemExit("downloaded files fail the signature check: %s" % ", ".join(bad))
    manifest = {"fetched": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "files": {
        n: dict(info[n], sha256=sha256(os.path.join(target, n)), bytes=os.path.getsize(os.path.join(target, n)))
        for n in names}}
    with open(os.path.join(target, "aibox-clamav-defs.json"), "w") as f:
        json.dump(manifest, f, indent=2)
    for n in names:
        print("%-13s version %s (%s)" % (n, info[n]["version"], info[n]["build"]))
    print("DEFS_FETCHED %s - copy the directory to the USB stick, then on the box: "
          "python3 clamav_deploy.py update-defs <stick>/<dir>" % target)


def deploy():
    config = settings()
    stamp = time.strftime("%Y%m%d-%H%M%S")
    if subprocess.run(["docker", "image", "inspect", config["image"]],
                      capture_output=True).returncode:
        print("PULL " + config["image"], flush=True)
        command("docker", "pull", "-q", config["image"])
    previous = inspect(CONTAINER)
    seed_db(config)
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
    out["db_dir"] = config["db_dir"]
    out["db_files"] = sorted(installed(config).values())
    print(json.dumps(out, indent=2))


def main(argv):
    if len(argv) == 2 and argv[1] in ("deploy", "status", "test"):
        if argv[1] == "deploy":
            deploy()
        elif argv[1] == "status":
            status()
        else:
            print(check_eicar(settings()["port"]))
        return 0
    if len(argv) == 3 and argv[1] == "fetch-defs":
        fetch_defs(argv[2])
        return 0
    if len(argv) in (3, 4) and argv[1] == "update-defs" and argv[3:] in ([], ["--allow-older"]):
        update_defs(argv[2], allow_older=argv[3:] == ["--allow-older"])
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
