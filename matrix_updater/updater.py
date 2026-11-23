"""Matrix AI update client on the Spark host (E07 M4).

    python3 updater.py check      refresh the signed metadata, download a newer release
    python3 updater.py request    act on the administrator's request (install / rollback)
    python3 updater.py status     print the state
    python3 updater.py bootstrap KIT   first installation from a release kit (USB stick)

`check` runs from a systemd timer. It trusts only the root metadata of the
installed kit (TUF): a release is offered when the channel names a newer
version than the installed one and the installed one is at least the release's
`min_from`. Images the box already has are not downloaded again. Nothing is
installed until an administrator approves it in the web UI: the adapter writes
`/data/updates/request.json`, a systemd path unit runs `request`.

Installing: the downloaded files are hashed again, the data is copied aside
when the release says its format changes (`compat.snapshot`), the images are
loaded, the new kit replaces the old one (kept as .prev) and its installer runs
every phase from the prebuilt images. When that fails, the previous kit and
images come back and the previous installer runs; the data copy is put back
only when the administrator asks for it (what was written since is lost).

The state goes to STATE_DIR/state.json and, without local paths, to the
adapter's data directory (`/data/updates/state.json` inside the container) for
the administration page.

Settings come from /etc/vivesec-matrix/matrix.conf:
    MATRIX_UPDATE           on | off (default off; anything else -> off)
    MATRIX_UPDATE_URL       https:// base of the repository (metadata/ and targets/ under it)
    MATRIX_UPDATE_CHANNEL   stable | pilot | labor (default stable; anything else -> off)
"""
import argparse
import datetime as dt
import hashlib
import json
import os
import re
import secrets
import shlex
import shutil
import stat
import subprocess
import sys
import tarfile
import time
from pathlib import Path

CONF_FILE = Path("/etc/vivesec-matrix/matrix.conf")
ROOT = Path("/var/lib/vivesec-matrix")
STATE_DIR = ROOT / "updater"
SHARE_DIR = ROOT / "adapter" / "updates"
KIT_DIR = Path("/opt/vivesec-matrix-kit")
CHANNELS = ("stable", "pilot", "labor")
VERSION = re.compile(r"^(\d{2})\.(\d{2})\.(\d{1,3})(?:\.(\d{1,4}))?(-dev\b.*)?$")
AGENT_VERSION = "1"
# What a data copy holds (below ROOT), and the containers stopped while it is taken.
DATA_DIRS = ("adapter", "rag", "auth", "graph", "neo4j")
CONTAINERS = ("vivesec-ui", "vivesec-adapter", "vivesec-graph", "vivesec-rag", "vivesec-neo4j", "vivesec-auth")
INSTALL_TIMEOUT = 3600


class UpdateError(Exception):
    pass


def utcnow():
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def version_key(text):
    """Ordering key of YY.MM.N[.B]; a development label of a version sorts before its release."""
    match = VERSION.match(text or "")
    if not match:
        return None
    parts = [int(part) if part is not None else -1 for part in match.groups()[:4]]
    if match.group(5):
        parts[3] = -2
    return tuple(parts)


def parse_conf(text):
    values = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, raw = line.partition("=")
        try:
            values[key.strip()] = " ".join(shlex.split(raw, comments=True))
        except ValueError:
            continue
    return values


class Settings:
    def __init__(self, values, state_dir=STATE_DIR, share_dir=SHARE_DIR, kit_dir=KIT_DIR, allow_http=False,
                 root=ROOT):
        self.enabled = values.get("MATRIX_UPDATE", "off").strip().lower() == "on"
        self.channel = values.get("MATRIX_UPDATE_CHANNEL", "stable").strip().lower()
        self.url = values.get("MATRIX_UPDATE_URL", "").strip().rstrip("/")
        self.problem = None
        scheme = "https?" if allow_http else "https"
        if self.channel not in CHANNELS:
            self.problem = "unknown MATRIX_UPDATE_CHANNEL %r" % self.channel
        elif self.enabled and not re.match(r"^%s://[A-Za-z0-9.-]+(:\d+)?(/[A-Za-z0-9._~/-]*)?$" % scheme, self.url):
            # http:// only in the tests: the metadata is signed, but a plain-text
            # channel would still show every box's checks to the network.
            self.problem = "MATRIX_UPDATE_URL must be an https:// URL"
        if self.problem:
            self.enabled = False
        self.state_dir = Path(state_dir)
        self.share_dir = Path(share_dir) if share_dir else None
        self.kit_dir = Path(kit_dir)
        self.root = Path(root)

    @classmethod
    def load(cls):
        text = CONF_FILE.read_text(encoding="utf-8") if CONF_FILE.is_file() else ""
        return cls(parse_conf(text))


def installed_version(kit_dir):
    try:
        label = json.loads((Path(kit_dir) / "KIT.json").read_text(encoding="utf-8")).get("label", "")
    except (OSError, ValueError):
        return None
    return label or None


def box_id(state_dir):
    """A random identifier of this box for the update host's access log (no customer data)."""
    path = Path(state_dir) / "box-id"
    if not path.is_file():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("mx-" + secrets.token_hex(8) + "\n")
    return path.read_text().strip()


def docker_has_image(image_id):
    result = subprocess.run(("docker", "image", "inspect", "-f", "{{.Id}}", image_id),
                            capture_output=True, text=True)
    return result.returncode == 0 and result.stdout.strip() == image_id


def write_json(path, data, mode=0o644):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name("." + path.name + ".tmp")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    os.replace(temp, path)


def read_state(settings):
    try:
        return json.loads((settings.state_dir / "state.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"format": 1, "status": "never"}


# The shared folder lies in the adapter's data directory, which the adapter
# container writes as root: anything in it may be a symlink, a FIFO or huge.
# On the box every access goes through a descriptor of the folder itself,
# without following links; files are created under fresh random names.
HARDENED = os.name == "posix"
REQUEST_MAX = 4096


class SharedFolder:
    def __init__(self, path):
        self.path = Path(path)
        self.fd = None

    def __enter__(self):
        if not HARDENED:
            self.path.mkdir(parents=True, exist_ok=True)
            return self
        try:
            os.mkdir(self.path, 0o755)
        except FileExistsError:
            pass
        try:
            self.fd = os.open(self.path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        except OSError as e:
            raise UpdateError("%s is not a plain folder: %s" % (self.path, e))
        if not stat.S_ISDIR(os.fstat(self.fd).st_mode):
            os.close(self.fd)
            raise UpdateError("%s is not a folder" % self.path)
        return self

    def __exit__(self, *exc):
        if self.fd is not None:
            os.close(self.fd)
        return False

    def write(self, name, data):
        text = (json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
        temp = ".%s.%s.tmp" % (name, secrets.token_hex(8))
        if not HARDENED:
            (self.path / temp).write_bytes(text)
            os.replace(self.path / temp, self.path / name)
            return
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644, dir_fd=self.fd)
        try:
            os.fchmod(fd, 0o644)
            os.write(fd, text)
        finally:
            os.close(fd)
        os.replace(temp, name, src_dir_fd=self.fd, dst_dir_fd=self.fd)

    def take(self, name):
        """Read and remove a small regular file; None when there is none."""
        if not HARDENED:
            path = self.path / name
            try:
                data = path.read_bytes()
            except FileNotFoundError:
                return None
            path.unlink()
        else:
            try:
                fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=self.fd)
            except FileNotFoundError:
                return None
            except OSError as e:  # a symlink (ELOOP) or something worse: drop it
                try:
                    os.unlink(name, dir_fd=self.fd)
                except OSError:
                    pass
                raise UpdateError("%s is not a plain file: %s" % (name, e))
            try:
                if not stat.S_ISREG(os.fstat(fd).st_mode):
                    raise UpdateError("%s is not a plain file" % name)
                data = os.read(fd, REQUEST_MAX + 1)
            finally:
                os.close(fd)
                try:
                    os.unlink(name, dir_fd=self.fd)
                except OSError:
                    # A directory (or anything unlink refuses) is moved out of
                    # the way, so the path unit does not fire for it again.
                    try:
                        os.rename(name, ".refused-%s" % secrets.token_hex(8), src_dir_fd=self.fd,
                                  dst_dir_fd=self.fd)
                    except OSError:
                        pass
        if len(data) > REQUEST_MAX:
            raise UpdateError("%s is too large" % name)
        return data


def save_state(settings, state):
    write_json(settings.state_dir / "state.json", state, 0o600)
    if settings.share_dir is not None:
        public = dict(state)
        if isinstance(public.get("available"), dict):
            public["available"] = {key: value for key, value in public["available"].items() if key != "files"}
        try:
            with SharedFolder(settings.share_dir) as shared:
                shared.write("state.json", public)
        except (OSError, UpdateError) as e:
            # The private state is saved; only the administration page misses it.
            print("updater: the shared state was not written: %s" % e, file=sys.stderr)


def make_updater(settings, user_agent, trusted_root=None):
    """The TUF client. `trusted_root` (bytes) starts the trust anew from that root,
    as a first installation does; otherwise the stored metadata goes on."""
    from tuf.ngclient import Updater, UpdaterConfig

    metadata = settings.state_dir / "metadata"
    if trusted_root is not None:
        shutil.rmtree(metadata, ignore_errors=True)
    metadata.mkdir(parents=True, exist_ok=True)
    if trusted_root is not None:
        (metadata / "root.json").write_bytes(trusted_root)
    if not (metadata / "root.json").is_file():
        trusted = settings.kit_dir / "src" / "matrix_updater" / "trusted_root.json"
        if not trusted.is_file():
            raise UpdateError("no trusted root: %s missing" % trusted)
        (metadata / "root.json").write_bytes(trusted.read_bytes())
    targets = settings.state_dir / "targets"
    targets.mkdir(parents=True, exist_ok=True)
    return Updater(metadata_dir=str(metadata), metadata_base_url=settings.url + "/metadata/",
                   target_dir=str(targets), target_base_url=settings.url + "/targets/",
                   config=UpdaterConfig(app_user_agent=user_agent))


def fetch_json(updater, target_path):
    info = updater.get_targetinfo(target_path)
    if info is None:
        return None, None
    path = updater.find_cached_target(info) or updater.download_target(info)
    return json.loads(Path(path).read_text(encoding="utf-8")), path


def fetch_file(updater, target_path):
    info = updater.get_targetinfo(target_path)
    if info is None:
        raise UpdateError("the release names %s, which the repository does not list" % target_path)
    return updater.find_cached_target(info) or updater.download_target(info)


def check(settings, has_image=docker_has_image):
    """One check against the channel; returns the new state."""
    with locked(settings, wait=False) as acquired:
        if not acquired:
            return read_state(settings)
        return _check(settings, has_image)


class locked:
    """One updater at a time: the timer's check skips while an installation runs."""

    def __init__(self, settings, wait):
        self.path = settings.state_dir / "lock"
        self.wait = wait
        self.handle = None

    def __enter__(self):
        try:
            import fcntl
        except ImportError:  # not a Linux host (the tests on a workstation)
            return True
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = open(self.path, "w")
        try:
            fcntl.flock(self.handle, fcntl.LOCK_EX | (0 if self.wait else fcntl.LOCK_NB))
        except BlockingIOError:
            return False
        return True

    def __exit__(self, *exc):
        if self.handle:
            self.handle.close()
        return False


def _check(settings, has_image):
    from tuf.api.exceptions import RepositoryError, DownloadError

    state = read_state(settings)
    if state.get("status") in ("installing", "rolling_back"):
        # Nobody holds the lock, so the request that wrote this died (power,
        # timeout, OOM): the services may be half replaced. It stays visible
        # until an administrator installs again or goes back.
        last = state.get("last") or {}
        last["failed_at"] = utcnow()
        state.update({"status": "failed", "interrupted": True, "last": last or None, "step": None,
                      "error": "interrupted while %s (%s)" % (state["status"].replace("_", " "),
                                                               state.get("step") or "?")})
        save_state(settings, state)
        return state
    if state.get("interrupted"):
        # An administrator has to go back first; nothing new is offered.
        return state
    # Neither the new nor the previous version passed its checks. The box is
    # only offered a fix: a release newer than the one that failed. Until one
    # comes (or while the channel cannot be read) the state stays as it is.
    broken = state.get("status") == "broken"
    installed = installed_version(settings.kit_dir)
    if broken:
        state["checked_at"] = utcnow()
    else:
        state.update({"format": 1, "channel": settings.channel, "installed": installed, "checked_at": utcnow()})
    if not settings.enabled:
        if not broken:
            state.update({"status": "off", "error": settings.problem, "available": None})
        save_state(settings, state)
        return state
    agent = "vivesec-matrix-updater/%s box=%s v=%s s=%s" % (
        AGENT_VERSION, box_id(settings.state_dir), installed or "unknown", state.get("status", "never"))
    kept = dict(state)
    try:
        updater = make_updater(settings, agent)
        updater.refresh()
        pointer, _ = fetch_json(updater, "channels/%s.json" % settings.channel)
        current = version_key(installed)
        floor = current
        if broken:
            keys = [key for key in (current, version_key((state.get("last") or {}).get("version")))
                    if key is not None]
            floor = max(keys) if keys else None
        if pointer is None or floor is not None and version_key(pointer.get("version")) <= floor:
            if broken:
                save_state(settings, state)
                return state
            state.update({"status": "current", "available": None, "error": None})
            save_state(settings, state)
            return state
        release, _ = fetch_json(updater, pointer["release"])
        if release is None or release.get("version") != pointer.get("version"):
            raise UpdateError("the channel points to a release the repository does not hold")
        offered = {"version": release["version"], "label": release.get("label"), "notes": release.get("notes") or {},
                   "security": bool(release.get("security")), "compat": release.get("compat") or {},
                   "published": release.get("published"), "min_from": release.get("min_from")}
        if release.get("min_from") and (current is None or current < version_key(release["min_from"])):
            if broken:
                save_state(settings, state)
                return state
            state.update({"status": "blocked", "available": offered, "error":
                          "%s needs %s or newer installed first" % (release["version"], release["min_from"])})
            save_state(settings, state)
            return state
        needed = [image for image in release.get("images") or [] if not has_image(image["id"])]
        offered["download_bytes"] = release["kit"]["length"] + sum(image["length"] for image in needed)
        state.update({"status": "downloading", "available": offered, "error": None})
        save_state(settings, state)
        files = {"release": str(fetch_json(updater, pointer["release"])[1]),
                 "kit": str(fetch_file(updater, release["kit"]["target"])), "images": {}}
        for image in needed:
            files["images"][image["name"]] = str(fetch_file(updater, image["target"]))
        offered["files"] = files
        offered["downloaded_at"] = utcnow()
        state.update({"status": "ready", "available": offered, "error": None})
        if broken:
            state.update({"format": 1, "channel": settings.channel, "installed": installed})
    except (RepositoryError, DownloadError, UpdateError, OSError, ValueError, KeyError) as e:
        # A failed check never touches the running box; it is retried by the timer.
        if broken:
            state = kept
        else:
            state.update({"status": "error", "error": "%s: %s" % (type(e).__name__, e)})
    save_state(settings, state)
    return state


class Host:
    """The commands an installation runs on the box; replaced in the tests."""

    def __init__(self, log_path=None):
        self.log_path = log_path

    def run(self, args, timeout=INSTALL_TIMEOUT):
        log = open(self.log_path, "a", encoding="utf-8") if self.log_path else subprocess.DEVNULL
        try:
            if self.log_path:
                log.write("$ %s\n" % " ".join(str(arg) for arg in args))
                log.flush()
            return subprocess.run([str(arg) for arg in args], stdout=log, stderr=subprocess.STDOUT,
                                  timeout=timeout).returncode
        except (OSError, subprocess.TimeoutExpired) as e:
            if self.log_path:
                log.write("FAILED %s\n" % e)
            return 1
        finally:
            if self.log_path:
                log.close()

    def image_id(self, ref):
        result = subprocess.run(("docker", "image", "inspect", "-f", "{{.Id}}", ref), capture_output=True, text=True)
        return result.stdout.strip() if result.returncode == 0 else None


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def set_step(settings, state, status, step):
    state.update({"status": status, "step": step, "step_at": utcnow()})
    save_state(settings, state)


def tree_bytes(path):
    total = 0
    for directory, _, names in os.walk(path):
        for name in names:
            try:
                total += os.lstat(os.path.join(directory, name)).st_size
            except OSError:
                pass
    return total


def take_snapshot(settings, host, version):
    """Copy the data aside with the services stopped; they are started again after."""
    sources = [settings.root / name for name in DATA_DIRS if (settings.root / name).is_dir()]
    needed = sum(tree_bytes(path) for path in sources)
    backups = settings.root / "backups"
    backups.mkdir(parents=True, exist_ok=True)
    os.chmod(backups, 0o700)
    if shutil.disk_usage(backups).free < needed * 1.2 + (1 << 30):
        raise UpdateError("not enough disk for the data copy (%d MB needed)" % (needed >> 20))
    target = backups / ("pre-%s-%s-%s" % (version, time.strftime("%Y%m%d-%H%M%S"), secrets.token_hex(3)))
    target.mkdir()
    for name in CONTAINERS:
        host.run(["docker", "stop", "-t", "60", name], timeout=120)
    try:
        for source in sources:
            if host.run(["cp", "-a", "--reflink=auto", source, target / source.name]):
                raise UpdateError("copying %s failed" % source)
    finally:
        for name in reversed(CONTAINERS):
            host.run(["docker", "start", name], timeout=120)
    return str(target)


def restore_snapshot(settings, host, snapshot):
    """Put a data copy back; the data it replaces is kept beside it, not deleted."""
    aside = settings.root / "backups" / ("replaced-%s-%s" % (time.strftime("%Y%m%d-%H%M%S"), secrets.token_hex(3)))
    aside.mkdir(parents=True)
    for name in CONTAINERS:
        host.run(["docker", "stop", "-t", "60", name], timeout=120)
    for source in sorted(Path(snapshot).iterdir()):
        current = settings.root / source.name
        if current.exists():
            os.replace(current, aside / source.name)
        if host.run(["cp", "-a", "--reflink=auto", source, current]):
            raise UpdateError("restoring %s failed" % source.name)
    return str(aside)


def unpack_kit(settings, kit_tgz):
    """Swap the kit directory; the previous one stays as <kit>.prev for a rollback."""
    new = settings.kit_dir.with_name(settings.kit_dir.name + ".new")
    prev = settings.kit_dir.with_name(settings.kit_dir.name + ".prev")
    shutil.rmtree(new, ignore_errors=True)
    new.mkdir(parents=True)
    with tarfile.open(kit_tgz, "r:gz") as archive:
        archive.extractall(new, filter="data")
    if not (new / "KIT.json").is_file() or not (new / "src" / "scripts" / "spark" / "spark_install.py").is_file():
        shutil.rmtree(new, ignore_errors=True)
        raise UpdateError("the release kit has no KIT.json or installer")
    shutil.rmtree(prev, ignore_errors=True)
    if settings.kit_dir.exists():
        os.replace(settings.kit_dir, prev)
    os.replace(new, settings.kit_dir)


def restore_kit(settings):
    prev = settings.kit_dir.with_name(settings.kit_dir.name + ".prev")
    if not prev.is_dir():
        raise UpdateError("no previous kit to go back to")
    failed = settings.kit_dir.with_name(settings.kit_dir.name + ".failed")
    shutil.rmtree(failed, ignore_errors=True)
    if settings.kit_dir.exists():
        os.replace(settings.kit_dir, failed)
    os.replace(prev, settings.kit_dir)


def run_installer(settings, host):
    installer = settings.kit_dir / "src" / "scripts" / "spark" / "spark_install.py"
    return host.run(["python3", installer])


def install(settings, version, host):
    state = read_state(settings)
    offered = state.get("available") or {}
    if state.get("interrupted"):
        # The kit in place may be the half-installed new one: installing again
        # would make it the "previous" kit. Going back comes first.
        raise UpdateError("the last installation was interrupted; roll back first")
    if state.get("status") not in ("ready", "failed") or offered.get("version") != version:
        raise UpdateError("%s is not ready to install (state %s)" % (version, state.get("status")))
    files = offered.get("files") or {}
    release = json.loads(Path(files["release"]).read_text(encoding="utf-8"))
    set_step(settings, state, "installing", "verify")
    # TUF checked the files when they were downloaded; they have lain on disk since.
    if sha256_file(files["kit"]) != release["kit"]["sha256"]:
        raise UpdateError("the downloaded kit changed on disk")
    by_name = {image["name"]: image for image in release["images"]}
    for name, path in files["images"].items():
        if sha256_file(path) != by_name[name]["sha256"]:
            raise UpdateError("the downloaded image %s changed on disk" % name)
    previous = {"label": installed_version(settings.kit_dir),
                "images": {name: host.image_id(name + ":latest") for name in by_name}}
    last = {"version": version, "previous": previous, "snapshot": None, "started_at": utcnow()}
    state["last"] = last
    if (release.get("compat") or {}).get("snapshot"):
        set_step(settings, state, "installing", "snapshot")
        last["snapshot"] = take_snapshot(settings, host, version)
    set_step(settings, state, "installing", "load")
    for name, path in sorted(files["images"].items()):
        if host.run(["docker", "load", "-i", path]):
            raise UpdateError("docker load of %s failed" % name)
    for image in release["images"]:
        if host.image_id(image["id"]) != image["id"]:
            raise UpdateError("image %s (%s) is not on the box after loading" % (image["name"], image["id"][:19]))
    set_step(settings, state, "installing", "kit")
    unpack_kit(settings, files["kit"])
    set_step(settings, state, "installing", "install")
    if run_installer(settings, host):
        last["failed_at"] = utcnow()
        set_step(settings, state, "rolling_back", "kit")
        # Whatever the new version did to the data, the previous one gets the copy back.
        restored = bool(last.get("snapshot"))
        rollback(settings, state, host, restore_data=restored)
        state.update({"status": "failed", "error": "the installer of %s failed; %s runs again%s" % (
            version, previous["label"], " with the data as before" if restored else ""), "step": None})
        save_state(settings, state)
        return state
    last["finished_at"] = utcnow()
    state.pop("interrupted", None)
    state.update({"status": "current", "installed": installed_version(settings.kit_dir), "available": None,
                  "error": None, "step": None})
    state.setdefault("history", []).insert(0, {"version": version, "at": utcnow(), "result": "installed"})
    del state["history"][20:]
    save_state(settings, state)
    for path in files["images"].values():
        Path(path).unlink(missing_ok=True)
    return state


def rollback(settings, state, host, restore_data):
    last = state.get("last") or {}
    previous = last.get("previous") or {}
    # After a failed installation the previous kit is in place already; a later
    # request (to put the data copy back, or after "broken") starts from there.
    if installed_version(settings.kit_dir) != previous.get("label"):
        restore_kit(settings)
        for name, image_id in sorted((previous.get("images") or {}).items()):
            if image_id:
                host.run(["docker", "tag", image_id, name + ":latest"], timeout=60)
    if restore_data:
        if not last.get("snapshot"):
            raise UpdateError("the last update took no data copy")
        set_step(settings, state, "rolling_back", "data")
        last["replaced_data"] = restore_snapshot(settings, host, last["snapshot"])
    set_step(settings, state, "rolling_back", "install")
    if run_installer(settings, host):
        state.update({"status": "broken", "error": "the previous version did not start either; "
                      "see %s" % (host.log_path or "the updater log"), "step": None})
        save_state(settings, state)
        raise UpdateError(state["error"])
    state["installed"] = installed_version(settings.kit_dir)
    state.setdefault("history", []).insert(0, {"version": last.get("version"), "at": utcnow(),
                                              "result": "rolled_back", "data_restored": bool(restore_data)})
    del state["history"][20:]
    return state


def handle_request(settings, host=None):
    """Read and remove the administrator's request, then act on it."""
    with locked(settings, wait=True):
        return _handle_request(settings, host)


RELEASE_VERSION = re.compile(r"^\d{2}\.\d{2}\.\d{1,3}(?:\.\d{1,4})?$")
REQUEST_USER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._@-]{0,63}$")


def parse_request(raw):
    """The request, reduced to checked values: nothing the adapter wrote reaches the state as it is."""
    try:
        request = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        request = None
    request = request if isinstance(request, dict) else {}
    action = request.get("action") if request.get("action") in ("install", "rollback") else None
    version = request.get("version")
    version = version if isinstance(version, str) and RELEASE_VERSION.match(version) else None
    by = request.get("by")
    by = by if isinstance(by, str) and REQUEST_USER.match(by) else None
    return {"action": action, "version": version, "by": by, "restore_data": request.get("restore_data") is True}


def _handle_request(settings, host):
    state = read_state(settings)
    try:
        with SharedFolder(settings.share_dir) as shared:
            raw = shared.take("request.json")
    except (OSError, UpdateError) as e:
        state.update({"request": {"action": None, "at": utcnow()}, "error": "UpdateError: %s" % e})
        save_state(settings, state)
        return state
    if raw is None:
        return state
    request = parse_request(raw)
    state["request"] = {"action": request["action"], "version": request["version"], "by": request["by"],
                        "restore_data": request["restore_data"], "at": utcnow()}
    if host is None:
        logs = settings.state_dir / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        host = Host(logs / ("request-%s.log" % time.strftime("%Y%m%d-%H%M%S")))
    try:
        if request["action"] == "install" and request["version"]:
            return install(settings, request["version"], host)
        if request["action"] == "rollback":
            if state.get("status") not in ("current", "failed", "broken") or not state.get("last"):
                raise UpdateError("nothing to roll back in state %s" % state.get("status"))
            set_step(settings, state, "rolling_back", "kit")
            rollback(settings, state, host, restore_data=request["restore_data"])
            state.pop("interrupted", None)
            state.update({"status": "current", "error": None, "step": None})
            save_state(settings, state)
            return state
        raise UpdateError("unknown request")
    except (UpdateError, OSError, ValueError, KeyError, AttributeError, TypeError) as e:
        state = dict(read_state(settings), request=state.get("request"))
        if state.get("status") in ("installing", "rolling_back"):
            state["status"] = "failed"
        state.update({"error": "%s: %s" % (type(e).__name__, e), "step": None})
        save_state(settings, state)
        return state


def bootstrap(settings, kit_tgz, host):
    """First installation from a release kit brought on a USB stick.

    The kit names its release and carries the channel's root: the stick's kit
    must be byte for byte the signed release kit, the images come from the
    channel (TUF-checked), and only then is the kit unpacked and installed."""
    from tuf.api.exceptions import RepositoryError, DownloadError

    if not settings.enabled:
        raise UpdateError("MATRIX_UPDATE must be on with an https:// MATRIX_UPDATE_URL (%s)"
                          % (settings.problem or "off"))
    try:
        with tarfile.open(kit_tgz, "r:gz") as archive:
            kit = json.load(archive.extractfile("KIT.json"))
            trusted = archive.extractfile("src/matrix_updater/trusted_root.json").read()
    except (OSError, KeyError, ValueError, tarfile.TarError, AttributeError) as e:
        raise UpdateError("%s is not a release kit: %s" % (kit_tgz, e))
    version = kit.get("label")
    if not isinstance(version, str) or not RELEASE_VERSION.match(version):
        raise UpdateError("%s is a development kit (%s), not a release" % (kit_tgz, version))
    state = {"format": 1, "channel": settings.channel, "status": "installing", "step": "verify",
             "installed": installed_version(settings.kit_dir), "checked_at": utcnow(),
             "last": {"version": version, "previous": {"label": installed_version(settings.kit_dir), "images": {}},
                      "snapshot": None, "started_at": utcnow(), "bootstrap": True}}
    save_state(settings, state)
    try:
        updater = make_updater(settings, "vivesec-matrix-updater/%s box=%s v=bootstrap s=installing" % (
            AGENT_VERSION, box_id(settings.state_dir)), trusted_root=trusted)
        updater.refresh()
        release, _ = fetch_json(updater, "releases/%s/release.json" % version)
        if release is None:
            raise UpdateError("the channel %s does not hold release %s" % (settings.url, version))
        if sha256_file(kit_tgz) != release["kit"]["sha256"]:
            raise UpdateError("the kit on the stick is not the signed kit of %s" % version)
        set_step(settings, state, "installing", "load")
        for image in release["images"]:
            if host.image_id(image["id"]) == image["id"]:
                continue
            path = fetch_file(updater, image["target"])
            print("loading %s" % image["name"], flush=True)
            if host.run(["docker", "load", "-i", path]) or host.image_id(image["id"]) != image["id"]:
                raise UpdateError("image %s did not load" % image["name"])
            Path(path).unlink(missing_ok=True)
    except (RepositoryError, DownloadError, OSError, KeyError, ValueError) as e:
        raise UpdateError("%s: %s" % (type(e).__name__, e))
    set_step(settings, state, "installing", "kit")
    unpack_kit(settings, kit_tgz)
    set_step(settings, state, "installing", "install")
    if run_installer(settings, host):
        state.update({"status": "failed", "step": None, "error": "the installer of %s failed; see %s" % (
            version, host.log_path or "the updater log")})
        save_state(settings, state)
        return state
    state["last"]["finished_at"] = utcnow()
    state.update({"status": "current", "step": None, "error": None, "available": None,
                  "installed": installed_version(settings.kit_dir)})
    state.setdefault("history", []).insert(0, {"version": version, "at": utcnow(), "result": "installed"})
    save_state(settings, state)
    return state


def main(argv=None):
    parser = argparse.ArgumentParser(description="Matrix AI update client")
    parser.add_argument("command", choices=("check", "request", "status", "bootstrap"))
    parser.add_argument("kit", nargs="?", help="bootstrap: the release kit (matrix-kit-<version>.tgz)")
    args = parser.parse_args(argv)
    settings = Settings.load()
    if args.command == "bootstrap":
        if os.geteuid() != 0 or not args.kit:
            print("usage: sudo updater.py bootstrap <release kit .tgz>", file=sys.stderr)
            return 1
        logs = settings.state_dir / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        host = Host(logs / ("bootstrap-%s.log" % time.strftime("%Y%m%d-%H%M%S")))
        print("log: %s" % host.log_path, flush=True)
        try:
            with locked(settings, wait=True):
                state = bootstrap(settings, Path(args.kit), host)
        except UpdateError as e:
            state = dict(read_state(settings), status="failed", step=None, error=str(e))
            save_state(settings, state)
        print(json.dumps({k: state.get(k) for k in ("status", "installed", "error")}, indent=2))
        return 0 if state.get("status") == "current" else 1
    if args.command in ("check", "request"):
        if os.geteuid() != 0:
            print("run as root", file=sys.stderr)
            return 1
        state = check(settings) if args.command == "check" else handle_request(settings)
    else:
        state = read_state(settings)
    shown = dict(state)
    if isinstance(shown.get("available"), dict):
        shown["available"] = {k: v for k, v in shown["available"].items() if k != "files"}
    print(json.dumps(shown, indent=2, ensure_ascii=False))
    return 0 if state.get("status") != "error" else 1


if __name__ == "__main__":
    sys.exit(main())
