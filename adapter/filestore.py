"""Session-scoped generated-file store (aibox_more3 §1).

Generated files are ALWAYS saved to the user's session on the AIBox first;
only then is the transfer to the ViVeSecBox attempted. When the transfer is
rejected ("permission") or no channel is up, the copy stays here and the file
is offered for download over the tunneled connection instead. On successful
transfer the local copy is removed and the user is directed to the drive path.

Scope key = (VVS-User, VVS-Drive) — the same drive-scoped session identity the
conversation memory uses (no cross-drive visibility). Disk layout:

    <root>/<session_key>/<safe-name>

Thread-safe; pure stdlib. The store holds DERIVED (generated) content only —
never original drive files — and lives under /data (the LUKS-backed volume in
luks mode), honouring the cleartext-never-on-plain-disk invariant.
"""
import os
import re
import threading

from session import session_key

_SAFE_RE = re.compile(r"[^A-Za-z0-9._ ()\[\]\-\u00C0-\u024F\u0400-\u04FF]+")


def safe_name(name):
    """A filename usable under the store root: path separators and control
    characters stripped, no dot-files, never empty."""
    base = (name or "").replace("\\", "/").rsplit("/", 1)[-1].strip()
    base = _SAFE_RE.sub("_", base)
    base = base.lstrip(".")
    return base or "unnamed"


class FileStore:
    def __init__(self, root):
        self.root = root
        self._lock = threading.Lock()
        if root:
            try:
                os.makedirs(root, exist_ok=True)
            except OSError:
                # e.g. LUKS mode before unlock: /data is not mounted yet.
                # save() re-attempts the makedirs when actually writing.
                pass

    @classmethod
    def from_env(cls, env=None):
        env = env if env is not None else os.environ
        root = env.get("ADAPTER_FILES_DIR", "").strip()
        return cls(root) if root else cls(None)

    def _dir(self, user, drive):
        return os.path.join(self.root, session_key(user, drive))

    def enabled(self):
        return bool(self.root)

    def save(self, user, drive, name, content):
        """Persist one generated file for the session; returns the stored
        (sanitized) name. Overwrites a same-named earlier artifact."""
        if not self.root:
            raise RuntimeError("file store disabled (ADAPTER_FILES_DIR empty)")
        name = safe_name(name)
        d = self._dir(user, drive)
        with self._lock:
            os.makedirs(d, exist_ok=True)
            tmp = os.path.join(d, name + ".tmp")
            with open(tmp, "wb") as f:
                f.write(content)
            os.replace(tmp, os.path.join(d, name))
        return name

    def get(self, user, drive, name):
        """Stored bytes or None."""
        if not self.root:
            return None
        path = os.path.join(self._dir(user, drive), safe_name(name))
        try:
            with open(path, "rb") as f:
                return f.read()
        except OSError:
            return None

    def list(self, user, drive):
        """[{name, size, mtime}] for the session, newest first."""
        if not self.root:
            return []
        d = self._dir(user, drive)
        try:
            names = sorted(os.listdir(d))
        except OSError:
            return []
        out = []
        for n in names:
            if n.endswith(".tmp"):
                continue
            try:
                st = os.stat(os.path.join(d, n))
            except OSError:
                continue
            out.append({"name": n, "size": st.st_size,
                        "mtime": int(st.st_mtime)})
        out.sort(key=lambda f: (-f["mtime"], f["name"]))
        return out

    def delete(self, user, drive, name):
        """Remove one artifact (after a successful transfer). True if gone."""
        if not self.root:
            return False
        path = os.path.join(self._dir(user, drive), safe_name(name))
        try:
            os.remove(path)
            return True
        except OSError:
            return False

    def purge(self):
        """Factory reset: drop every stored artifact. Returns removed count."""
        if not self.root:
            return 0
        removed = 0
        with self._lock:
            for base, _dirs, files in os.walk(self.root, topdown=False):
                for f in files:
                    try:
                        os.remove(os.path.join(base, f))
                        removed += 1
                    except OSError:
                        pass
                if base != self.root:
                    try:
                        os.rmdir(base)
                    except OSError:
                        pass
        return removed

    def stats(self):
        if not self.root:
            return {"enabled": False}
        sessions = files = 0
        try:
            for entry in os.listdir(self.root):
                d = os.path.join(self.root, entry)
                if os.path.isdir(d):
                    sessions += 1
                    files += len([n for n in os.listdir(d)
                                  if not n.endswith(".tmp")])
        except OSError:
            pass
        return {"enabled": True, "root": self.root,
                "sessions": sessions, "files": files}
