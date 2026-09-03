"""Drive -> corpus_id derivation for the adapter.

The ViVeSec v2 front speaks in absolute ViVeSecBox paths
(`/storage/drives/<drive>/...`) and a `VVS-Drive` header
(`/storage/drives/<drive>/`). The RAG contract speaks in `corpus_id`. This
module is the single place that maps a drive root to a stable corpus_id.

Collision-free by design: the corpus_id carries a hash of the *full normalized
drive root*, so a drive literally named `beta-dev-2` and a drive named
`beta dev 2` (whose readable slug also becomes `beta-dev-2`) never collapse to
the same corpus. The readable slug is kept as a human-friendly prefix only.
"""
import base64
import hashlib
import os
import re

# The virtual absolute root under which every drive lives on the ViVeSecBox.
DRIVE_PREFIX = os.environ.get("ADAPTER_DRIVE_PREFIX", "/storage/drives")


def norm(path):
    """Strip a single trailing slash (the VVS-Drive header ends in '/',
    stored paths do not), preserving a bare root."""
    if not path:
        return path
    if len(path) > 1 and path.endswith("/"):
        return path.rstrip("/")
    return path


def decode_vvs_drive(raw):
    """Decode a `VVS-Drive` header value to its UTF-8 path.

    Per ViVeSec v2 the ViVeSecBox sends the drive path as urlsafe base64 of the
    UTF-8 bytes, so spaces / '?' / unicode travel safely in an HTTP header
    (e.g. `L3N0b3JhZ2UvZHJpdmVzL8WRemlrw6lzIGJldHM_Lw==` -> `/storage/drives/őzikés bets?/`).
    Padding is tolerated. Raises ValueError on a malformed value.
    """
    if not raw:
        raise ValueError("empty VVS-Drive header")
    s = raw.strip()
    s += "=" * ((-len(s)) % 4)  # restore stripped base64 padding
    try:
        return base64.urlsafe_b64decode(s.encode("ascii")).decode("utf-8")
    except Exception as e:  # noqa: BLE001
        raise ValueError("invalid VVS-Drive encoding (expected urlsafe base64): %s" % e)


def _slug(name):
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return s or "drive"


def drive_root_of_path(path):
    """Return the drive root for an absolute path, or None if the path is not
    under the drive prefix or is the bare prefix itself."""
    p = norm(path)
    prefix = norm(DRIVE_PREFIX)
    if p == prefix:
        return None
    if not p.startswith(prefix + "/"):
        return None
    rest = p[len(prefix) + 1:]
    if not rest:
        return None
    drive_name = rest.split("/", 1)[0]
    return prefix + "/" + drive_name


def corpus_id_of_drive_root(drive_root):
    root = norm(drive_root)
    name = root.rsplit("/", 1)[-1]
    digest = hashlib.sha1(root.encode("utf-8")).hexdigest()[:8]
    return "%s-%s" % (_slug(name), digest)


def corpus_id_of_path(path):
    """corpus_id for a sync path (file/dir). Raises ValueError if the path is
    not under the drive prefix."""
    root = drive_root_of_path(path)
    if root is None:
        raise ValueError("path not under drive prefix %s: %s" % (DRIVE_PREFIX, path))
    return corpus_id_of_drive_root(root)


def corpus_id_of_drive(vvs_drive):
    """corpus_id for a `VVS-Drive` header value (`/storage/drives/<name>/`)."""
    root = norm(vvs_drive)
    if not root or not root.startswith(norm(DRIVE_PREFIX) + "/"):
        raise ValueError("invalid VVS-Drive: %s" % vvs_drive)
    return corpus_id_of_drive_root(root)
