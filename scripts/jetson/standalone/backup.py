#!/usr/bin/env python3
"""Backup and restore of a standalone box's encrypted volume (E05, E27 S8).

    backup.py init-target TARGET [--yes-erase DEV] [--size 500G] [--recovery-stdin]
    backup.py run [--if-present]     new generation on the backup disk
    backup.py rekey [--recovery-stdin]  after `vault.py rotate recovery`: the disk takes the new recovery key
    backup.py list [--from-host H] [--recovery]
    backup.py verify [GEN] [--quick] [--recovery]
    backup.py drill [GEN] [--keep]   restore into a scratch directory on the volume, verify, time it
    backup.py restore [GEN] --confirm HOSTNAME [--recovery] [--key-stdin] [--from-host H]
    backup.py status                 last run, last drill; exit 1 when the last good backup is too old
    backup.py install-timer          daily systemd timer (AIBOX_BACKUP_TIME)

The box holds the only copy of its documents, so the backup is mandatory.
Target: a removable disk (AIBOX_BACKUP_TARGET=LABEL=AIBOX-BACKUP, the default)
or a file (an absolute path, e.g. on a second internal disk). The target is a
LUKS2 volume of its own with two keys:
    backup key    random, kept on the box's encrypted volume (.aibox/backup.key),
                  so scheduled runs need nobody; it exists only while the box's
                  volume is open.
    recovery key  the box's paper recovery key (vault.py init): it opens the
                  backup disk on a replacement box. init-target checks it
                  against the box's volume before using it. The disk records
                  which recovery key of the volume it holds: after `vault.py
                  rotate recovery`, `run` refuses the disk until `rekey`
                  replaces its recovery slot (the old key stops opening it).

Each run adds a generation <host>/generations/<YYYYmmdd-HHMMSS>/: an rsync copy
of everything on the volume (unchanged files are hard links to the previous
generation, so a generation costs only the changed bytes), the LUKS header of
the volume, the container settings, the configuration file and a manifest with
the SHA-256 of every file. The first rsync pass runs live; the second, short
one runs while the containers that use the volume are paused (docker pause),
so SQLite databases, the index and the graph are copied in one consistent
state (AIBOX_BACKUP_PAUSE=off skips the pause). AIBOX_BACKUP_KEEP generations
are kept. RPO = the interval of the timer (daily by default).

`drill` is the documented, measured restore test (RTO): it restores a
generation into a scratch directory on the encrypted volume, checks every
file against the manifest, records the time and removes the copy.
Keys reach cryptsetup on stdin only.
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import socket
import stat
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kitlib  # noqa: E402
import vault  # noqa: E402
from kitlib import KitError, log, run  # noqa: E402

MAPPER = "aibox_backup"
LABEL = "AIBOX-BACKUP"
IDENTITY = "aibox-backup.json"
STATUS_FILE = "backup-status.json"
KEY_FILE = "backup.key"
LOCK_PATH = "/run/aibox-backup.lock"
GEN_RE = re.compile(r"^\d{8}-\d{6}$")
SERVICE_PATH = "/etc/systemd/system/aibox-backup.service"
TIMER_PATH = "/etc/systemd/system/aibox-backup.timer"
RSYNC = ["rsync", "-aHAX", "--numeric-ids"]
# Never part of a generation: the file system's own directory and drill scratch copies.
EXCLUDES = ["--exclude=/lost+found", "--exclude=/.aibox/drill-*"]


# ---------------------------------------------------------------- settings

def settings(values=None):
    s = kitlib.load_settings() if values is None else values
    vcfg = vault.settings(s)
    tgt = (s.get("AIBOX_BACKUP_TARGET") or "LABEL=" + LABEL).strip()
    if tgt.startswith("/"):
        tgt = vault._abs_path(tgt, "AIBOX_BACKUP_TARGET")
        if kitlib.under(tgt, vcfg["mount"]) or any(kitlib.under(tgt, p) for p in vcfg["links"]):
            raise KitError("AIBOX_BACKUP_TARGET cannot lie on the volume it backs up")
        if tgt in (vcfg["device"], vcfg["file"]):
            raise KitError("AIBOX_BACKUP_TARGET is the volume itself")
    elif not re.fullmatch(r"(LABEL|UUID|PARTUUID)=[A-Za-z0-9._-]{1,64}", tgt):
        raise KitError("AIBOX_BACKUP_TARGET must be LABEL=..., UUID=... or an absolute path")
    keep = (s.get("AIBOX_BACKUP_KEEP") or "14").strip()
    if not keep.isdigit() or not 1 <= int(keep) <= 365:
        raise KitError("AIBOX_BACKUP_KEEP must be 1-365")
    pause = (s.get("AIBOX_BACKUP_PAUSE") or "on").strip().lower()
    note = None
    if pause not in ("on", "off"):
        note, pause = "unknown AIBOX_BACKUP_PAUSE %r: pausing (on)" % pause, "on"
    when = (s.get("AIBOX_BACKUP_TIME") or "02:30").strip()
    if not re.fullmatch(r"([01][0-9]|2[0-3]):[0-5][0-9]", when):
        raise KitError("AIBOX_BACKUP_TIME must be HH:MM")
    max_age = (s.get("AIBOX_BACKUP_MAX_AGE_HOURS") or "36").strip()
    if not max_age.isdigit() or not 1 <= int(max_age) <= 8760:
        raise KitError("AIBOX_BACKUP_MAX_AGE_HOURS must be 1-8760")
    extra = []
    for raw in (s.get("AIBOX_BACKUP_EXTRA") or "/etc/aibox/standalone.conf").replace(",", " ").split():
        path = vault._abs_path(raw, "AIBOX_BACKUP_EXTRA entry")
        if kitlib.under(path, vcfg["mount"]):
            raise KitError("AIBOX_BACKUP_EXTRA: %s is on the volume (backed up anyway)" % path)
        extra.append(path)
    return {"target": tgt, "keep": int(keep), "pause": pause == "on", "note": note, "time": when,
            "max_age_hours": int(max_age), "extra": extra, "vault": vcfg}


def meta_dir(vcfg):
    return os.path.join(vcfg["mount"], vault.META_DIR)


def key_path(vcfg):
    return os.path.join(meta_dir(vcfg), KEY_FILE)


def status_path(vcfg):
    return os.path.join(meta_dir(vcfg), STATUS_FILE)


def load_status(vcfg):
    try:
        with open(status_path(vcfg)) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_status(vcfg, update):
    if not vault.is_mounted(vcfg):
        return
    data = load_status(vcfg)
    data.update(update)
    os.makedirs(meta_dir(vcfg), mode=0o700, exist_ok=True)
    tmp = status_path(vcfg) + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, status_path(vcfg))


def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


class Lock:
    def __enter__(self):
        import fcntl
        self.fcntl = fcntl
        self.f = open(LOCK_PATH, "w")
        try:
            fcntl.flock(self.f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.f.close()
            raise KitError("another backup or restore is running")
        return self

    def __exit__(self, *exc):
        self.fcntl.flock(self.f, self.fcntl.LOCK_UN)
        self.f.close()


# ---------------------------------------------------------------- the disk

def resolve(target):
    if target.startswith("/"):
        return target if os.path.exists(target) else None
    _, out, _ = run(["blkid", "-o", "device", "-t", target], check=False)
    devices = sorted(set(out.split()))
    if len(devices) > 1:
        raise KitError("more than one device matches %s: %s" % (target, " ".join(devices)))
    return devices[0] if devices else None


class BackupDisk:
    """Open and mount the backup target in a private directory under /run."""

    def __init__(self, cfg, key, readonly=False):
        self.cfg, self.key, self.readonly = cfg, key, readonly
        self.dir = self.parent = None
        self.opened = self.mounted = False

    def __enter__(self):
        device = resolve(self.cfg["target"])
        if not device:
            raise KitError("backup target %s is not attached" % self.cfg["target"])
        if os.path.exists("/dev/mapper/" + MAPPER):
            raise KitError("/dev/mapper/%s is already open (cryptsetup close %s)" % (MAPPER, MAPPER))
        args = ["open", "--type", "luks2", "--key-file", "-"] + (["--readonly"] if self.readonly else [])
        code, _, err = vault.cs(args + [device, MAPPER], key=self.key, check=False)
        if code:
            raise KitError("the key does not open the backup disk" if code == 2
                           else "cannot open the backup disk: %s" % err.strip()[-300:])
        self.opened = True
        try:
            if not self.readonly:
                code, out, _ = run(["e2fsck", "-p", "/dev/mapper/" + MAPPER], check=False)
                if code >= 2:
                    raise KitError("the backup file system needs a check (e2fsck exit %d)" % code)
            self.parent, self.dir = vault.private_mountpoint("aibox-backup-")
            run(["mount", "-o", "nodev,nosuid,noexec,noatime" + (",ro" if self.readonly else ""),
                 "/dev/mapper/" + MAPPER, self.dir])
            self.mounted = True
        except BaseException:
            self.__exit__()
            raise
        return self.dir

    def __exit__(self, *exc):
        if self.dir:
            run(["sync"], check=False)
            vault.release_mountpoint(self.parent, self.dir, mounted=self.mounted)
            self.dir, self.mounted = None, False
        if self.opened:
            vault.cs(["close", MAPPER], check=False)
            self.opened = False


def backup_key(vcfg):
    path = key_path(vcfg)
    if not os.path.exists(path):
        raise KitError("no backup key on this volume (%s): init-target first, or use --recovery" % path)
    return vault.read_key_file(path)


def open_key(cfg, recovery=False, key_stdin=False):
    if recovery or key_stdin:
        return vault.obtain_key(cfg["vault"], recovery=True, from_stdin=key_stdin)
    return backup_key(cfg["vault"])


def generations(base):
    if not os.path.isdir(base):
        return []
    return sorted(g for g in os.listdir(base)
                  if GEN_RE.match(g) and os.path.isfile(os.path.join(base, g, "meta", "manifest.json")))


def host_base(root, host):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", host or ""):
        raise KitError("bad host name %r" % host)
    return os.path.join(root, host, "generations")


def pick(base, gen):
    gens = generations(base)
    if not gens:
        raise KitError("no complete generation in %s" % base)
    if gen in (None, "", "latest"):
        return gens[-1]
    if gen not in gens:
        raise KitError("generation %s not found (have: %s)" % (gen, ", ".join(gens[-5:])))
    return gen


# ---------------------------------------------------------------- manifest

def walk(root):
    """(relative path, DirEntry, kind) of files and symlinks below root, sorted."""
    stack = [""]
    while stack:
        rel = stack.pop()
        with os.scandir(os.path.join(root, rel) if rel else root) as it:
            entries = sorted(it, key=lambda e: e.name)
        subdirs = []
        for e in entries:
            r = rel + "/" + e.name if rel else e.name
            if e.is_symlink():
                yield r, e, "l"
            elif e.is_dir(follow_symlinks=False):
                subdirs.append(r)
            elif e.is_file(follow_symlinks=False):
                yield r, e, "f"
        stack.extend(reversed(subdirs))


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def build_manifest(data_dir, prev_dir=None, prev_manifest=None):
    """Manifest of data_dir. A file that is a hard link to the same path in the
    previous generation (unchanged, rsync --link-dest) reuses its hash."""
    prev = {e["p"]: e for e in (prev_manifest or {}).get("files", []) if "h" in e}
    files, total, new = [], 0, 0
    for rel, entry, kind in walk(data_dir):
        if kind == "l":
            files.append({"p": rel, "l": os.readlink(entry.path)})
            continue
        st = entry.stat(follow_symlinks=False)
        digest = None
        old = prev.get(rel)
        if old and prev_dir and old["s"] == st.st_size:
            try:
                if os.stat(os.path.join(prev_dir, rel)).st_ino == st.st_ino:
                    digest = old["h"]
            except OSError:
                pass
        if digest is None:
            digest = sha256(entry.path)
            new += st.st_size
        files.append({"p": rel, "s": st.st_size, "h": digest})
        total += st.st_size
    files.sort(key=lambda e: e["p"])
    return {"version": 1, "files": files, "count": len(files), "bytes": total, "new_bytes": new}


def verify_tree(root, manifest, quick=False):
    """Problems of `root` against the manifest (missing, size, hash, link target)."""
    problems = []
    for e in manifest["files"]:
        path = os.path.join(root, e["p"])
        if "l" in e:
            if not os.path.islink(path) or os.readlink(path) != e["l"]:
                problems.append("link %s" % e["p"])
            continue
        try:
            st = os.lstat(path)
        except OSError:
            problems.append("missing %s" % e["p"])
            continue
        if not stat.S_ISREG(st.st_mode) or st.st_size != e["s"]:
            problems.append("size %s" % e["p"])
        elif not quick and sha256(path) != e["h"]:
            problems.append("content %s" % e["p"])
    return problems


def write_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=1)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def read_json(path):
    with open(path) as f:
        return json.load(f)


# ---------------------------------------------------------------- commands

def rsync(src, dst, extra=(), allow_vanished=False):
    code, out, err = run(RSYNC + list(extra) + [src.rstrip("/") + "/", dst.rstrip("/") + "/"], check=False)
    if code and not (allow_vanished and code == 24):
        raise KitError("rsync failed (exit %d): %s" % (code, err.strip()[-800:]))


def check_target_device(device, vcfg):
    st = os.stat(device)
    if not stat.S_ISBLK(st.st_mode):
        raise KitError("%s is not a block device" % device)
    _, mounts, _ = run(["lsblk", "-nro", "MOUNTPOINT", device], check=False)
    if mounts.strip():
        raise KitError("%s (or a partition on it) is mounted" % device)
    for used in (vault.mount_source("/") or "", vcfg["device"], vault.mount_source(vcfg["mount"]) or ""):
        if used and used.startswith("/dev/") and (used == device or used.startswith(device)
                                                  or device.startswith(used)):
            raise KitError("%s is in use by the system or the volume" % device)
    _, tran, _ = run(["lsblk", "-dno", "TRAN,RM", device], check=False)
    parts = tran.split()
    if "usb" not in parts and parts[-1:] != ["1"]:
        log("WARNING %s is not a removable USB disk (%s)" % (device, tran.strip() or "?"))


def cmd_init_target(cfg, path, yes_erase=None, size=None, recovery_stdin=False):
    kitlib.require_root()
    vcfg = cfg["vault"]
    if not vault.is_mounted(vcfg):
        raise KitError("unlock the volume first")
    path = vault._abs_path(path, "target")
    is_file = not path.startswith("/dev/")
    if is_file:
        if os.path.exists(path):
            raise KitError("%s exists" % path)
        if kitlib.under(path, vcfg["mount"]) or any(kitlib.under(path, p) for p in vcfg["links"]):
            raise KitError("the backup cannot lie on the volume it backs up")
        if not re.fullmatch(r"[1-9][0-9]*[GT]", size or ""):
            raise KitError("a file target needs --size (e.g. 200G)")
    else:
        if yes_erase != path:
            raise KitError("this erases %s: repeat with --yes-erase %s" % (path, path))
        check_target_device(path, vcfg)
    info = vault.luks_info(vcfg)
    rec = (info or {}).get("roles", {}).get("recovery")
    if not rec:
        raise KitError("the volume has no recovery key recorded")
    print("The backup disk will also open with the box's recovery key.", flush=True)
    recovery = vault.obtain_key(vcfg, recovery=True, from_stdin=recovery_stdin)
    if not vault.test_key(vcfg, recovery, rec["keyslot"]):
        raise KitError("this is not the recovery key of the volume")
    kpath = key_path(vcfg)
    if not os.path.exists(kpath):
        os.makedirs(meta_dir(vcfg), mode=0o700, exist_ok=True)
        os.replace(vault.write_key_file(kpath, vault.new_keyfile_key()), kpath)
        log("BACKUP_KEY created on the volume")
    key = vault.read_key_file(kpath)
    if is_file:
        tcfg = dict(vcfg, file=path, device="", size=size)
        vault.prepare_target(tcfg, None)
    else:
        tcfg = dict(vcfg, device=path, file="")
    try:
        vault.cs(["-q", "luksFormat"] + vault.LUKS_FORMAT + vcfg["pbkdf"]
                 + ["--label", LABEL, "--key-slot", "0", "--key-file", "-", path], key=key)
        vault.cs(["luksAddKey", "--key-slot", "1", "--key-file", "-"] + vcfg["pbkdf"] + [path, "{NEWKEY}"],
                 key=key, new_key=recovery)
        vault.set_role(tcfg, vault.luks_info(tcfg), "backup", 0, "backup-key")
        vault.set_role(tcfg, vault.luks_info(tcfg), "recovery", 1, "recovery-key",
                       {"volume_recovery": vault.recovery_id(info)})
        vault.cs(["open", "--type", "luks2", "--key-file", "-", path, MAPPER], key=key)
        try:
            run(["mkfs.ext4", "-q", "-L", "aibox-backup", "/dev/mapper/" + MAPPER])
        finally:
            vault.cs(["close", MAPPER], check=False)
    except BaseException:
        if is_file and os.path.exists(path):
            os.unlink(path)
        raise
    with BackupDisk(dict(cfg, target=path), key) as root:
        write_json(os.path.join(root, IDENTITY), {"created": now_iso(), "by": socket.gethostname(),
                                                   "volume_uuid": info["uuid"]})
    log("BACKUP_TARGET_READY %s (opens with the backup key and the recovery key)" % path)
    if is_file and cfg["target"] != path:
        print("Set AIBOX_BACKUP_TARGET=%s in /etc/aibox/standalone.conf" % path)
    elif not is_file and not cfg["target"].startswith("LABEL="):
        print("Set AIBOX_BACKUP_TARGET=LABEL=%s in /etc/aibox/standalone.conf" % LABEL)


def pause_containers(names):
    paused = []
    for name in names:
        state = kitlib.docker_inspect([name])
        if not state or not state[0]["State"].get("Running") or state[0]["State"].get("Paused"):
            continue
        if run(["docker", "pause", name], check=False)[0] == 0:
            paused.append(name)
    return paused


def unpause_containers(names):
    for name in names:
        run(["docker", "unpause", name], check=False)


def disk_cfg(vcfg, device):
    """vault-style settings that point the LUKS helpers at a backup disk."""
    return dict(vcfg, device="", file=device) if not device.startswith("/dev/") else dict(vcfg, device=device, file="")


def stale_recovery(vcfg, device):
    """None when the disk's recovery slot holds the volume's current recovery
    key, else why not (a rotated recovery key must not keep opening backups)."""
    current = vault.recovery_id(vault.luks_info(vcfg))
    disk = (vault.luks_info(disk_cfg(vcfg, device)) or {}).get("roles", {}).get("recovery")
    if not current:
        return "the volume has no recovery key recorded"
    if not disk:
        return "the backup disk has no recovery key recorded"
    if disk.get("volume_recovery") != current:
        return ("the backup disk still opens with a previous recovery key of the volume "
                "(run backup.py rekey with the current recovery key)")
    return None


def cmd_rekey(cfg, recovery_stdin=False):
    """Replace the recovery slot of the attached backup disk with the volume's
    current recovery key (after vault.py rotate recovery)."""
    kitlib.require_root()
    vcfg = cfg["vault"]
    if not vault.is_mounted(vcfg):
        raise KitError("unlock the volume first")
    with Lock():
        device = resolve(cfg["target"])
        if not device:
            raise KitError("backup target %s is not attached" % cfg["target"])
        info = vault.luks_info(vcfg)
        rec = (info or {}).get("roles", {}).get("recovery")
        if not rec:
            raise KitError("the volume has no recovery key recorded")
        print("Current recovery key of the volume:", flush=True)
        recovery = vault.obtain_key(vcfg, recovery=True, from_stdin=recovery_stdin)
        if not vault.test_key(vcfg, recovery, rec["keyslot"]):
            raise KitError("this is not the current recovery key of the volume")
        dcfg = disk_cfg(vcfg, device)
        disk = vault.luks_info(dcfg)
        if not disk:
            raise KitError("%s is not a LUKS backup disk" % device)
        key = backup_key(vcfg)
        if not vault.test_key(dcfg, key):
            raise KitError("the backup key of this volume does not open %s" % device)
        old = disk["roles"].get("recovery")
        slot = vault.free_slot(disk)
        vault.cs(["luksAddKey", "--key-slot", str(slot), "--key-file", "-"] + vcfg["pbkdf"]
                 + [device, "{NEWKEY}"], key=key, new_key=recovery)
        if not vault.test_key(dcfg, recovery, slot):
            vault.cs(["luksKillSlot", "--key-file", "-", device, str(slot)], key=key, check=False)
            raise KitError("the new recovery slot does not open the disk; nothing changed")
        if old and old["keyslot"] != slot:
            vault.cs(["luksKillSlot", "--key-file", "-", device, str(old["keyslot"])], key=key)
        # recorded only once the old key is gone, so an interrupted rekey stays "stale"
        vault.set_role(dcfg, vault.luks_info(dcfg), "recovery", slot, "recovery-key",
                       {"volume_recovery": vault.recovery_id(info)})
    log("REKEYED %s: the previous recovery key no longer opens it" % device)


def cmd_run(cfg, if_present=False):
    kitlib.require_root()
    vcfg = cfg["vault"]
    if cfg["note"]:
        log("WARNING " + cfg["note"])
    with Lock():
        if not vault.is_mounted(vcfg):
            if if_present:
                log("SKIPPED: the volume is locked")
                return 0
            raise KitError("unlock the volume first")
        if not resolve(cfg["target"]):
            save_status(vcfg, {"last_attempt": {"at": now_iso(), "result": "target not attached"}})
            if if_present:
                log("SKIPPED: backup target %s is not attached" % cfg["target"])
                return 0
            raise KitError("backup target %s is not attached" % cfg["target"])
        started, t0 = now_iso(), time.time()
        result = {"at": started, "result": "failed"}
        try:
            stale = stale_recovery(vcfg, resolve(cfg["target"]))
            if stale:
                raise KitError(stale)
            with BackupDisk(cfg, backup_key(vcfg)) as root:
                result.update(_run(cfg, root, t0))
        except BaseException as e:
            result["error"] = str(e)[-500:]
            save_status(vcfg, {"last_attempt": result})
            raise
        result["result"] = "ok"
        save_status(vcfg, {"last_attempt": result, "last_ok": result})
        log("BACKUP_OK %s: %d files, %.1f MB (%.1f MB new), paused %.1f s, %.0f s total; %.0f GB free"
            % (result["generation"], result["files"], result["bytes"] / 1e6, result["new_bytes"] / 1e6,
               result["pause_s"], result["duration_s"], result["free_bytes"] / 1e9))
    return 0


def _run(cfg, root, t0):
    vcfg = cfg["vault"]
    host = socket.gethostname()
    base = host_base(root, host)
    os.makedirs(base, mode=0o700, exist_ok=True)
    for name in os.listdir(base):
        if name.endswith(".partial"):
            shutil.rmtree(os.path.join(base, name))
    gens = generations(base)
    prev = os.path.join(base, gens[-1]) if gens else None
    stamp = time.strftime("%Y%m%d-%H%M%S")
    if gens and gens[-1] >= stamp:
        raise KitError("a generation %s already exists (clock?)" % gens[-1])
    work = os.path.join(base, stamp + ".partial")
    for sub in ("data", "meta", "extra"):
        os.makedirs(os.path.join(work, sub), mode=0o700)
    opts = EXCLUDES + ["--delete"] + (["--link-dest=" + os.path.join(prev, "data")] if prev else [])
    log("PASS 1 (live) %s -> %s" % (vcfg["mount"], stamp))
    rsync(vcfg["mount"], os.path.join(work, "data"), opts, allow_vanished=True)
    names = vault.dependent_containers(vcfg) if cfg["pause"] else []
    log("PASS 2 (paused: %s)" % (" ".join(names) or "nothing"))
    tp = time.time()
    paused = []
    try:
        paused = pause_containers(names)
        rsync(vcfg["mount"], os.path.join(work, "data"), opts)
    finally:
        unpause_containers(paused)
    pause_s = time.time() - tp
    for path in cfg["extra"]:
        if os.path.exists(path):
            run(RSYNC + ["-R", path, os.path.join(work, "extra") + "/"])
    vault.cs(["luksHeaderBackup", vault.target(vcfg), "--header-backup-file",
              os.path.join(work, "meta", "luks-header.img")])
    containers = kitlib.docker_inspect(vault.dependent_containers(vcfg))
    write_json(os.path.join(work, "meta", "containers.json"), containers)
    prev_manifest = read_json(os.path.join(prev, "meta", "manifest.json")) if prev else None
    manifest = build_manifest(os.path.join(work, "data"), prev and os.path.join(prev, "data"), prev_manifest)
    write_json(os.path.join(work, "meta", "manifest.json"), manifest)
    info = vault.luks_info(vcfg) or {}
    summary = {"generation": stamp, "host": host, "volume_uuid": info.get("uuid"),
               "files": manifest["count"], "bytes": manifest["bytes"], "new_bytes": manifest["new_bytes"],
               "pause_s": round(pause_s, 1), "previous": gens[-1] if gens else None}
    summary["duration_s"] = round(time.time() - t0, 1)
    write_json(os.path.join(work, "meta", "info.json"), summary)
    run(["sync"], check=False)
    os.rename(work, os.path.join(base, stamp))
    latest = os.path.join(root, host, "latest")
    tmp = latest + ".tmp"
    if os.path.lexists(tmp):
        os.unlink(tmp)
    os.symlink(os.path.join("generations", stamp), tmp)
    os.replace(tmp, latest)
    for old in generations(base)[:-cfg["keep"]]:
        shutil.rmtree(os.path.join(base, old))
        log("PRUNED %s" % old)
    summary["free_bytes"] = shutil.disk_usage(root).free
    summary["duration_s"] = round(time.time() - t0, 1)
    return summary


def cmd_list(cfg, from_host=None, recovery=False, key_stdin=False):
    kitlib.require_root()
    with Lock(), BackupDisk(cfg, open_key(cfg, recovery, key_stdin), readonly=True) as root:
        hosts = sorted(h for h in os.listdir(root) if os.path.isdir(os.path.join(root, h, "generations")))
        for host in ([from_host] if from_host else hosts):
            base = host_base(root, host)
            print("%s:" % host)
            for g in generations(base):
                try:
                    i = read_json(os.path.join(base, g, "meta", "info.json"))
                except (OSError, ValueError):
                    i = {}
                print("  %s  %7d files  %10.1f MB  %8.1f MB new  pause %5.1f s" % (
                    g, i.get("files", 0), i.get("bytes", 0) / 1e6, i.get("new_bytes", 0) / 1e6,
                    i.get("pause_s", 0)))
        print("free: %.1f GB" % (shutil.disk_usage(root).free / 1e9))


def cmd_verify(cfg, gen=None, quick=False, from_host=None, recovery=False, key_stdin=False):
    kitlib.require_root()
    with Lock(), BackupDisk(cfg, open_key(cfg, recovery, key_stdin), readonly=True) as root:
        base = host_base(root, from_host or socket.gethostname())
        g = pick(base, gen)
        manifest = read_json(os.path.join(base, g, "meta", "manifest.json"))
        t0 = time.time()
        problems = verify_tree(os.path.join(base, g, "data"), manifest, quick)
    for p in problems[:50]:
        print("BAD " + p)
    if problems:
        raise KitError("generation %s: %d problems" % (g, len(problems)))
    log("VERIFY_OK %s: %d files%s in %.0f s" % (g, manifest["count"], "" if quick else " (SHA-256)",
                                                time.time() - t0))


def cmd_drill(cfg, gen=None, keep=False, from_host=None, recovery=False, key_stdin=False):
    kitlib.require_root()
    vcfg = cfg["vault"]
    if not vault.is_mounted(vcfg):
        raise KitError("unlock the volume first")
    with Lock(), BackupDisk(cfg, open_key(cfg, recovery, key_stdin), readonly=True) as root:
        base = host_base(root, from_host or socket.gethostname())
        g = pick(base, gen)
        manifest = read_json(os.path.join(base, g, "meta", "manifest.json"))
        usage = shutil.disk_usage(vcfg["mount"])
        reserve = min(5 << 30, usage.total // 20)
        if manifest["bytes"] > usage.free - reserve:
            raise KitError("the drill needs %.1f GB on the volume, %.1f GB are free"
                           % (manifest["bytes"] / 1e9, usage.free / 1e9))
        scratch = os.path.join(meta_dir(vcfg), "drill-" + time.strftime("%Y%m%d-%H%M%S"))
        os.makedirs(scratch, mode=0o700)
        t0 = time.time()
        try:
            rsync(os.path.join(base, g, "data"), scratch)
            t_copy = time.time() - t0
            problems = verify_tree(scratch, manifest)
        finally:
            if not keep:
                shutil.rmtree(scratch, ignore_errors=True)
        total = time.time() - t0
    result = {"at": now_iso(), "generation": g, "files": manifest["count"], "bytes": manifest["bytes"],
              "copy_s": round(t_copy, 1), "duration_s": round(total, 1),
              "result": "ok" if not problems else "%d problems" % len(problems)}
    save_status(vcfg, {"last_drill": result})
    for p in problems[:50]:
        print("BAD " + p)
    if problems:
        raise KitError("restore drill of %s failed: %d problems" % (g, len(problems)))
    log("DRILL_OK %s: %d files, %.1f MB restored in %.0f s, verified (SHA-256) in %.0f s total%s"
        % (g, manifest["count"], manifest["bytes"] / 1e6, t_copy, total,
           "; kept in %s" % scratch if keep else ""))


def cmd_restore(cfg, gen=None, confirm=None, from_host=None, recovery=False, key_stdin=False):
    kitlib.require_root()
    vcfg = cfg["vault"]
    host = socket.gethostname()
    if confirm != host:
        raise KitError("this replaces everything on the volume: repeat with --confirm %s" % host)
    if not vault.is_mounted(vcfg):
        raise KitError("unlock the volume first (on a new box: vault.py init, migrate)")
    with Lock(), BackupDisk(cfg, open_key(cfg, recovery, key_stdin), readonly=True) as root:
        base = host_base(root, from_host or host)
        g = pick(base, gen)
        src = os.path.join(base, g)
        manifest = read_json(os.path.join(src, "meta", "manifest.json"))
        names = vault.dependent_containers(vcfg)
        t0 = time.time()
        vault.stop_containers(names)
        try:
            rsync(os.path.join(src, "data"), vcfg["mount"],
                  EXCLUDES + ["--delete", "--exclude=/.aibox/"])
            old_key = os.path.join(src, "data", vault.META_DIR, KEY_FILE)
            if not os.path.exists(key_path(vcfg)) and os.path.exists(old_key):
                os.makedirs(meta_dir(vcfg), mode=0o700, exist_ok=True)
                shutil.copy2(old_key, key_path(vcfg))
            problems = [p for p in verify_tree(vcfg["mount"], manifest, quick=True)
                        if not p.split(" ", 1)[1].startswith(vault.META_DIR + "/")]
        finally:
            failed = vault.start_containers(names)
        total = time.time() - t0
    result = {"at": now_iso(), "generation": g, "from_host": from_host or host,
              "duration_s": round(total, 1), "result": "ok" if not problems and not failed else "problems"}
    save_status(vcfg, {"last_restore": result})
    for p in problems[:50]:
        print("BAD " + p)
    if problems:
        raise KitError("restore of %s: %d problems" % (g, len(problems)))
    if failed:
        raise KitError("restored, but containers did not start: %s" % ", ".join(failed))
    log("RESTORE_OK %s in %.0f s (%d files). Container settings and the configuration of that "
        "time: %s/meta/containers.json, %s/extra/ on the backup disk"
        % (g, total, manifest["count"], g, g))


def cmd_status(cfg):
    vcfg = cfg["vault"]
    if not vault.is_mounted(vcfg):
        print("volume locked: no status")
        return 1
    data = load_status(vcfg)
    print(json.dumps(data, indent=2))
    ok = data.get("last_ok")
    if not ok:
        print("NO_BACKUP_YET")
        return 1
    try:
        at = time.mktime(time.strptime(ok["at"][:19], "%Y-%m-%dT%H:%M:%S"))
    except (KeyError, ValueError):
        return 1
    age = (time.time() - at) / 3600
    if age > cfg["max_age_hours"]:
        print("BACKUP_STALE last good backup %.0f h ago (limit %d h)" % (age, cfg["max_age_hours"]))
        return 1
    print("BACKUP_FRESH last good backup %.1f h ago" % age)
    return 0


def timer_units(when):
    service = """[Unit]
Description=ViVeSec AI Box backup
After=aibox-vault.service docker.service

[Service]
Type=oneshot
ExecStart=/usr/bin/python3 {lib}/backup.py run --if-present
Nice=10
IOSchedulingClass=idle
""".format(lib=vault.LIB_DIR)
    timer = """[Unit]
Description=Daily ViVeSec AI Box backup

[Timer]
OnCalendar=*-*-* {when}:00
Persistent=true

[Install]
WantedBy=timers.target
""".format(when=when)
    return service, timer


def cmd_install_timer(cfg):
    kitlib.require_root()
    vault.install_kit_files()
    service, timer = timer_units(cfg["time"])
    for path, text in ((SERVICE_PATH, service), (TIMER_PATH, timer)):
        with open(path, "w") as f:
            f.write(text)
    run(["systemctl", "daemon-reload"])
    run(["systemctl", "enable", "--now", "aibox-backup.timer"])
    log("TIMER_INSTALLED daily at %s (target %s)" % (cfg["time"], cfg["target"]))


def main(argv):
    p = argparse.ArgumentParser(prog="backup.py", description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd")
    a = sub.add_parser("init-target")
    a.add_argument("path")
    a.add_argument("--yes-erase")
    a.add_argument("--size")
    a.add_argument("--recovery-stdin", action="store_true")
    sub.add_parser("run").add_argument("--if-present", action="store_true")
    sub.add_parser("rekey").add_argument("--recovery-stdin", action="store_true")
    for name in ("list", "verify", "drill", "restore"):
        a = sub.add_parser(name)
        if name != "list":
            a.add_argument("generation", nargs="?")
        a.add_argument("--from-host")
        a.add_argument("--recovery", action="store_true", help="open the disk with the recovery key")
        a.add_argument("--key-stdin", action="store_true", help="recovery key on stdin")
        if name == "verify":
            a.add_argument("--quick", action="store_true")
        if name == "drill":
            a.add_argument("--keep", action="store_true")
        if name == "restore":
            a.add_argument("--confirm")
    sub.add_parser("status")
    sub.add_parser("install-timer")
    args = p.parse_args(argv[1:])
    if not args.cmd:
        print(__doc__)
        return 2
    cfg = settings()
    if args.cmd == "init-target":
        cmd_init_target(cfg, args.path, args.yes_erase, args.size, args.recovery_stdin)
    elif args.cmd == "run":
        return cmd_run(cfg, args.if_present)
    elif args.cmd == "rekey":
        cmd_rekey(cfg, args.recovery_stdin)
    elif args.cmd == "list":
        cmd_list(cfg, args.from_host, args.recovery, args.key_stdin)
    elif args.cmd == "verify":
        cmd_verify(cfg, args.generation, args.quick, args.from_host, args.recovery, args.key_stdin)
    elif args.cmd == "drill":
        cmd_drill(cfg, args.generation, args.keep, args.from_host, args.recovery, args.key_stdin)
    elif args.cmd == "restore":
        cmd_restore(cfg, args.generation, args.confirm, args.from_host, args.recovery, args.key_stdin)
    elif args.cmd == "status":
        return cmd_status(cfg)
    elif args.cmd == "install-timer":
        cmd_install_timer(cfg)
    return 0


if __name__ == "__main__":
    sys.exit(kitlib.main_guard(main, sys.argv))
