#!/usr/bin/env python3
"""Encrypted data volume of a standalone box (E05, E27 S8).

    vault.py status [--json]          state of the volume, keys, guard, links, containers
    vault.py check                    host checks: swap, core dumps, guard, links, containers
    vault.py init [--yes-erase DEV]   create the LUKS2 volume (+ recovery key), format, mount
    vault.py unlock [--recovery] [--key-stdin] [--no-start]
    vault.py lock                     stop the dependent containers, unmount, close
    vault.py migrate                  move AIBOX_VAULT_LINKS onto the volume (symlinks back)
    vault.py wipe-plaintext --confirm delete the plaintext copies migrate kept
    vault.py rotate unlock|recovery [--method M] [--key-stdin]
    vault.py header-backup FILE       LUKS header copy (store it like the recovery key)
    vault.py erase --confirm HOSTNAME cryptographic erase: every key slot is destroyed
    vault.py install-unit             systemd unit: unlock at boot (usb/keyfile), lock at shutdown
    vault.py boot                     what the unit runs

The volume is LUKS2 (AES-XTS-512, argon2id) on a partition (AIBOX_VAULT_DEVICE)
or in a file (AIBOX_VAULT_FILE + AIBOX_VAULT_SIZE; DGX Spark: the factory OS
fills the whole SSD). It is mounted at AIBOX_VAULT_MOUNT (default /data).
Directories elsewhere move onto it with `migrate` and stay reachable through a
relative symlink (AIBOX_VAULT_LINKS, e.g. /data/adapter on a box whose /data
holds the docker root as well).

Fail closed: while the volume is closed its mount point is immutable
(chattr +i), so nothing - in particular a container started by docker at boot -
can write application data onto the plaintext disk: such a container fails to
start and `unlock` starts it once the volume is mounted.

Key slots (the role of each is recorded in a LUKS2 token of the header):
    unlock    AIBOX_VAULT_UNLOCK: passphrase (default; nothing stored, typed at
              `unlock`), usb (random key file on a removable stick, unattended
              boot while the stick is in), keyfile (random key in
              AIBOX_VAULT_KEYFILE on the system disk; protects a removed data
              disk only - development). Unknown values mean passphrase.
    recovery  160-bit random key shown once at `init`/`rotate`; written on the
              handover sheet. It also opens the backup disks (backup.py).
Keys reach cryptsetup on stdin or through a pipe, never on the command line.
"""
import argparse
import base64
import json
import os
import re
import secrets
import shutil
import socket
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kitlib  # noqa: E402
from kitlib import KitError, log, run  # noqa: E402

UNLOCK_MODES = ("passphrase", "usb", "keyfile")
TOKEN_TYPE = "aibox-role"
LABEL = "aibox-vault"
META_DIR = ".aibox"
MIN_PASSPHRASE = 12
RECOVERY_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"  # Crockford base32
LUKS_FORMAT = ["--type", "luks2", "--cipher", "aes-xts-plain64", "--key-size", "512",
               "--hash", "sha256"]
LIB_DIR = "/usr/local/lib/aibox"
UNIT_PATH = "/etc/systemd/system/aibox-vault.service"
KIT_FILES = ("kitlib.py", "vault.py", "backup.py", "timesync.py")


# ---------------------------------------------------------------- settings

def _abs_path(value, what):
    value = (value or "").strip()
    if value != "/":
        value = value.rstrip("/")
    if not value.startswith("/") or ".." in value.split("/") or "//" in value:
        raise KitError("%s must be an absolute path without '..': %r" % (what, value))
    return value


def settings(values=None):
    """Validated vault settings from `values` (default: config file + environment)."""
    s = kitlib.load_settings() if values is None else values
    device = (s.get("AIBOX_VAULT_DEVICE") or "").strip()
    image = (s.get("AIBOX_VAULT_FILE") or "").strip()
    if device and image:
        raise KitError("set AIBOX_VAULT_DEVICE or AIBOX_VAULT_FILE, not both")
    if device:
        device = _abs_path(device, "AIBOX_VAULT_DEVICE")
        if not device.startswith("/dev/"):
            raise KitError("AIBOX_VAULT_DEVICE must be a /dev path")
    if image:
        image = _abs_path(image, "AIBOX_VAULT_FILE")
    name = (s.get("AIBOX_VAULT_NAME") or "aibox_vault").strip()
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,30}", name):
        raise KitError("AIBOX_VAULT_NAME must be lower-case letters, digits, _")
    mount = _abs_path(s.get("AIBOX_VAULT_MOUNT") or "/data", "AIBOX_VAULT_MOUNT")
    if mount == "/" or mount.count("/") < 1 or mount in ("/etc", "/usr", "/var", "/home", "/root",
                                                        "/boot", "/tmp", "/run"):
        raise KitError("AIBOX_VAULT_MOUNT cannot be %s" % mount)
    links = []
    for raw in (s.get("AIBOX_VAULT_LINKS") or "").replace(",", " ").split():
        path = _abs_path(raw, "AIBOX_VAULT_LINKS entry")
        if path.count("/") < 2:
            raise KitError("AIBOX_VAULT_LINKS: %s is a top-level directory" % path)
        if kitlib.under(path, mount) or kitlib.under(mount, path):
            raise KitError("AIBOX_VAULT_LINKS: %s overlaps the mount point %s" % (path, mount))
        if os.path.basename(path) in [os.path.basename(p) for p in links] \
                or os.path.basename(path) == META_DIR:
            raise KitError("AIBOX_VAULT_LINKS: two entries named %s" % os.path.basename(path))
        links.append(path)
    if image and (kitlib.under(image, mount) or any(kitlib.under(image, p) for p in links)):
        raise KitError("AIBOX_VAULT_FILE cannot lie on the volume itself")
    mode = (s.get("AIBOX_VAULT_UNLOCK") or "passphrase").strip().lower()
    mode_note = None
    if mode not in UNLOCK_MODES:
        mode_note = "unknown AIBOX_VAULT_UNLOCK %r: using passphrase" % mode
        mode = "passphrase"
    usb = (s.get("AIBOX_VAULT_USB") or "LABEL=AIBOX-KEY").strip()
    if not re.fullmatch(r"(LABEL|UUID|PARTUUID)=[A-Za-z0-9._-]{1,64}", usb):
        raise KitError("AIBOX_VAULT_USB must be LABEL=..., UUID=... or PARTUUID=...")
    usb_file = (s.get("AIBOX_VAULT_USB_FILE") or "aibox-vault.key").strip()
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", usb_file) or usb_file.startswith("."):
        raise KitError("AIBOX_VAULT_USB_FILE must be a plain file name")
    wait = (s.get("AIBOX_VAULT_USB_WAIT") or "120").strip()
    if not wait.isdigit() or int(wait) > 3600:
        raise KitError("AIBOX_VAULT_USB_WAIT must be seconds (0-3600)")
    containers = (s.get("AIBOX_VAULT_CONTAINERS") or "").replace(",", " ").split()
    for c in containers:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", c):
            raise KitError("AIBOX_VAULT_CONTAINERS: bad name %r" % c)
    return {"device": device, "file": image, "size": (s.get("AIBOX_VAULT_SIZE") or "").strip(),
            "name": name, "mount": mount, "links": links, "unlock": mode, "unlock_note": mode_note,
            "keyfile": _abs_path(s.get("AIBOX_VAULT_KEYFILE") or "/etc/aibox/vault.key",
                                 "AIBOX_VAULT_KEYFILE"),
            "usb": usb, "usb_file": usb_file, "usb_wait": int(wait), "containers": containers,
            "pbkdf": ["--pbkdf", "argon2id"]}


def target(cfg):
    path = cfg["device"] or cfg["file"]
    if not path:
        raise KitError("no volume configured: set AIBOX_VAULT_DEVICE or AIBOX_VAULT_FILE")
    return path


def mapper(cfg):
    return "/dev/mapper/" + cfg["name"]


# ---------------------------------------------------------------- keys

def new_recovery_key():
    raw = int.from_bytes(secrets.token_bytes(20), "big")
    chars = "".join(RECOVERY_ALPHABET[(raw >> (5 * i)) & 31] for i in range(32))
    return chars


def format_recovery(key):
    return "-".join(key[i:i + 4] for i in range(0, len(key), 4))


def normalize_recovery(text):
    """Typed recovery key -> the canonical 32 characters (the LUKS passphrase)."""
    value = re.sub(r"[\s-]", "", (text or "").upper())
    value = value.translate(str.maketrans("OIL", "011"))
    if len(value) != 32 or any(c not in RECOVERY_ALPHABET for c in value):
        raise KitError("not a recovery key (32 characters, groups of four)")
    return value


def check_passphrase(value):
    if len(value) < MIN_PASSPHRASE:
        raise KitError("the passphrase needs at least %d characters" % MIN_PASSPHRASE)
    if len(set(value)) < 5:
        raise KitError("the passphrase is too uniform")
    return value.encode("utf-8")


def new_keyfile_key():
    # Printable, so the same key also works when typed: 64 random bytes as base64.
    return base64.b64encode(secrets.token_bytes(48))


# ---------------------------------------------------------------- cryptsetup

def cs(args, key=None, new_key=None, check=True):
    """cryptsetup with the key on stdin and an added key through a pipe."""
    if new_key is None:
        return run(["cryptsetup"] + args, stdin=key, check=check)
    read_fd, write_fd = os.pipe()
    try:
        os.write(write_fd, new_key)
        os.close(write_fd)
        write_fd = None
        argv = ["cryptsetup"] + [a.replace("{NEWKEY}", "/dev/fd/%d" % read_fd) for a in args]
        import subprocess
        result = subprocess.run(argv, input=key, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                pass_fds=(read_fd,),
                                **({} if key is not None else {"stdin": subprocess.DEVNULL}))
    finally:
        os.close(read_fd)
        if write_fd is not None:
            os.close(write_fd)
    err = result.stderr.decode("utf-8", "replace")
    if check and result.returncode:
        raise KitError("cryptsetup %s failed (exit %d): %s" % (args[0], result.returncode,
                                                               err.strip()[-800:]))
    return result.returncode, result.stdout.decode("utf-8", "replace"), err


def parse_dump(text):
    """Key slots, tokens, UUID and label from `cryptsetup luksDump` (LUKS2 text)."""
    out = {"version": None, "uuid": None, "label": None, "keyslots": [], "tokens": {}}
    section = None
    token = None
    for line in text.splitlines():
        if not line.strip():
            continue
        if not line[0].isspace():
            head, _, value = line.partition(":")
            section = head.strip()
            value = value.strip()
            if section == "Version":
                out["version"] = value
            elif section == "UUID":
                out["uuid"] = value
            elif section == "Label":
                out["label"] = None if value in ("", "(no label)") else value
            token = None
            continue
        entry = re.match(r"^  (\d+): (\S+)", line)
        if section == "Keyslots" and entry:
            out["keyslots"].append(int(entry.group(1)))
        elif section == "Tokens":
            if entry:
                token = int(entry.group(1))
                out["tokens"][token] = {"type": entry.group(2), "keyslots": []}
            else:
                slot = re.match(r"^\s+Keyslot:\s+(\d+)", line)
                if slot and token is not None:
                    out["tokens"][token]["keyslots"].append(int(slot.group(1)))
    return out


def luks_info(cfg):
    code, out, _ = cs(["luksDump", target(cfg)], check=False)
    if code:
        return None
    info = parse_dump(out)
    info["roles"] = {}
    for tid, tok in sorted(info["tokens"].items()):
        if tok["type"] != TOKEN_TYPE:
            continue
        code, data, _ = cs(["token", "export", "--token-id", str(tid), target(cfg)], check=False)
        try:
            meta = json.loads(data) if not code else {}
        except ValueError:
            meta = {}
        role = meta.get("role")
        if role:
            info["roles"][role] = {"token": tid, "keyslot": int((meta.get("keyslots") or ["-1"])[0]),
                                   "method": meta.get("method"), "created": meta.get("created"),
                                   "volume_recovery": meta.get("volume_recovery")}
    return info


def role_token(role, slot, method, extra=None):
    token = {"type": TOKEN_TYPE, "keyslots": [str(slot)], "role": role,
             "method": method, "created": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    token.update(extra or {})
    return json.dumps(token)


def set_role(cfg, info, role, slot, method, extra=None):
    old = (info or {}).get("roles", {}).get(role)
    if old:
        cs(["token", "remove", "--token-id", str(old["token"]), target(cfg)])
    cs(["token", "import", "--json-file", "-", target(cfg)],
       key=role_token(role, slot, method, extra).encode())


def recovery_id(info):
    """Identifies the volume's current recovery key (its slot and creation time);
    a backup disk records it, so a rotated key is noticed there."""
    rec = (info or {}).get("roles", {}).get("recovery")
    return "%s/%s" % (rec["keyslot"], rec["created"]) if rec else None


def free_slot(info):
    for slot in range(32):
        if slot not in info["keyslots"]:
            return slot
    raise KitError("no free LUKS key slot")


def test_key(cfg, key, slot=None):
    args = ["open", "--test-passphrase", "--type", "luks2", "--key-file", "-"]
    if slot is not None:
        args += ["--key-slot", str(slot)]
    return cs(args + [target(cfg)], key=key, check=False)[0] == 0


# ---------------------------------------------------------------- host state

def is_open(cfg):
    return os.path.exists(mapper(cfg))


def mount_source(path):
    try:
        with open("/proc/self/mountinfo") as f:
            for line in f:
                parts = line.split()
                if len(parts) > 4 and parts[4] == path:
                    sep = parts.index("-")
                    return parts[sep + 2]
    except OSError:
        pass
    return None


def is_mounted(cfg):
    return mount_source(cfg["mount"]) is not None


def immutable(path):
    code, out, _ = run(["lsattr", "-d", path], check=False)
    return code == 0 and "i" in out.split()[0] if out.strip() else False


def guard(cfg, quiet=False):
    """Make the closed volume's mount point immutable (fail closed)."""
    path = cfg["mount"]
    if is_mounted(cfg):
        return "mounted"
    os.makedirs(path, mode=0o755, exist_ok=True)
    leftovers = os.listdir(path)
    if leftovers:
        msg = ("%s is not empty while the volume is closed (%s): plaintext data on the system disk"
               % (path, ", ".join(sorted(leftovers)[:5])))
        if quiet:
            log("WARNING " + msg)
            return "not-empty"
        raise KitError(msg)
    if not immutable(path):
        run(["chattr", "+i", path])
    return "immutable"


def link_state(cfg):
    out = []
    for path in cfg["links"]:
        want = os.path.join(cfg["mount"], os.path.basename(path))
        if os.path.islink(path):
            ok = os.path.realpath(path) == os.path.realpath(want)
            out.append({"path": path, "state": "linked" if ok else "wrong-link",
                        "target": os.readlink(path)})
        elif os.path.isdir(path):
            out.append({"path": path, "state": "plaintext-dir"})
        elif os.path.exists(path):
            out.append({"path": path, "state": "not-a-directory"})
        else:
            out.append({"path": path, "state": "missing"})
    return out


def dependent_containers(cfg, containers=None):
    """Containers that use the volume: an explicit list, or every container
    with a restart policy whose bind or volume source resolves onto the volume
    (or a link). Parked `*-prev-*` containers are left alone."""
    containers = kitlib.docker_containers() if containers is None else containers
    roots = [cfg["mount"]] + cfg["links"]
    names = []
    for c in containers:
        name = c.get("Name", "").lstrip("/")
        if cfg["containers"]:
            if name in cfg["containers"]:
                names.append(name)
            continue
        if "-prev-" in name:
            continue
        policy = ((c.get("HostConfig") or {}).get("RestartPolicy") or {}).get("Name")
        if policy not in ("always", "unless-stopped"):
            continue
        for m in c.get("Mounts") or []:
            source = m.get("Source") or ""
            real = os.path.realpath(source) if source else ""
            if any(kitlib.under(p, r) for p in (source, real) if p for r in roots):
                names.append(name)
                break
    return kitlib.order_containers(names)


def start_containers(names):
    failed = []
    for name in names:
        code, _, err = run(["docker", "start", name], check=False)
        log(("START " if not code else "START FAILED ") + name + ("" if not code else ": " + err.strip()[-300:]))
        if code:
            failed.append(name)
    return failed


def stop_containers(names, timeout=60):
    """Stop them in parallel (one docker call): some images ignore SIGTERM and
    wait the full timeout, which must stay within the unit's stop timeout."""
    running = [c.get("Name", "").lstrip("/") for c in kitlib.docker_inspect(names)
               if (c.get("State") or {}).get("Running")]
    if running:
        run(["docker", "stop", "-t", str(timeout)] + running, check=False)
    for name in running:
        log("STOPPED " + name)


# ---------------------------------------------------------------- key sources

def usb_device(cfg):
    code, out, _ = run(["blkid", "-o", "device", "-t", cfg["usb"]], check=False)
    devices = [d for d in out.split() if d]
    if len(devices) > 1:
        raise KitError("more than one device matches %s" % cfg["usb"])
    return devices[0] if devices else None


class UsbKey:
    """Mount the key stick read-only (or read-write to write a key) in a private dir."""

    def __init__(self, cfg, write=False, wait=0):
        self.cfg, self.write, self.wait = cfg, write, wait
        self.dir = None

    def __enter__(self):
        deadline = time.time() + self.wait
        device = usb_device(self.cfg)
        while not device and time.time() < deadline:
            time.sleep(3)
            device = usb_device(self.cfg)
        if not device:
            raise KitError("key stick %s not found" % self.cfg["usb"])
        self.parent, self.dir = private_mountpoint("aibox-key-")
        opts = "nosuid,nodev,noexec," + ("rw,sync" if self.write else "ro")
        _, fstype, _ = run(["blkid", "-o", "value", "-s", "TYPE", device], check=False)
        if fstype.strip() in ("vfat", "exfat", "ntfs", "ntfs3", "msdos"):
            # these have no owner or mode on disk: the mount options decide who reads the key
            opts += ",uid=0,gid=0,umask=077"
        try:
            run(["mount", "-o", opts, device, self.dir])
        except BaseException:
            release_mountpoint(self.parent, self.dir, mounted=False)
            raise
        return os.path.join(self.dir, self.cfg["usb_file"])

    def __exit__(self, *exc):
        if self.dir:
            release_mountpoint(self.parent, self.dir)
            self.dir = None


def private_mountpoint(prefix):
    """(parent, mount dir): the parent is root-only, so the mounted file
    system stays unreachable for other users whatever its own modes are."""
    parent = tempfile.mkdtemp(prefix=prefix, dir="/run")
    os.chmod(parent, 0o700)
    path = os.path.join(parent, "mnt")
    os.mkdir(path, 0o700)
    return parent, path


def release_mountpoint(parent, path, mounted=True):
    if mounted and run(["umount", path], check=False)[0]:
        log("WARNING %s is busy: detaching it lazily" % path)
        run(["umount", "-l", path], check=False)
    for p in (path, parent):
        try:
            os.rmdir(p)
        except OSError:
            pass


def read_key_file(path):
    try:
        with open(path, "rb") as f:
            key = f.read(4097)
    except OSError as e:
        raise KitError("cannot read the key file: %s" % e.strerror)
    if not key or len(key) > 4096:
        raise KitError("the key file is empty or too large")
    return key


def write_key_file(path, key):
    tmp = path + ".new"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o400)
    try:
        os.write(fd, key)
        os.fsync(fd)
    finally:
        os.close(fd)
    return tmp


def stdin_key():
    data = sys.stdin.buffer.read(4097)
    if data.endswith(b"\n"):
        data = data[:-1]
    if not data or len(data) > 4096:
        raise KitError("no key on stdin")
    return data


def obtain_key(cfg, recovery=False, from_stdin=False, wait=0):
    if from_stdin:
        key = stdin_key()
        return normalize_recovery(key.decode("ascii", "replace")).encode() if recovery else key
    if recovery:
        return normalize_recovery(kitlib.prompt_secret("Recovery key")).encode()
    if cfg["unlock"] == "keyfile":
        return read_key_file(cfg["keyfile"])
    if cfg["unlock"] == "usb":
        with UsbKey(cfg, wait=wait) as path:
            return read_key_file(path)
    return kitlib.prompt_secret("Volume passphrase").encode("utf-8")


# ---------------------------------------------------------------- commands

def cmd_status(cfg, as_json=False):
    info = None
    luks = False
    tgt = cfg["device"] or cfg["file"]
    if tgt and os.path.exists(tgt):
        luks = cs(["isLuks", tgt], check=False)[0] == 0
        if luks:
            info = luks_info(cfg)
    mounted = is_mounted(cfg)
    state = {"target": tgt or None, "exists": bool(tgt and os.path.exists(tgt)), "luks": luks,
             "open": is_open(cfg), "mounted": mounted, "mount": cfg["mount"],
             "mount_source": mount_source(cfg["mount"]),
             "guard": "mounted" if mounted else ("immutable" if os.path.isdir(cfg["mount"])
                                                 and immutable(cfg["mount"]) else "open"),
             "unlock": cfg["unlock"], "unlock_note": cfg["unlock_note"],
             "uuid": info and info["uuid"], "keyslots": info and info["keyslots"],
             "roles": info and info["roles"], "links": link_state(cfg)}
    try:
        state["containers"] = dependent_containers(cfg)
    except KitError:
        state["containers"] = None
    if as_json:
        print(json.dumps(state, indent=2))
    else:
        for key in ("target", "luks", "open", "mounted", "mount", "guard", "unlock", "uuid"):
            print("%-10s %s" % (key, state[key]))
        for role, r in sorted((state["roles"] or {}).items()):
            print("key        %s: slot %s (%s, %s)" % (role, r["keyslot"], r["method"], r["created"]))
        for link in state["links"]:
            print("link       %s: %s" % (link["path"], link["state"]))
        print("containers %s" % " ".join(state["containers"] or []))
        if cfg["unlock_note"]:
            print("WARNING    " + cfg["unlock_note"])
    return state


def host_checks(cfg):
    """(level, message) pairs; level PASS, WARN or FAIL."""
    out = []
    code, swaps, _ = run(["swapon", "--show=NAME,TYPE", "--noheadings", "--raw"], check=False)
    plain = []
    for line in swaps.splitlines():
        name = line.split()[0] if line.split() else ""
        if not name or name.startswith("/dev/zram"):
            continue
        if name.startswith("/dev/mapper/") or name.startswith("/dev/dm-"):
            _, kind, _ = run(["lsblk", "-no", "TYPE", name], check=False)
            if "crypt" in kind:
                continue
        plain.append(name)
    out.append(("FAIL", "plaintext swap (memory with document text can land on disk): %s - "
                "swapoff it and remove it from /etc/fstab, or use zram" % ", ".join(plain))
               if plain else ("PASS", "swap: none, zram or encrypted"))
    try:
        with open("/proc/sys/kernel/core_pattern") as f:
            pattern = f.read().strip()
    except OSError:
        pattern = ""
    if pattern.startswith("|") or "/" in pattern:
        out.append(("WARN", "core dumps are collected (%s): a crash dump of a service holds document "
                    "text on the system disk; set kernel.core_pattern=core and ulimit -c 0, or "
                    "disable apport/systemd-coredump" % pattern[:60]))
    else:
        out.append(("PASS", "core dumps are not collected to disk"))
    mounted = is_mounted(cfg)
    if not mounted:
        guarded = os.path.isdir(cfg["mount"]) and immutable(cfg["mount"]) and not os.listdir(cfg["mount"])
        out.append(("PASS" if guarded else "FAIL",
                    "closed volume: mount point %s %s" % (cfg["mount"], "is empty and immutable" if guarded
                                                          else "is NOT guarded (vault.py lock)")))
    else:
        out.append(("PASS", "volume mounted at %s" % cfg["mount"]))
    for link in link_state(cfg):
        level = "PASS" if link["state"] == "linked" else "FAIL"
        out.append((level, "link %s: %s" % (link["path"], link["state"])))
    try:
        containers = kitlib.docker_containers()
        names = set(dependent_containers(cfg, containers))
        for c in containers:
            name = c.get("Name", "").lstrip("/")
            if name not in names:
                continue
            host = c.get("HostConfig") or {}
            if not host.get("ReadonlyRootfs") and "/tmp" not in (host.get("Tmpfs") or {}):
                out.append(("WARN", "%s: writable image layer without a /tmp tmpfs - temporary files "
                            "would land on the plaintext docker disk" % name))
    except KitError:
        out.append(("WARN", "docker is not reachable"))
    if cfg["unlock"] == "keyfile":
        out.append(("WARN", "AIBOX_VAULT_UNLOCK=keyfile: the key is on the system disk - only a "
                    "separately removed data disk is protected"))
    if cfg["unlock_note"]:
        out.append(("WARN", cfg["unlock_note"]))
    return out


def cmd_check(cfg):
    results = host_checks(cfg)
    for level, msg in results:
        print("%s %s" % (level, msg))
    return 1 if any(level == "FAIL" for level, _ in results) else 0


def prepare_target(cfg, yes_erase):
    if cfg["device"]:
        dev = cfg["device"]
        if not os.path.exists(dev):
            raise KitError("%s does not exist" % dev)
        import stat
        if not stat.S_ISBLK(os.stat(dev).st_mode):
            raise KitError("%s is not a block device" % dev)
        if yes_erase != dev:
            raise KitError("this erases %s: repeat it with --yes-erase %s" % (dev, dev))
        _, mounts, _ = run(["lsblk", "-nro", "MOUNTPOINT", dev], check=False)
        if mounts.strip():
            raise KitError("%s (or a partition on it) is mounted" % dev)
        root = mount_source("/") or ""
        if root and (root == dev or root.startswith(dev)):
            raise KitError("%s holds the root file system" % dev)
        return
    path = cfg["file"]
    if os.path.exists(path):
        raise KitError("%s already exists" % path)
    size = cfg["size"]
    if not re.fullmatch(r"[1-9][0-9]*[GT]", size or ""):
        raise KitError("AIBOX_VAULT_SIZE must look like 64G or 2T")
    parent = os.path.dirname(path)
    os.makedirs(parent, mode=0o700, exist_ok=True)
    need = int(size[:-1]) * (1 << (30 if size[-1] == "G" else 40))
    free = shutil.disk_usage(parent).free
    if need > free - (20 << 30):
        raise KitError("%s needs %s but only %d GiB are free on %s (20 GiB kept free)"
                       % (path, size, free >> 30, parent))
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(fd)
    code, _, err = run(["fallocate", "-l", size, path], check=False)
    if code:
        os.unlink(path)
        raise KitError("fallocate failed: %s" % err.strip())


def show_recovery(key, confirm, out_path=None, mount="/"):
    """Show the recovery key on the terminal only (never stdout: install logs
    tee it), or write it to `out_path` (0600, not on the volume) when asked."""
    if out_path:
        out_path = os.path.abspath(out_path)
        if kitlib.under(out_path, mount):
            raise KitError("the recovery key cannot be written onto the volume it opens")
        fd = os.open(out_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(format_recovery(key) + "\n")
        log("RECOVERY_KEY written to %s - move it off the box" % out_path)
        return
    try:
        tty = open("/dev/tty", "w")
    except OSError:
        raise KitError("no terminal to show the recovery key on (use --recovery-out FILE)")
    with tty:
        tty.write("\n" + "=" * 72 + "\n")
        tty.write(" RECOVERY KEY of %s - shown once, never stored on the box\n" % socket.gethostname())
        tty.write("\n     %s\n\n" % format_recovery(key))
        tty.write(" Write it on the handover sheet and keep it apart from the box.\n")
        tty.write(" It opens the volume and the backup disks when every other key is lost.\n")
        tty.write(" Without it, a lost passphrase or key stick means the data is gone.\n")
        tty.write("=" * 72 + "\n\n")
        tty.flush()
        if confirm:
            typed = kitlib.prompt_secret("Type the recovery key to confirm it is written down")
            if normalize_recovery(typed) != key:
                raise KitError("the typed key differs - nothing was changed")
            tty.write("\033[2J\033[H")
            tty.flush()


def new_unlock_key(cfg, method, key_stdin=False):
    """(key bytes, finalize callback) for a new unlock key of `method`."""
    if method == "passphrase":
        key = stdin_key() if key_stdin else \
            kitlib.prompt_secret("New volume passphrase", confirm=True).encode("utf-8")
        check_passphrase(key.decode("utf-8", "replace"))
        return key, lambda: None
    key = new_keyfile_key()
    if method == "keyfile":
        os.makedirs(os.path.dirname(cfg["keyfile"]), mode=0o700, exist_ok=True)
        tmp = write_key_file(cfg["keyfile"], key)
        return key, lambda: os.replace(tmp, cfg["keyfile"])

    def finalize():
        with UsbKey(cfg, write=True) as path:
            tmp = write_key_file(path, key)
            os.replace(tmp, path)
            os.sync()
        log("key written to the stick %s (%s) - keep it apart from the box" % (cfg["usb"], cfg["usb_file"]))
    return key, finalize


def cmd_init(cfg, yes_erase=None, no_confirm=False, key_stdin=False, recovery_out=None):
    kitlib.require_root()
    tgt = target(cfg)
    if os.path.exists(tgt) and cs(["isLuks", tgt], check=False)[0] == 0:
        raise KitError("%s is already a LUKS volume (unlock it, or erase it first)" % tgt)
    if is_mounted(cfg):
        raise KitError("%s is already a mount point" % cfg["mount"])
    if os.path.isdir(cfg["mount"]) and os.listdir(cfg["mount"]):
        raise KitError("%s is not empty" % cfg["mount"])
    if cfg["unlock_note"]:
        log("WARNING " + cfg["unlock_note"])
    if cfg["device"] and yes_erase != cfg["device"]:
        raise KitError("this erases %s: repeat it with --yes-erase %s" % (cfg["device"], cfg["device"]))
    recovery = new_recovery_key()
    show_recovery(recovery, confirm=not no_confirm, out_path=recovery_out, mount=cfg["mount"])
    unlock_key, finalize = new_unlock_key(cfg, cfg["unlock"], key_stdin)
    if cfg["unlock"] == "usb":
        finalize()  # the stick must work before the volume depends on it
        finalize = lambda: None  # noqa: E731
    prepare_target(cfg, yes_erase)
    try:
        log("FORMAT %s (LUKS2)" % tgt)
        cs(["-q", "luksFormat"] + LUKS_FORMAT + cfg["pbkdf"]
           + ["--label", LABEL, "--key-slot", "0", "--key-file", "-", tgt], key=unlock_key)
        cs(["luksAddKey", "--key-slot", "1", "--key-file", "-"] + cfg["pbkdf"] + [tgt, "{NEWKEY}"],
           key=unlock_key, new_key=recovery.encode())
        info = luks_info(cfg)
        set_role(cfg, info, "unlock", 0, cfg["unlock"])
        set_role(cfg, luks_info(cfg), "recovery", 1, "recovery-key")
        if not (test_key(cfg, unlock_key, 0) and test_key(cfg, recovery.encode(), 1)):
            raise KitError("the new keys do not open the volume")
    except BaseException:
        if cfg["file"] and os.path.exists(cfg["file"]):
            os.unlink(cfg["file"])
        raise
    finalize()
    cs(["open", "--type", "luks2", "--key-file", "-", tgt, cfg["name"]], key=unlock_key)
    run(["mkfs.ext4", "-q", "-L", LABEL, mapper(cfg)])
    guard(cfg)
    mount(cfg)
    log("VAULT_READY %s on %s, unlock=%s" % (cfg["mount"], tgt, cfg["unlock"]))
    print("Next: vault.py header-backup <file on the key/recovery medium>, vault.py migrate "
          "(if AIBOX_VAULT_LINKS is set), vault.py install-unit")


def mount(cfg):
    if is_mounted(cfg):
        return
    code, out, _ = run(["e2fsck", "-p", mapper(cfg)], check=False)
    if code >= 2:
        raise KitError("file system check of %s needs attention (e2fsck exit %d): %s"
                       % (mapper(cfg), code, out.strip()[-500:]))
    os.makedirs(cfg["mount"], exist_ok=True)
    run(["mount", "-o", "nodev,nosuid,noatime", mapper(cfg), cfg["mount"]])
    os.chmod(cfg["mount"], 0o755)
    os.makedirs(os.path.join(cfg["mount"], META_DIR), mode=0o700, exist_ok=True)


def cmd_unlock(cfg, recovery=False, key_stdin=False, start=True, wait=0):
    kitlib.require_root()
    tgt = target(cfg)
    if not is_open(cfg):
        if cs(["isLuks", tgt], check=False)[0]:
            raise KitError("%s is not a LUKS volume (vault.py init)" % tgt)
        key = obtain_key(cfg, recovery=recovery, from_stdin=key_stdin, wait=wait)
        code, _, err = cs(["open", "--type", "luks2", "--key-file", "-", tgt, cfg["name"]],
                          key=key, check=False)
        if code:
            raise KitError("the key does not open the volume" if code == 2 else
                           "cryptsetup open failed: %s" % err.strip()[-300:])
        log("OPENED %s" % tgt)
    mount(cfg)
    log("MOUNTED %s" % cfg["mount"])
    bad = [link for link in link_state(cfg) if link["state"] != "linked"]
    for link in bad:
        log("WARNING link %s: %s (vault.py migrate)" % (link["path"], link["state"]))
    if start:
        failed = start_containers(dependent_containers(cfg))
        if failed:
            raise KitError("containers did not start: %s" % ", ".join(failed))
    log("VAULT_UNLOCKED")


def cmd_lock(cfg, shutdown=False):
    kitlib.require_root()
    try:
        names = dependent_containers(cfg)
    except KitError:
        names = []
    stop_containers(names)
    run(["sync"], check=False)
    if is_mounted(cfg):
        code, _, err = run(["umount", cfg["mount"]], check=False)
        if code:
            _, users, users_err = run(["fuser", "-vm", cfg["mount"]], check=False)
            msg = "cannot unmount %s: %s\n%s" % (cfg["mount"], err.strip(), (users + users_err)[-1500:])
            if shutdown:
                log("WARNING " + msg)
                return
            raise KitError(msg)
    if is_open(cfg):
        cs(["close", cfg["name"]], check=not shutdown)
    guard(cfg, quiet=True)
    log("VAULT_LOCKED")


def cmd_boot(cfg):
    kitlib.require_root()
    if cfg["unlock_note"]:
        log("WARNING " + cfg["unlock_note"])
    if not is_mounted(cfg):
        guard(cfg, quiet=True)
    if cfg["unlock"] == "passphrase":
        log("VAULT_LOCKED: unlock with 'sudo aibox-vault unlock' (passphrase)")
        return
    cmd_unlock(cfg, wait=cfg["usb_wait"])


def rsync_same(src, dst):
    _, out, _ = run(["rsync", "-aHAXn", "--numeric-ids", "--delete", "--itemize-changes",
                     src + "/", dst + "/"])
    return [line for line in out.splitlines() if line.strip()]


def cmd_migrate(cfg):
    kitlib.require_root()
    if not is_mounted(cfg):
        raise KitError("unlock the volume first")
    todo = [link for link in link_state(cfg) if link["state"] != "linked"]
    if not todo:
        log("MIGRATE nothing to do")
        return
    for link in todo:
        if link["state"] in ("wrong-link", "not-a-directory"):
            raise KitError("%s is %s - fix it by hand" % (link["path"], link["state"]))
    names = dependent_containers(cfg)
    stop_containers(names)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    try:
        for link in todo:
            path = link["path"]
            dest = os.path.join(cfg["mount"], os.path.basename(path))
            if link["state"] == "plaintext-dir":
                if os.path.exists(dest) and os.listdir(dest):
                    raise KitError("%s already holds data" % dest)
                log("COPY %s -> %s" % (path, dest))
                run(["rsync", "-aHAX", "--numeric-ids", path + "/", dest + "/"])
                diff = rsync_same(path, dest)
                if diff:
                    raise KitError("copy of %s differs: %s" % (path, diff[:5]))
                os.rename(path, "%s.pre-vault-%s" % (path, stamp))
            else:
                os.makedirs(dest, mode=0o700, exist_ok=True)
            os.symlink(os.path.relpath(dest, os.path.dirname(path)), path)
            log("LINKED %s -> %s" % (path, os.readlink(path)))
    finally:
        failed = start_containers(names)
    if failed:
        raise KitError("containers did not start: %s" % ", ".join(failed))
    log("MIGRATED; the plaintext copies stay as *.pre-vault-%s until vault.py wipe-plaintext" % stamp)


def plaintext_copies(cfg):
    out = []
    for path in cfg["links"]:
        parent, base = os.path.dirname(path), os.path.basename(path)
        if os.path.isdir(parent):
            out += [os.path.join(parent, n) for n in sorted(os.listdir(parent))
                    if n.startswith(base + ".pre-vault-")]
    return out


def cmd_wipe_plaintext(cfg, confirm):
    kitlib.require_root()
    copies = plaintext_copies(cfg)
    if not copies:
        log("no plaintext copies")
        return
    for path in copies:
        print(path)
    if not confirm:
        raise KitError("repeat with --confirm to delete these")
    filesystems = set()
    for path in copies:
        filesystems.add(run(["df", "--output=target", path])[1].splitlines()[-1].strip())
        shutil.rmtree(path)
        log("DELETED %s" % path)
    for fs in sorted(filesystems):
        run(["fstrim", "-v", fs], check=False)
    print("Deleted. On an SSD the old blocks may survive until the drive reuses them; "
          "the volume must hold any data that is still sensitive.")


def cmd_rotate(cfg, role, method=None, key_stdin=False, recovery=False, recovery_out=None):
    kitlib.require_root()
    info = luks_info(cfg)
    if not info:
        raise KitError("%s is not a LUKS volume" % target(cfg))
    old = info["roles"].get(role)
    if not old:
        raise KitError("no %s key is recorded in the header" % role)
    method = method or (cfg["unlock"] if role == "unlock" else "recovery-key")
    if role == "unlock" and method not in UNLOCK_MODES:
        raise KitError("--method must be one of %s" % ", ".join(UNLOCK_MODES))
    if role == "unlock" and method == "passphrase" and key_stdin:
        raise KitError("--key-stdin cannot carry both the current and the new passphrase")
    if recovery or key_stdin or cfg["unlock"] != "passphrase":
        current = obtain_key(cfg, recovery=recovery, from_stdin=key_stdin)
    else:
        typed = kitlib.prompt_secret("Current passphrase or recovery key")
        current = typed.encode("utf-8")
        try:
            as_recovery = normalize_recovery(typed).encode()
            if test_key(cfg, as_recovery):
                current = as_recovery
        except KitError:
            pass
    if not test_key(cfg, current):
        raise KitError("the current key does not open the volume")
    if role == "recovery":
        new = new_recovery_key()
        show_recovery(new, confirm=sys.stdin.isatty() and not recovery_out, out_path=recovery_out,
                      mount=cfg["mount"])
        new_key, finalize = new.encode(), (lambda: None)
    else:
        new_key, finalize = new_unlock_key(cfg, method)
    slot = free_slot(info)
    cs(["luksAddKey", "--key-slot", str(slot), "--key-file", "-"] + cfg["pbkdf"]
       + [target(cfg), "{NEWKEY}"], key=current, new_key=new_key)
    try:
        if not test_key(cfg, new_key, slot):
            raise KitError("the new key does not open the volume; nothing changed")
        finalize()
    except BaseException:
        cs(["luksKillSlot", "--key-file", "-", target(cfg), str(slot)], key=current, check=False)
        raise
    set_role(cfg, info, role, slot, method)
    cs(["luksKillSlot", "--key-file", "-", target(cfg), str(old["keyslot"])], key=new_key)
    log("ROTATED %s: slot %d -> %d (%s). Header backups made before now still accept the "
        "old key: replace them." % (role, old["keyslot"], slot, method))
    if role == "recovery":
        log("WARNING every backup disk still opens with the old recovery key: attach each one and run "
            "'aibox-backup rekey' (backups to a disk are refused until then)")
    if role == "unlock" and method != cfg["unlock"]:
        log("WARNING set AIBOX_VAULT_UNLOCK=%s in /etc/aibox/standalone.conf" % method)


def cmd_header_backup(cfg, path):
    kitlib.require_root()
    path = os.path.abspath(path)
    if os.path.exists(path):
        raise KitError("%s exists" % path)
    if kitlib.under(path, cfg["mount"]):
        raise KitError("a header backup on the volume itself is useless")
    old = os.umask(0o077)
    try:
        cs(["luksHeaderBackup", target(cfg), "--header-backup-file", path])
    finally:
        os.umask(old)
    os.chmod(path, 0o600)
    log("HEADER_BACKUP %s - it opens with today's keys even after a rotation: keep it with "
        "the recovery key, not on the box" % path)


def cmd_erase(cfg, confirm):
    kitlib.require_root()
    host = socket.gethostname()
    if confirm != host:
        raise KitError("this destroys every key of the volume for good: repeat with --confirm %s" % host)
    cmd_lock(cfg)
    cs(["-q", "luksErase", target(cfg)])
    if cfg["unlock"] == "keyfile" and os.path.exists(cfg["keyfile"]):
        os.unlink(cfg["keyfile"])
    log("ERASED: no key opens %s any more. Destroy its header backups too; backup disks are "
        "separate volumes and are not affected." % target(cfg))


def unit_text():
    return """[Unit]
Description=ViVeSec AI Box encrypted data volume
After=docker.service local-fs.target
Wants=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/bin/python3 {lib}/vault.py boot
ExecStop=/usr/bin/python3 {lib}/vault.py lock --shutdown
TimeoutStartSec=20min
TimeoutStopSec=5min

[Install]
WantedBy=multi-user.target
""".format(lib=LIB_DIR)


def install_kit_files():
    here = os.path.dirname(os.path.abspath(__file__))
    os.makedirs(LIB_DIR, mode=0o755, exist_ok=True)
    for name in KIT_FILES:
        src = os.path.join(here, name)
        if os.path.exists(src):
            shutil.copyfile(src, os.path.join(LIB_DIR, name))
            os.chmod(os.path.join(LIB_DIR, name), 0o755)
    for tool in ("vault", "backup", "timesync"):
        wrapper = "/usr/local/sbin/aibox-" + tool
        with open(wrapper, "w") as f:
            f.write("#!/bin/sh\nexec /usr/bin/python3 %s/%s.py \"$@\"\n" % (LIB_DIR, tool))
        os.chmod(wrapper, 0o755)


def cmd_install_unit(cfg):
    kitlib.require_root()
    conf = os.environ.get("AIBOX_STANDALONE_CONF") or kitlib.CONF_PATH
    if not os.path.exists(conf):
        raise KitError("%s is missing: the unit reads its settings from there" % conf)
    target(cfg)
    install_kit_files()
    with open(UNIT_PATH, "w") as f:
        f.write(unit_text())
    run(["systemctl", "daemon-reload"])
    run(["systemctl", "enable", "aibox-vault.service"])
    # Mark it active for this boot so the shutdown locks cleanly (no unlock here).
    if is_mounted(cfg):
        run(["systemctl", "start", "aibox-vault.service"], check=False)
    log("UNIT_INSTALLED %s (unlock=%s); tools in %s, aibox-vault / aibox-backup / aibox-timesync"
        % (UNIT_PATH, cfg["unlock"], LIB_DIR))


def main(argv):
    p = argparse.ArgumentParser(prog="vault.py", description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd")
    sub.add_parser("status").add_argument("--json", action="store_true")
    sub.add_parser("check")
    a = sub.add_parser("init")
    a.add_argument("--yes-erase")
    a.add_argument("--no-confirm", action="store_true", help="do not ask to retype the recovery key")
    a.add_argument("--key-stdin", action="store_true", help="passphrase mode: new passphrase on stdin")
    a.add_argument("--recovery-out", help="write the recovery key to this file instead of the terminal")
    a = sub.add_parser("unlock")
    a.add_argument("--recovery", action="store_true")
    a.add_argument("--key-stdin", action="store_true")
    a.add_argument("--no-start", action="store_true")
    sub.add_parser("lock").add_argument("--shutdown", action="store_true")
    sub.add_parser("boot")
    sub.add_parser("migrate")
    sub.add_parser("wipe-plaintext").add_argument("--confirm", action="store_true")
    a = sub.add_parser("rotate")
    a.add_argument("role", choices=("unlock", "recovery"))
    a.add_argument("--method", choices=UNLOCK_MODES)
    a.add_argument("--key-stdin", action="store_true", help="current key on stdin")
    a.add_argument("--recovery", action="store_true", help="authorize with the recovery key")
    a.add_argument("--recovery-out", help="new recovery key to this file instead of the terminal")
    sub.add_parser("header-backup").add_argument("file")
    sub.add_parser("erase").add_argument("--confirm")
    sub.add_parser("install-unit")
    args = p.parse_args(argv[1:])
    if not args.cmd:
        print(__doc__)
        return 2
    cfg = settings()
    if args.cmd == "status":
        cmd_status(cfg, args.json)
    elif args.cmd == "check":
        return cmd_check(cfg)
    elif args.cmd == "init":
        cmd_init(cfg, args.yes_erase, args.no_confirm, args.key_stdin, args.recovery_out)
    elif args.cmd == "unlock":
        cmd_unlock(cfg, args.recovery, args.key_stdin, not args.no_start)
    elif args.cmd == "lock":
        cmd_lock(cfg, args.shutdown)
    elif args.cmd == "boot":
        cmd_boot(cfg)
    elif args.cmd == "migrate":
        cmd_migrate(cfg)
    elif args.cmd == "wipe-plaintext":
        cmd_wipe_plaintext(cfg, args.confirm)
    elif args.cmd == "rotate":
        cmd_rotate(cfg, args.role, args.method, args.key_stdin, args.recovery, args.recovery_out)
    elif args.cmd == "header-backup":
        cmd_header_backup(cfg, args.file)
    elif args.cmd == "erase":
        cmd_erase(cfg, args.confirm)
    elif args.cmd == "install-unit":
        cmd_install_unit(cfg)
    return 0


if __name__ == "__main__":
    sys.exit(kitlib.main_guard(main, sys.argv))
