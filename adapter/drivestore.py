"""The box's own file store for standalone mode (E27 S5), pure stdlib.

Without a ViVeSecBox the box keeps the original files itself. A user uploads a
file from the web UI; it waits in quarantine until clamd has scanned it, is
then moved into the room, and is indexed on the same internal path the
ViVeSecBox sync takes (directory -> file/check -> content -> RAG and graph).

Disk layout under `ADAPTER_STORE_DIR` (default `/data/files`, which belongs on
the encrypted volume, E05):

    rooms/<room>/<path>      the stored files; /storage/drives/<room>/<path>
    quarantine/<id>.part     an upload being received
    quarantine/<id>.bin      a received upload waiting for the scan
    uploads.json             upload records (status per upload)
    events.jsonl             upload, scan, store, index, folder and delete
                             events: who, what, size, SHA-256; never content

Paths are strict: every segment is NFC-normalised and must not be empty, `.`
or `..`, start with a dot, end with a space or dot, or contain a separator, a
control character or a bidirectional override. Nothing under the store root
may be a symlink; such entries are neither listed nor followed.

The upload worker is ONE thread, so uploads are scanned, released and indexed
in order, one at a time, and an overwrite never races another upload of the
same path.
"""
import hashlib
import json
import os
import shutil
import stat
import sys
import threading
import time
import unicodedata
import uuid

import acl
import clamd
import corpus

SCAN_OFF = "off"
SCAN_ON = "on"
SCAN_REQUIRED = "required"
SCAN_MODES = (SCAN_OFF, SCAN_ON, SCAN_REQUIRED)

DEFAULT_MAX_BYTES = 256 * 1024 * 1024
# What the RAG can read (rag_service/extract.py) plus the images OCR reads.
DEFAULT_EXTENSIONS = (
    ".txt", ".md", ".markdown", ".text", ".csv", ".tsv", ".json", ".log", ".rst",
    ".yaml", ".yml", ".ini", ".xml",
    ".pdf", ".docx", ".pptx", ".ppt", ".xls", ".html", ".htm", ".rtf",
    ".xlsx", ".xlsm",
    ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp",
)
DEFAULT_WRITERS = ("role:admin",)

MAX_SEGMENT_BYTES = 255
MAX_PATH_BYTES = 1024
MAX_DEPTH = 32
_BIDI = frozenset(chr(c) for c in list(range(0x202A, 0x202F)) + list(range(0x2066, 0x206A)))

# Upload states. The final ones never change again.
QUEUED = "queued"                    # in quarantine, waiting for the worker
WAITING_SCANNER = "waiting_scanner"  # clamd unreachable and the scan is required
INDEXING = "indexing"                # released into the room, being indexed
INDEX_PENDING = "index_pending"      # stored; the index was not reachable, retried
INDEXED = "indexed"
STORED = "stored"                    # stored, but the RAG does not index it (reason)
INFECTED = "infected"
REJECTED = "rejected"                # clamd refused to scan it
FAILED = "failed"
CANCELLED = "cancelled"
FINAL = frozenset((INDEXED, STORED, INFECTED, REJECTED, FAILED, CANCELLED))
RETRIED = frozenset((WAITING_SCANNER, INDEX_PENDING))

RECORD_KEEP_SECONDS = 7 * 24 * 3600
RECORD_MAX = 2000
COPY_CHUNK = 1024 * 1024


class StoreError(Exception):
    """A refused store operation; `code` is the stable error for the client."""

    def __init__(self, code, message=None, status=400):
        super().__init__(message or code)
        self.code = code
        self.status = status


class UploadTooLarge(StoreError):
    def __init__(self):
        super().__init__("too_large", "the file is larger than the upload limit", 413)


def _warn(message):
    sys.stderr.write("[adapter] store: %s\n" % message)


# -- settings -------------------------------------------------------------------
def parse_scan_mode(raw, warn=_warn):
    """off | on | required. Unset and unknown values both mean `required`:
    an upload is never indexed unscanned unless that was asked for."""
    value = (raw or "").strip().lower()
    if not value:
        return SCAN_REQUIRED
    if value in SCAN_MODES:
        return value
    warn("unknown ADAPTER_UPLOAD_SCAN %r -> %s" % (raw, SCAN_REQUIRED))
    return SCAN_REQUIRED


def parse_extensions(raw):
    """A frozenset of lower-case extensions with the dot, or None for `*`
    (any type). Unset means the default (indexable) list."""
    if raw is None or not raw.strip():
        return frozenset(DEFAULT_EXTENSIONS)
    items = [item.strip().lower() for item in raw.replace(";", ",").replace(" ", ",").split(",")]
    if "*" in items:
        return None
    return frozenset(item if item.startswith(".") else "." + item for item in items if item)


def parse_writers(raw, warn=_warn):
    """The principals that may upload, create folders and delete. Unset means
    administrators only; a malformed entry is dropped (it grants nothing)."""
    if raw is None or not raw.strip():
        return frozenset(DEFAULT_WRITERS)
    out = set()
    for item in raw.replace(";", ",").split(","):
        item = item.strip()
        if not item:
            continue
        principal = acl.normalize_principal(item)
        if principal is None:
            warn("ignoring malformed ADAPTER_STORE_WRITERS entry %r" % item)
            continue
        out.add(principal)
    return frozenset(out)


def may_write(writers, access):
    """Whether a caller's access context names one of the writers."""
    if access is None:
        return False
    return bool(set(access.principals) & set(writers))


# -- paths ----------------------------------------------------------------------
def check_segment(segment):
    """The NFC form of one path segment, or StoreError."""
    name = unicodedata.normalize("NFC", segment or "")
    if not name or name in (".", ".."):
        raise StoreError("invalid_path", "empty or relative path segment")
    if len(name.encode("utf-8")) > MAX_SEGMENT_BYTES:
        raise StoreError("invalid_path", "a name is longer than %d bytes" % MAX_SEGMENT_BYTES)
    if name.startswith("."):
        raise StoreError("invalid_path", "names must not start with a dot")
    if name != name.rstrip(" .") or name != name.lstrip(" "):
        raise StoreError("invalid_path", "names must not start with a space or end with a space or dot")
    for ch in name:
        if ch in "/\\" or ch in _BIDI or unicodedata.category(ch) == "Cc":
            raise StoreError("invalid_path", "the name contains a character that is not allowed")
    return name


def split_path(path, prefix=None):
    """(room, [segments below the room]) of a drive-absolute path, NFC, or
    StoreError. The room itself (no segments) is allowed."""
    prefix = corpus.norm(prefix or corpus.DRIVE_PREFIX)
    raw = corpus.norm(str(path or ""))
    if not raw.startswith(prefix + "/"):
        raise StoreError("invalid_path", "path outside the drive prefix")
    parts = raw[len(prefix) + 1:].split("/")
    if len(parts) > MAX_DEPTH + 1:
        raise StoreError("invalid_path", "the folder structure is too deep")
    segments = [check_segment(part) for part in parts]
    canonical = prefix + "/" + "/".join(segments)
    if len(canonical.encode("utf-8")) > MAX_PATH_BYTES:
        raise StoreError("invalid_path", "the path is too long")
    return segments[0], segments[1:]


def canonical_path(path, prefix=None):
    prefix = corpus.norm(prefix or corpus.DRIVE_PREFIX)
    room, rest = split_path(path, prefix)
    return "/".join([prefix, room] + rest)


def extension_of(path):
    name = path.rsplit("/", 1)[-1]
    return os.path.splitext(name)[1].lower()


# -- the store ------------------------------------------------------------------
class DriveStore:
    def __init__(self, root, prefix=None, max_bytes=DEFAULT_MAX_BYTES, extensions=...,
                 clock=time.time):
        self.root = os.path.abspath(root) if root else None
        self.prefix = corpus.norm(prefix or corpus.DRIVE_PREFIX)
        self.max_bytes = int(max_bytes)
        self.extensions = (frozenset(DEFAULT_EXTENSIONS) if extensions is ...
                           else extensions)
        self.clock = clock
        self.lock = threading.RLock()
        self._records = {}
        self._loaded = False

    @classmethod
    def from_env(cls, env=None):
        env = env if env is not None else os.environ
        root = (env.get("ADAPTER_STORE_DIR", "/data/files") or "").strip()
        raw_max = (env.get("ADAPTER_UPLOAD_MAX_BYTES") or "").strip()
        try:
            max_bytes = int(raw_max) if raw_max else DEFAULT_MAX_BYTES
        except ValueError:
            _warn("ADAPTER_UPLOAD_MAX_BYTES=%r is not a number -> %d" % (raw_max, DEFAULT_MAX_BYTES))
            max_bytes = DEFAULT_MAX_BYTES
        return cls(root or None, max_bytes=max(1, max_bytes),
                   extensions=parse_extensions(env.get("ADAPTER_UPLOAD_EXTENSIONS")))

    @property
    def enabled(self):
        return bool(self.root)

    def _prepare(self):
        """Create the layout; on a locked LUKS volume /data is not mounted yet,
        so this is retried by every operation that needs the disk."""
        try:
            os.makedirs(self.root, mode=0o700, exist_ok=True)
            os.chmod(self.root, 0o700)
            for name in ("events.jsonl", "uploads.json"):
                if os.path.exists(os.path.join(self.root, name)):
                    os.chmod(os.path.join(self.root, name), 0o600)
            for name in ("rooms", "quarantine"):
                os.makedirs(os.path.join(self.root, name), mode=0o700, exist_ok=True)
        except OSError as e:
            _warn("cannot prepare %s yet (%s)" % (self.root, e.strerror))
            return False
        self._real_rooms = os.path.realpath(os.path.join(self.root, "rooms"))
        self._quarantine = os.path.join(self.root, "quarantine")
        if not self._loaded:
            self._load_records()
        return True

    def _ready(self):
        if not self.root:
            raise StoreError("store_disabled", "the file store is not configured", 503)
        if not self._loaded and not self._prepare():
            raise StoreError("store_unavailable", "the file store is not available", 503)

    def _usable(self):
        """Like _ready, for reads that answer 'nothing' instead of failing."""
        try:
            self._ready()
            return True
        except StoreError:
            return False

    # -- path mapping -----------------------------------------------------------
    def canonical(self, path):
        return canonical_path(path, self.prefix)

    def room_root(self, path):
        room, _rest = split_path(path, self.prefix)
        return self.prefix + "/" + room

    def is_room_root(self, path):
        _room, rest = split_path(path, self.prefix)
        return not rest

    def _local(self, path):
        """Disk path of a canonical drive path. Every existing component is
        checked: none may be a symlink or lead outside the rooms directory."""
        self._ready()
        room, rest = split_path(path, self.prefix)
        current = self._real_rooms
        for segment in [room] + rest:
            current = os.path.join(current, segment)
            try:
                info = os.lstat(current)
            except (FileNotFoundError, NotADirectoryError):
                continue
            except OSError as e:
                raise StoreError("store_unavailable", e.strerror, 503)
            if stat.S_ISLNK(info.st_mode):
                raise StoreError("invalid_path", "symbolic links are not followed")
        return current

    def kind(self, path):
        """'file', 'folder' or None (missing, or anything else on disk)."""
        try:
            info = os.lstat(self._local(path))
        except FileNotFoundError:
            return None
        except NotADirectoryError:
            return None
        if stat.S_ISDIR(info.st_mode):
            return "folder"
        if stat.S_ISREG(info.st_mode):
            return "file"
        return None

    def conflict(self, path):
        """Why `path` cannot become a file: 'parent_is_file', 'is_folder', or
        None. An existing file at `path` itself is not a conflict here."""
        room, rest = split_path(path, self.prefix)
        current = self.prefix + "/" + room
        if self.kind(current) == "file":
            return "parent_is_file"
        for segment in rest[:-1]:
            current = current + "/" + segment
            if self.kind(current) == "file":
                return "parent_is_file"
        if rest and self.kind(path) == "folder":
            return "is_folder"
        return None

    # -- reading ----------------------------------------------------------------
    def list_dir(self, path):
        """[{path, name, file, size, modified}] of one folder; [] when the
        folder does not exist. Symlinks and invalid names are skipped."""
        local = self._local(path)
        try:
            names = os.listdir(local)
        except (FileNotFoundError, NotADirectoryError):
            return []
        base = self.canonical(path)
        out = []
        for name in sorted(names):
            try:
                clean = check_segment(name)
            except StoreError:
                continue
            if clean != name:
                continue
            try:
                info = os.lstat(os.path.join(local, name))
            except OSError:
                continue
            if stat.S_ISDIR(info.st_mode):
                out.append({"path": base + "/" + name, "name": name, "file": False,
                            "size": None, "modified": int(info.st_mtime * 1000)})
            elif stat.S_ISREG(info.st_mode):
                out.append({"path": base + "/" + name, "name": name, "file": True,
                            "size": info.st_size, "modified": int(info.st_mtime * 1000)})
        return out

    def walk(self, path):
        """Every canonical path at and below `path` that exists on disk."""
        out = []
        kind = self.kind(path)
        if kind is None:
            return out
        base = self.canonical(path)
        out.append(base)
        if kind == "folder":
            for entry in self.list_dir(base):
                out.extend(self.walk(entry["path"]) if not entry["file"] else [entry["path"]])
        return out

    def open_file(self, path):
        """(binary file object, size) of a stored file, or None."""
        local = self._local(path)
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
        try:
            fd = os.open(local, flags)
        except (FileNotFoundError, NotADirectoryError, IsADirectoryError):
            return None
        except OSError:
            return None
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            os.close(fd)
            return None
        return os.fdopen(fd, "rb"), info.st_size

    def read_bytes(self, path):
        opened = self.open_file(path)
        if opened is None:
            return None
        handle, _size = opened
        with handle:
            return handle.read()

    def stat_file(self, path):
        """(size, mtime in ns) of a stored file, or None."""
        try:
            info = os.lstat(self._local(path))
        except OSError:
            return None
        if not stat.S_ISREG(info.st_mode):
            return None
        return info.st_size, info.st_mtime_ns

    # -- writing ----------------------------------------------------------------
    def make_dirs(self, path):
        """Create a folder and its parents; the canonical folders created, from
        the room down. StoreError when a file is in the way."""
        room, rest = split_path(path, self.prefix)
        created = []
        current = self.prefix + "/" + room
        with self.lock:
            for index in range(len(rest) + 1):
                if index:
                    current = current + "/" + rest[index - 1]
                kind = self.kind(current)
                if kind == "folder":
                    continue
                if kind is not None or os.path.lexists(self._local(current)):
                    raise StoreError("parent_is_file", "a file is in the way", 409)
                try:
                    os.mkdir(self._local(current), 0o750)
                except FileExistsError:
                    if self.kind(current) != "folder":
                        raise StoreError("parent_is_file", "a file is in the way", 409)
                    continue
                created.append(current)
        return created

    def remove(self, path):
        """Delete a file or a folder tree from disk. The room root stays."""
        if self.is_room_root(path):
            raise StoreError("room_root", "a room cannot be deleted here", 400)
        local = self._local(path)
        with self.lock:
            try:
                info = os.lstat(local)
            except FileNotFoundError:
                return False
            if stat.S_ISDIR(info.st_mode):
                shutil.rmtree(local)
            else:
                os.remove(local)
        return True

    def purge(self):
        """Factory reset: every stored file, quarantined upload and record."""
        if not self.root:
            return 0
        removed = 0
        with self.lock:
            for name in ("rooms", "quarantine"):
                base = os.path.join(self.root, name)
                for top, _dirs, files in os.walk(base, topdown=False):
                    removed += len(files)
                shutil.rmtree(base, ignore_errors=True)
            for name in ("uploads.json",):
                try:
                    os.remove(os.path.join(self.root, name))
                except OSError:
                    pass
            self._records = {}
            self._loaded = False
            self._prepare()
        return removed

    # -- quarantine -------------------------------------------------------------
    def receive(self, read, length, user, drive, path, overwrite=False):
        """Stream one upload into quarantine and record it as queued.

        `read(n)` returns the next bytes of the body ('' at its end); `length`
        is the declared Content-Length or None for a body that ends by itself.
        The limit is enforced while reading, before anything is kept.
        """
        self._ready()
        target = self.canonical(path)
        if length is not None and length > self.max_bytes:
            raise UploadTooLarge()
        upload_id = uuid.uuid4().hex
        part = os.path.join(self._quarantine, upload_id + ".part")
        digest = hashlib.sha256()
        size = 0
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
        fd = os.open(part, flags, 0o600)
        try:
            with os.fdopen(fd, "wb") as handle:
                remaining = length
                while remaining is None or remaining > 0:
                    want = COPY_CHUNK if remaining is None else min(COPY_CHUNK, remaining)
                    block = read(want)
                    if not block:
                        break
                    size += len(block)
                    if size > self.max_bytes:
                        raise UploadTooLarge()
                    digest.update(block)
                    handle.write(block)
                    if remaining is not None:
                        remaining -= len(block)
                if remaining:
                    raise StoreError("incomplete", "the upload ended early", 400)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(part, self._bin(upload_id))
        except BaseException:
            try:
                os.remove(part)
            except OSError:
                pass
            raise
        now = self.clock()
        record = {"id": upload_id, "user": user, "drive": corpus.norm(drive or ""),
                  "path": target, "size": size, "sha256": digest.hexdigest(),
                  "overwrite": bool(overwrite), "status": QUEUED, "scan": None,
                  "signature": None, "reason": None, "chunks": None,
                  "created": now, "updated": now}
        with self.lock:
            self._records[upload_id] = record
            self._save_records()
        self.event("upload", user, target, size=size, sha256=record["sha256"], upload=upload_id)
        return dict(record)

    def _bin(self, upload_id):
        return os.path.join(self._quarantine, upload_id + ".bin")

    def quarantined(self, upload_id):
        path = self._bin(upload_id)
        return path if os.path.isfile(path) else None

    def discard(self, upload_id):
        try:
            os.remove(self._bin(upload_id))
        except OSError:
            pass

    def release(self, upload_id, path, overwrite):
        """Move a scanned upload into the room (atomic rename). StoreError
        when the target cannot take it."""
        source = self.quarantined(upload_id)
        if source is None:
            raise StoreError("lost", "the quarantined file is missing", 500)
        with self.lock:
            problem = self.conflict(path)
            if problem:
                raise StoreError(problem, status=409)
            if not overwrite and self.kind(path) == "file":
                raise StoreError("exists", status=409)
            parent = path.rsplit("/", 1)[0]
            created = self.make_dirs(parent)
            os.replace(source, self._local(path))
        return created

    # -- records ----------------------------------------------------------------
    def _records_path(self):
        return os.path.join(self.root, "uploads.json")

    def _load_records(self):
        try:
            with open(self._records_path(), "r", encoding="utf-8") as handle:
                data = json.load(handle)
            self._records = {r["id"]: r for r in data.get("uploads", [])
                             if isinstance(r, dict) and r.get("id")}
        except FileNotFoundError:
            self._records = {}
        except (OSError, ValueError) as e:
            _warn("upload records unreadable (%s): starting empty" % e)
            self._records = {}
        self._loaded = True

    def _save_records(self):
        now = self.clock()
        records = sorted(self._records.values(), key=lambda r: r.get("created") or 0)
        keep = [r for r in records if r.get("status") not in FINAL
                or now - (r.get("updated") or 0) < RECORD_KEEP_SECONDS]
        finished = [r for r in keep if r.get("status") in FINAL]
        drop = {r["id"] for r in finished[:max(0, len(keep) - RECORD_MAX)]}
        self._records = {r["id"]: r for r in keep if r["id"] not in drop}
        tmp = self._records_path() + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump({"uploads": list(self._records.values())}, handle)
        os.replace(tmp, self._records_path())

    def record(self, upload_id):
        if not self._usable():
            return None
        with self.lock:
            found = self._records.get(upload_id)
            return dict(found) if found else None

    def update(self, upload_id, **fields):
        if not self._usable():
            return None
        with self.lock:
            found = self._records.get(upload_id)
            if found is None:
                return None
            found.update(fields)
            found["updated"] = self.clock()
            self._save_records()
            return dict(found)

    def records(self, user=None, statuses=None):
        if not self._usable():
            return []
        with self.lock:
            out = [dict(r) for r in self._records.values()
                   if (user is None or r.get("user") == user)
                   and (statuses is None or r.get("status") in statuses)]
        out.sort(key=lambda r: -(r.get("created") or 0))
        return out

    def pending_for(self, path):
        """Unfinished uploads aimed at exactly `path`."""
        target = self.canonical(path)
        return [r for r in self.records() if r.get("path") == target
                and r.get("status") not in FINAL]

    def cancel_under(self, path, user):
        """Cancel every unfinished upload at or below `path` (a delete won)."""
        self._ready()
        base = self.canonical(path)
        cancelled = []
        with self.lock:
            for record in self._records.values():
                if record.get("status") in FINAL:
                    continue
                if not (record["path"] == base or record["path"].startswith(base + "/")):
                    continue
                record["status"] = CANCELLED
                record["reason"] = "deleted"
                record["updated"] = self.clock()
                self.discard(record["id"])
                cancelled.append(record["id"])
            if cancelled:
                self._save_records()
        for upload_id in cancelled:
            self.event("cancel", user, base, upload=upload_id)
        return cancelled

    def dismiss(self, upload_id, user):
        """The uploader removes a record from the list; an unfinished upload is
        cancelled first. False when the record is not theirs."""
        self._ready()
        with self.lock:
            record = self._records.get(upload_id)
            if record is None or record.get("user") != user:
                return False
            if record.get("status") in (INDEXING, INDEX_PENDING):
                raise StoreError("busy", "the file is being indexed", 409)
            if record.get("status") not in FINAL:
                self.discard(upload_id)
                self.event("cancel", user, record["path"], upload=upload_id)
            del self._records[upload_id]
            self._save_records()
        return True

    # -- event log ----------------------------------------------------------------
    def event(self, action, user, path, **detail):
        """Append one event to events.jsonl. A failed write is reported, not
        raised: the file operation itself already happened."""
        if not self._usable():
            return
        entry = {"ts": round(self.clock(), 3), "action": action, "user": user, "path": path}
        entry.update({key: value for key, value in detail.items() if value is not None})
        try:
            with self.lock:
                fd = os.open(os.path.join(self.root, "events.jsonl"),
                             os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
                with os.fdopen(fd, "a", encoding="utf-8") as handle:
                    handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError as e:
            _warn("event log not written (%s)" % e)


# -- the worker -----------------------------------------------------------------
class UploadWorker:
    """Scans, releases and indexes uploads, one at a time, in order.

    `index(path)` indexes a stored file and returns {"status": INDEXED|STORED,
    "reason", "chunks"}; it raises when the index cannot be reached (the upload
    then waits as INDEX_PENDING). `unindex(path)` removes a path from the
    index again (an upload that was deleted while it was being indexed).
    """

    def __init__(self, store, scanner, scan_mode, index, unindex, retry_seconds=60.0):
        self.store = store
        self.scanner = scanner
        self.scan_mode = scan_mode
        self.index = index
        self.unindex = unindex
        self.retry_seconds = retry_seconds
        self.cond = threading.Condition()
        self.queue = []
        self._thread = None
        self._timer = None
        self.scanner_state = {"ok": None, "error": None, "checked": None}

    def start(self):
        if self._thread is not None or not self.store.enabled:
            return
        self._thread = threading.Thread(target=self._loop, name="upload-worker", daemon=True)
        self._thread.start()
        self._timer = threading.Thread(target=self._retry_loop, name="upload-retry", daemon=True)
        self._timer.start()

    def submit(self, upload_id):
        with self.cond:
            if upload_id not in self.queue:
                self.queue.append(upload_id)
            self.cond.notify()

    def resume(self):
        """Queue every unfinished upload again (start-up, retry timer)."""
        for record in reversed(self.store.records()):
            if record.get("status") not in FINAL:
                self.submit(record["id"])

    def _retry_loop(self):
        self.resume()
        while True:
            time.sleep(self.retry_seconds)
            for record in reversed(self.store.records(statuses=RETRIED)):
                self.submit(record["id"])

    def _loop(self):
        while True:
            with self.cond:
                while not self.queue:
                    self.cond.wait()
                upload_id = self.queue.pop(0)
            try:
                self.process(upload_id)
            except Exception as e:  # noqa: BLE001
                _warn("upload %s: %s" % (upload_id, e))

    def drain(self):
        """Process everything queued on the calling thread (tests)."""
        while True:
            with self.cond:
                if not self.queue:
                    return
                upload_id = self.queue.pop(0)
            self.process(upload_id)

    # -- one upload -------------------------------------------------------------
    def process(self, upload_id):
        record = self.store.record(upload_id)
        if record is None or record["status"] in FINAL:
            return
        if record["status"] in (QUEUED, WAITING_SCANNER):
            record = self._scan_and_release(record)
            if record is None:
                return
        if record["status"] in (INDEXING, INDEX_PENDING):
            self._index(record)

    def _scan(self, record, source):
        """(scan result, signature) or None when the upload must wait/stop."""
        user, path, upload_id = record["user"], record["path"], record["id"]
        if self.scan_mode == SCAN_OFF:
            return "off", None
        try:
            result, signature = self.scanner.scan_file(source)
            self.scanner_state = {"ok": True, "error": None, "checked": time.time()}
        except clamd.ScanUnavailable as e:
            self.scanner_state = {"ok": False, "error": str(e), "checked": time.time()}
            if self.scan_mode == SCAN_REQUIRED:
                if record["status"] != WAITING_SCANNER:
                    self.store.update(upload_id, status=WAITING_SCANNER, reason="scanner_unavailable")
                    self.store.event("scan", user, path, upload=upload_id, result="unavailable")
                return None
            self.store.event("scan", user, path, upload=upload_id, result="unavailable",
                             detail="released unscanned (ADAPTER_UPLOAD_SCAN=on)")
            return "unavailable", None
        except clamd.ScanError as e:
            if self.scan_mode == SCAN_REQUIRED:
                self.store.discard(upload_id)
                self.store.update(upload_id, status=REJECTED, reason="scan_error")
                self.store.event("scan", user, path, upload=upload_id, result="error",
                                 detail=str(e)[:200], sha256=record["sha256"])
                return None
            self.store.event("scan", user, path, upload=upload_id, result="error",
                             detail=str(e)[:200])
            return "error", None
        if result == "infected":
            self.store.discard(upload_id)
            self.store.update(upload_id, status=INFECTED, scan="infected", signature=signature)
            self.store.event("scan", user, path, upload=upload_id, result="infected",
                             signature=signature, sha256=record["sha256"], size=record["size"])
            return None
        self.store.event("scan", user, path, upload=upload_id, result="clean")
        return result, signature

    def _scan_and_release(self, record):
        upload_id = record["id"]
        source = self.store.quarantined(upload_id)
        if source is None:
            self.store.update(upload_id, status=FAILED, reason="lost")
            return None
        outcome = self._scan(record, source)
        if outcome is None:
            return None
        # Checked and released under the store lock, so a delete of the target
        # folder (cancel_under) either cancels this upload first or sees it
        # INDEXING and has it removed from the index afterwards.
        with self.store.lock:
            current = self.store.record(upload_id)
            if current is None or current["status"] in FINAL:
                # Cancelled (deleted or dismissed) while it was being scanned.
                self.store.discard(upload_id)
                return None
            try:
                created = self.store.release(upload_id, record["path"], record["overwrite"])
            except StoreError as e:
                self.store.discard(upload_id)
                self.store.update(upload_id, status=FAILED, reason=e.code, scan=outcome[0])
                self.store.event("store", record["user"], record["path"], upload=upload_id,
                                 result="failed", detail=e.code)
                return None
            released = self.store.update(upload_id, status=INDEXING, scan=outcome[0], reason=None)
        self.store.event("store", record["user"], record["path"], upload=upload_id,
                         result="stored", sha256=record["sha256"], size=record["size"],
                         folders=created or None)
        return released

    def _index(self, record):
        upload_id, path = record["id"], record["path"]
        if self.store.kind(path) != "file":
            self.store.update(upload_id, status=FAILED, reason="deleted")
            return
        if record["status"] != INDEXING:
            record = self.store.update(upload_id, status=INDEXING)
        try:
            result = self.index(path)
        except Exception as e:  # noqa: BLE001
            self.store.update(upload_id, status=INDEX_PENDING, reason="index_unavailable")
            _warn("indexing %s postponed: %s" % (upload_id, e))
            return
        current = self.store.record(upload_id)
        if current is None or current["status"] == CANCELLED or self.store.kind(path) != "file":
            # Deleted while it was being indexed: the delete may have run first.
            try:
                self.unindex(path)
            except Exception as e:  # noqa: BLE001
                _warn("removing %s from the index failed: %s" % (upload_id, e))
            return
        status = result.get("status") or INDEXED
        self.store.update(upload_id, status=status, reason=result.get("reason"),
                          chunks=result.get("chunks"))
        self.store.event("index", record["user"], path, upload=upload_id, result=status,
                         detail=result.get("reason"), chunks=result.get("chunks"))
