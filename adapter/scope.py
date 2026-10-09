"""Which drives -- and therefore which corpora -- one request may read.

A scope resolves to the single active `VVS-Drive` unless the caller supplies
extra drive roots, so today's behaviour is byte-identical to the one drive =
one corpus model the rest of the adapter was built on. The module exists so
that "may this request see this path?" has exactly ONE implementation:
retrieval, filename search and file reads must not each grow their own copy of
an ACL rule.

The scope is resolved server-side and is never widened by anything the client
sends; a client may only ever narrow it.
"""
import base64
import hashlib
import json
import os
import threading

import corpus

# The ViVeSecBox sends the drives the user may reach BESIDES the active one,
# urlsafe-base64 of NUL-separated UTF-8 paths (docs/aibox_patch_0902.md).
HEADER_OTHER_DRIVES = "VVS-Other-Drives"

SOURCE_SINGLE = "single"
SOURCE_LIST = "list"
SOURCE_HEADER = "header"
SOURCE_ENTITLEMENTS = "entitlements"
SOURCE_ALL_DRIVES = "all-drives"
SOURCE_TOKEN = "token"


def _escapes(path):
    """True when a path is not canonical: '..' climbs out of the drive it
    names, and '.' or an empty segment would let the same file be named by a
    string the ACL rules do not match."""
    parts = corpus.norm(path or "").split("/")
    return any(part in ("", ".", "..") for part in parts[1:])


def decode_other_drives(raw):
    """Decode a `VVS-Other-Drives` header into drive paths.

    The value is urlsafe base64 of the UTF-8 paths joined by NUL bytes, so a
    drive name containing a comma, a space or unicode survives the header
    unchanged. Padding is tolerated. Raises ValueError on a malformed value.
    """
    if not raw or not raw.strip():
        return []
    s = raw.strip()
    s += "=" * ((-len(s)) % 4)
    try:
        blob = base64.urlsafe_b64decode(s.encode("ascii"))
    except Exception as e:  # noqa: BLE001
        raise ValueError("invalid %s encoding (expected urlsafe base64): %s"
                         % (HEADER_OTHER_DRIVES, e))
    try:
        text = blob.decode("utf-8")
    except UnicodeDecodeError as e:
        raise ValueError("invalid %s encoding (expected UTF-8): %s"
                         % (HEADER_OTHER_DRIVES, e))
    return [part for part in text.split("\x00") if part.strip()]


class Entitlements:
    """user -> drive roots, read from a JSON map (`ADAPTER_ENTITLEMENTS`).

    Shape: `{"lars.nygaard": ["/storage/drives/finance/"], "*": [...]}`. This
    is the stand-in for the box header while the contract is being agreed, so
    multi-drive retrieval can be exercised on a box that does not send it yet.
    The file is re-read when its mtime changes; an unreadable or malformed file
    grants nothing rather than keeping a stale grant.
    """

    def __init__(self, path):
        self.path = path
        self._mtime = None
        self._map = {}
        self._lock = threading.Lock()

    @classmethod
    def from_env(cls):
        path = (os.environ.get("ADAPTER_ENTITLEMENTS") or "").strip()
        return cls(path) if path else None

    def _refresh(self):
        try:
            mtime = os.path.getmtime(self.path)
        except OSError:
            self._map, self._mtime = {}, None
            return
        if mtime == self._mtime:
            return
        self._mtime = mtime
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            self._map = {}
            return
        self._map = {str(k): [str(p) for p in v]
                     for k, v in (data or {}).items()
                     if isinstance(v, (list, tuple))}

    def drives_for(self, user):
        with self._lock:
            self._refresh()
            return list(self._map.get("*", [])) + list(self._map.get(user or "", []))


class Scope:
    """A resolved read scope: the active drive first, then every other drive
    the request is entitled to. Treated as immutable once built."""

    def __init__(self, active_drive, drive_roots, corpus_ids, source,
                 active_corpus_id=None):
        # The raw header value is kept because the session and file stores are
        # keyed by it; drive_roots/corpus_ids are normalized and parallel.
        self.active_drive = active_drive
        self.active_root = corpus.norm(active_drive)
        self.drive_roots = tuple(drive_roots)
        self.corpus_ids = tuple(corpus_ids)
        self.source = source
        # Pinned at construction so narrowing the SEARCH scope cannot move the
        # write target or the audit key.
        self.active_corpus_id = active_corpus_id or corpus.corpus_id_of_drive(
            self.active_root)

    @property
    def corpus_id(self):
        """The active drive's corpus: save target, audit key, analyse default."""
        return self.active_corpus_id

    @property
    def multi(self):
        return len(self.drive_roots) > 1

    @property
    def session_scope(self):
        """Conversation key. A scope that is exactly the active drive keeps
        using the raw drive string, so conversations spilled by earlier builds
        stay addressable; any other scope gets its own thread, because the
        assistant's earlier answers count as grounding and must not cross into
        a scope that excludes the drive they came from."""
        if self.drive_roots == (self.active_root,):
            return self.active_drive
        digest = hashlib.sha1("\x00".join(self.drive_roots).encode("utf-8"))
        return "scope:" + digest.hexdigest()[:32]

    def contains_root(self, drive_root):
        return corpus.norm(drive_root or "") in self.drive_roots

    def contains_path(self, path):
        """Is a drive-absolute path inside the scope? Fail-closed: a path that
        is not under one of the scoped drive roots -- or that climbs out of one
        with '..' -- is out."""
        if _escapes(path):
            return False
        root = corpus.drive_root_of_path(path or "")
        return root is not None and root in self.drive_roots

    def corpus_id_for_path(self, path):
        """corpus_id for a drive-absolute path, refusing anything outside the
        scope so a caller cannot reach another drive by naming a file in it."""
        if not self.contains_path(path):
            raise ValueError("path outside the request scope: %s" % path)
        root = corpus.drive_root_of_path(path)
        return self.corpus_ids[self.drive_roots.index(root)]

    def as_dict(self):
        """Audit/status view -- what the box actually searched."""
        return {"source": self.source,
                "active_drive": self.active_root,
                "drives": list(self.drive_roots),
                "corpus_ids": list(self.corpus_ids)}

    def narrow(self, requested):
        """Restrict the search to `requested`, which may only be a SUBSET.

        The client picks where to look, never what it may reach: a drive the
        request was not already entitled to raises ValueError, which the
        caller turns into a 403. An empty selection means "no narrowing".
        """
        if not requested:
            return self
        wanted = set()
        for path in requested:
            root = corpus.norm(str(path or ""))
            if not root:
                continue
            if root not in self.drive_roots:
                raise ValueError("drive outside the request scope: %s" % path)
            wanted.add(root)
        if not wanted:
            return self
        roots = [root for root in self.drive_roots if root in wanted]
        ids = [self.corpus_ids[self.drive_roots.index(root)] for root in roots]
        return Scope(self.active_drive, roots, ids, self.source,
                     self.active_corpus_id)

    def __len__(self):
        return len(self.drive_roots)

    def __repr__(self):
        return "Scope(%s, %d drive(s), source=%s)" % (
            self.active_root, len(self.drive_roots), self.source)


def resolve(active_drive, extra_drives=None, source=None):
    """Build the scope for one request.

    `extra_drives` is where the ViVeSecBox entitlement list will arrive; while
    it is empty the scope is exactly the active drive, which is why this module
    can ship before the header contract is agreed. Duplicates and the active
    drive repeated in the list are collapsed, order is kept deterministic.
    """
    root = corpus.norm(active_drive or "")
    if not root:
        raise ValueError("missing active drive")
    roots = [root]
    ids = [corpus.corpus_id_of_drive(root)]
    for extra in extra_drives or ():
        candidate = corpus.norm(extra or "")
        if not candidate or candidate in roots:
            continue
        roots.append(candidate)
        ids.append(corpus.corpus_id_of_drive(candidate))
    if source is None:
        source = SOURCE_SINGLE if len(roots) == 1 else SOURCE_LIST
    return Scope(active_drive, roots, ids, source)


def rebuild(active_drive, drive_roots, source=None):
    """Restore a scope from a persisted root list (a queued job record).

    Unlike `resolve` the active drive is NOT added back: a job must run with
    exactly the scope its request was narrowed to, otherwise queueing an answer
    would silently widen it again.
    """
    roots, ids = [], []
    for path in drive_roots or ():
        root = corpus.norm(path or "")
        if not root or root in roots:
            continue
        ids.append(corpus.corpus_id_of_drive(root))
        roots.append(root)
    if not roots:
        return resolve(active_drive)
    if source is None:
        source = SOURCE_SINGLE if len(roots) == 1 else SOURCE_LIST
    return Scope(active_drive, roots, ids, source)


def resolve_request(active_drive, other_drives_header=None, user="",
                    entitlements=None, all_drives=None, on_warning=None,
                    asserted_drives=None):
    """Resolve one request's scope in the documented priority order: the
    drives asserted by a verified identity token, then the box header, then
    the entitlements file, then every known drive (demo only), then the active
    drive alone.

    A signed assertion is authoritative: when it lists drives -- even none --
    the unsigned header and the local fallbacks are not consulted.

    A malformed header is logged and DROPPED instead of failing the request.
    The header can only ever widen the scope, so ignoring it falls back to
    today's single-drive behaviour -- a contract mismatch must not take the box
    offline, and it can never leak.
    """
    warn = on_warning or (lambda _message: None)
    candidates, source = [], None
    if asserted_drives is not None:
        candidates = [str(p) for p in asserted_drives if p]
        source = SOURCE_TOKEN
        other_drives_header, entitlements, all_drives = None, None, None
    if other_drives_header:
        try:
            candidates = decode_other_drives(other_drives_header)
            source = SOURCE_HEADER
        except ValueError as e:
            warn(str(e))
            candidates = []
    if not candidates and entitlements is not None:
        granted = entitlements.drives_for(user)
        if granted:
            candidates, source = granted, SOURCE_ENTITLEMENTS
    if not candidates and all_drives is not None:
        known = all_drives() or []
        if known:
            candidates, source = known, SOURCE_ALL_DRIVES
    usable = []
    for path in candidates:
        candidate = corpus.norm(path or "")
        try:
            corpus.corpus_id_of_drive(candidate)
        except ValueError:
            warn("ignoring drive outside %s: %r" % (corpus.DRIVE_PREFIX, path))
            continue
        usable.append(candidate)
    return resolve(active_drive, usable, source if usable else None)
