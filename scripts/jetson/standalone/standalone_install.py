#!/usr/bin/env python3
"""Standalone installation: the S8 phases in order, resumable (E27 S8).

    sudo python3 standalone_install.py --list
    sudo python3 standalone_install.py --check          preflight only, changes nothing
    sudo python3 standalone_install.py                  every phase
    sudo python3 standalone_install.py --resume         skip the phases already done
    sudo python3 standalone_install.py --only backup    one phase

Prerequisites: the base installation (scripts/jetson/install/00-install-all.sh
on a Jetson) and an application release (172-release-box.py prepare/promote),
so vivesec-rag, -adapter, -ui (and -graph/-neo4j) run; /etc/aibox/standalone.conf
(standalone.conf.example). Phases:

    preflight  tools, swap and core dumps, settings
    time       timesync.py configure: the customer's time source only
    vault      vault.py init (recovery key on the terminal) or unlock; migrate the
               data directories onto it; boot/shutdown unit; tools in /usr/local/sbin
    tls        tls-pki.sh init (once) + https-front.sh deploy
    clamav     clamav_deploy.py deploy (+ update-defs from AIBOX_CLAMAV_DEFS_DIR)
    auth       auth_deploy.py deploy AIBOX_SOURCE_DIR + enable (standalone mode)
    backup     backup.py install-timer; with a prepared disk attached: run + drill
    audit      every check again; one PASS/FAIL line each
"""
import argparse
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import kitlib  # noqa: E402
import vault  # noqa: E402
from kitlib import KitError, log  # noqa: E402

PHASES = ("preflight", "time", "vault", "tls", "clamav", "auth", "backup", "audit")
STATE_DIR = "/var/lib/aibox-install"
STATE_FILE = STATE_DIR + "/standalone-phases"
TOOLS = ("cryptsetup", "rsync", "chattr", "lsattr", "e2fsck", "mkfs.ext4", "blkid", "fallocate",
         "docker", "openssl", "systemctl", "timedatectl")


def tool(*args, check=True):
    """Run a kit tool with the merged settings in its environment; output passes through."""
    env = dict(os.environ)
    env.update(kitlib.load_settings())
    log("$ " + " ".join(args))
    code = subprocess.call(list(args), env=env, cwd=HERE)
    if check and code:
        raise KitError("%s failed (exit %d)" % (os.path.basename(args[1] if args[0] in
                                                                  ("python3", "bash") else args[0]), code))
    return code


def py(script, *args, check=True):
    return tool(sys.executable, os.path.join(HERE, script), *args, check=check)


def sh(script, *args, check=True):
    return tool("bash", os.path.join(HERE, script), *args, check=check)


def phase_preflight(s):
    missing = [t for t in TOOLS if not shutil.which(t)]
    if missing:
        raise KitError("missing tools: %s" % " ".join(missing))
    cfg = vault.settings(s)
    vault.target(cfg)
    problems = 0
    for level, msg in vault.host_checks(cfg):
        if "mount point" in msg or msg.startswith("link "):
            continue  # the vault phase sets these up
        print("%s %s" % (level, msg))
        problems += level == "FAIL"
    for key in ("AIBOX_TLS_DNS", "AIBOX_SOURCE_DIR"):
        if not s.get(key):
            print("FAIL %s is not set" % key)
            problems += 1
    if not s.get("AIBOX_NTP_SERVERS"):
        print("FAIL AIBOX_NTP_SERVERS is not set (the customer's time source; 'none' to skip)")
        problems += 1
    if problems:
        raise KitError("%d preflight problems" % problems)
    log("PREFLIGHT_OK")


def phase_time(s):
    py("timesync.py", "configure")


def phase_vault(s):
    cfg = vault.settings(s)
    tgt = vault.target(cfg)
    luks = os.path.exists(tgt) and kitlib.run(["cryptsetup", "isLuks", tgt], check=False)[0] == 0
    if not luks:
        args = ["init"] + (["--yes-erase", cfg["device"]] if cfg["device"] else [])
        if cfg["device"]:
            print("The volume phase ERASES %s." % cfg["device"])
            if input("Type the device name to continue: ").strip() != cfg["device"]:
                raise KitError("not confirmed")
        py("vault.py", *args)
    elif not vault.is_mounted(cfg):
        py("vault.py", "unlock")
    if cfg["links"]:
        py("vault.py", "migrate")
    py("vault.py", "install-unit")
    print("Keep a LUKS header backup with the recovery key: sudo aibox-vault header-backup "
          "<file on a medium kept apart from the box>")


def phase_tls(s):
    tls_dir = s.get("AIBOX_TLS_DIR") or "/data/tls"
    if not os.path.exists(os.path.join(tls_dir, "ca", "ca.crt")):
        sh("tls-pki.sh", "init")
    sh("https-front.sh", "deploy")


def phase_clamav(s):
    py("clamav_deploy.py", "deploy")
    if s.get("AIBOX_CLAMAV_DEFS_DIR"):
        py("clamav_deploy.py", "update-defs", s["AIBOX_CLAMAV_DEFS_DIR"])


def phase_auth(s):
    source = s.get("AIBOX_SOURCE_DIR")
    if not source or not os.path.isdir(os.path.join(source, "auth_service")):
        raise KitError("AIBOX_SOURCE_DIR must be a release build directory with auth_service/")
    py("auth_deploy.py", "deploy", source)
    py("auth_deploy.py", "enable")
    print("First administrator: docker exec -it vivesec-auth python /app/auth_service/manage.py "
          "create-user admin --display-name Administrator --role admin --temporary")


def phase_backup(s):
    import backup
    cfg = backup.settings(s)
    py("backup.py", "install-timer")
    device = backup.resolve(cfg["target"])
    if not device:
        print("No backup disk attached. Prepare one: sudo aibox-backup init-target /dev/sdX --yes-erase "
              "/dev/sdX (asks for the recovery key), then: sudo aibox-backup run && sudo aibox-backup drill")
        return
    if kitlib.run(["cryptsetup", "isLuks", device], check=False)[0]:
        raise KitError("%s is not a prepared backup disk (backup.py init-target)" % device)
    py("backup.py", "run")
    py("backup.py", "drill")


def phase_audit(s):
    checks = [("volume", lambda: py("vault.py", "check", check=False)),
              ("time", lambda: py("timesync.py", "check", check=False)),
              ("backup", lambda: py("backup.py", "status", check=False)),
              ("tls", lambda: sh("tls-pki.sh", "check", check=False)),
              ("https", lambda: sh("https-front.sh", "verify", check=False)),
              ("clamav", lambda: py("clamav_deploy.py", "test", check=False)),
              ("auth", lambda: py("auth_deploy.py", "status", check=False))]
    failed = []
    for name, check in checks:
        code = check()
        print("%s %s" % ("PASS" if code == 0 else "FAIL", name), flush=True)
        if code:
            failed.append(name)
    if failed:
        raise KitError("audit: %s" % " ".join(failed))
    log("STANDALONE_AUDIT_OK")


def done_phases():
    try:
        with open(STATE_FILE) as f:
            return {line.split()[1] for line in f if len(line.split()) == 2}
    except OSError:
        return set()


def main(argv):
    p = argparse.ArgumentParser(prog="standalone_install.py", description=__doc__.split("\n")[0])
    p.add_argument("--list", action="store_true")
    p.add_argument("--check", action="store_true")
    p.add_argument("--only", choices=PHASES)
    p.add_argument("--from", dest="start", choices=PHASES)
    p.add_argument("--skip", action="append", choices=PHASES, default=[])
    p.add_argument("--resume", action="store_true")
    args = p.parse_args(argv[1:])
    if args.list:
        print("\n".join(PHASES))
        return 0
    kitlib.require_root()
    settings = kitlib.load_settings()
    if args.check:
        phase_preflight(settings)
        return 0
    selected = [args.only] if args.only else list(PHASES[PHASES.index(args.start):] if args.start else PHASES)
    os.makedirs(STATE_DIR, mode=0o750, exist_ok=True)
    done = done_phases() if args.resume else set()
    for phase in selected:
        if phase in args.skip or phase in done:
            log("--- %s: skipped" % phase)
            continue
        log("=== %s" % phase)
        globals()["phase_" + phase](kitlib.load_settings())
        with open(STATE_FILE, "a") as f:
            f.write("%s %s\n" % (time.strftime("%Y-%m-%dT%H:%M:%S"), phase))
        log("=== %s: OK" % phase)
    log("STANDALONE_INSTALL_COMPLETE")
    return 0


if __name__ == "__main__":
    sys.exit(kitlib.main_guard(main, sys.argv))
