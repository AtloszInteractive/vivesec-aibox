"""Encrypted storage manager for the AIBox (J1: cryptsetup LUKS2 + presence lock).

The AIBox keeps the indexed data on a LUKS2-encrypted volume. The storage key is
supplied by the ViVeSecBox (manually in J1, via init/commit in J2) and is ALWAYS
passed to cryptsetup over STDIN, never on the argv -- so it never appears in the
process list.

Lifecycle:
    unlock(key) : luksOpen <device> <name> (key on stdin) -> mount at <mount>
    lock()      : umount <mount> -> luksClose <name>

Two modes (env ADAPTER_STORAGE_MODE):
    off  (default) : no encryption layer; the box is always "unlocked". Keeps the
                     current containerized deployment working until the real
                     device is wired. lock()/unlock() are no-ops.
    luks           : drive cryptsetup/mount on a real block device or a loopback
                     backing file (cryptsetup auto-attaches a loop device for a
                     plain file).

luks-mode env:
    ADAPTER_LUKS_DEVICE : block device or backing file (e.g. /dev/nvme0n1p2)
    ADAPTER_LUKS_NAME   : dm-crypt mapper name (default vivesec_data)
    ADAPTER_LUKS_MOUNT  : mountpoint for the decrypted filesystem (e.g. /data)
    ADAPTER_LUKS_FORMAT : "1" to luksFormat + mkfs on first unlock when the device
                          is not yet a LUKS container (J1 manual bootstrap)
    ADAPTER_LUKS_FSTYPE : filesystem to create on first init (default ext4)

The command runner is injectable so the state machine can be unit-tested without
a real device. Pure stdlib (subprocess), Py3.8+.
"""
import os
import subprocess
import threading

_TRUE = ("1", "true", "yes", "on")


class StorageError(Exception):
    pass


def _default_runner(cmd, input_bytes=None, timeout=120):
    p = subprocess.run(cmd, input=input_bytes,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       timeout=timeout)
    return p.returncode, p.stdout or b"", p.stderr or b""


class StorageManager:
    def __init__(self, mode="off", device="", name="vivesec_data", mount="",
                 allow_format=False, fs_type="ext4", runner=None):
        self.mode = (mode or "off").strip().lower()
        self.device = device
        self.name = name
        self.mount = mount
        self.allow_format = bool(allow_format)
        self.fs_type = fs_type or "ext4"
        self._run = runner or _default_runner
        self._cmd_lock = threading.Lock()
        # In 'off' mode the box has no LUKS layer and is always available.
        self._unlocked = (self.mode == "off")

    @classmethod
    def from_env(cls, env=None):
        env = env or os.environ
        return cls(
            mode=env.get("ADAPTER_STORAGE_MODE", "off"),
            device=env.get("ADAPTER_LUKS_DEVICE", ""),
            name=env.get("ADAPTER_LUKS_NAME", "vivesec_data"),
            mount=env.get("ADAPTER_LUKS_MOUNT", ""),
            allow_format=(env.get("ADAPTER_LUKS_FORMAT", "") or "").strip().lower() in _TRUE,
            fs_type=env.get("ADAPTER_LUKS_FSTYPE", "ext4"),
        )

    @property
    def mapper(self):
        return "/dev/mapper/" + self.name

    def is_locked(self):
        return not self._unlocked

    def is_unlocked(self):
        return self._unlocked

    # -- cryptsetup / mount helpers -----------------------------------------
    def _cs(self, args, key=None):
        return self._run(["cryptsetup"] + args,
                         input_bytes=(key.encode("utf-8") if key is not None else None))

    def _is_luks(self):
        rc, _, _ = self._cs(["isLuks", self.device])
        return rc == 0

    def _is_active(self):
        rc, _, _ = self._cs(["status", self.name])
        return rc == 0

    def _is_mounted(self):
        if not self.mount:
            return False
        try:
            with open("/proc/mounts") as f:
                for line in f:
                    parts = line.split()
                    if len(parts) >= 2 and parts[1] == self.mount:
                        return True
        except OSError:
            return False
        return False

    @staticmethod
    def _decode(err):
        return (err or b"").decode("utf-8", "replace").strip()

    # -- lifecycle -----------------------------------------------------------
    def unlock(self, storage_key):
        """Open (and on first run, format) the encrypted volume and mount it."""
        if self.mode == "off":
            self._unlocked = True
            return
        if not storage_key:
            raise StorageError("storage_key required")
        if not self.device:
            raise StorageError("ADAPTER_LUKS_DEVICE not configured")
        with self._cmd_lock:
            first_init = False
            if not self._is_luks():
                if not self.allow_format:
                    raise StorageError(
                        "device %s is not a LUKS container "
                        "(set ADAPTER_LUKS_FORMAT=1 to initialize)" % self.device)
                rc, _, err = self._cs(
                    ["-q", "--type", "luks2", "luksFormat", self.device, "-"],
                    key=storage_key)
                if rc != 0:
                    raise StorageError("luksFormat failed: %s" % self._decode(err))
                first_init = True
            if not self._is_active():
                rc, _, err = self._cs(["luksOpen", self.device, self.name, "-"],
                                      key=storage_key)
                if rc != 0:
                    raise StorageError("luksOpen failed: %s" % self._decode(err))
            if first_init:
                rc, _, err = self._run(["mkfs." + self.fs_type, "-q", self.mapper])
                if rc != 0:
                    raise StorageError("mkfs failed: %s" % self._decode(err))
            if self.mount and not self._is_mounted():
                try:
                    os.makedirs(self.mount, exist_ok=True)
                except OSError:
                    pass
                rc, _, err = self._run(["mount", self.mapper, self.mount])
                if rc != 0:
                    raise StorageError("mount failed: %s" % self._decode(err))
            self._unlocked = True

    def lock(self):
        """Unmount and close the encrypted volume (idempotent / best effort)."""
        if self.mode == "off":
            return
        with self._cmd_lock:
            if self.mount and self._is_mounted():
                self._run(["umount", self.mount])
            if self._is_active():
                self._cs(["luksClose", self.name])
            self._unlocked = False

    def status(self):
        mounted = True if self.mode == "off" else self._unlocked
        return {"mode": self.mode, "locked": self.is_locked(),
                "device": self.device, "mapper": self.name,
                "mount": self.mount, "mounted": mounted}
