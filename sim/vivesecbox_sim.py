"""ViVeSecBox simulator — the mock of the ViVeSec platform side.

Owns a local sample tree under `sim/drives/<drive name>/...`, maps each item to
its ViVeSecBox absolute path `/storage/drives/<drive name>/...`, and runs the
PUSH-based diff sync against the AIBox server:

  1. upsert directory entries
  2. for each local file: get(path); if missing OR mtime/size differ ->
     check(path,size,mtime,head) -> if token: content(token, raw bytes)
  3. reconcile deletions: any path in the AIBox subtree that is gone locally ->
     drop/tree (segment-aware), so a removed file/folder leaves the index.

Diff-based (not full re-upload), per-file two-phase, with a serial-vs-parallel
upload toggle (--parallel N) to test the AIBox under concurrent sync.

stdlib-only (urllib). Run examples:
    python sim/vivesecbox_sim.py sync
    python sim/vivesecbox_sim.py sync --drive "beta dev 2" --parallel 4
    python sim/vivesecbox_sim.py delete --drive "beta dev 2"
    python sim/vivesecbox_sim.py query --drive finance "What was Q4 revenue?"
"""
import argparse
import base64
import json
import os
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
DRIVES_ROOT = os.path.join(HERE, "drives")
STORAGE_PREFIX = "/storage/drives"  # ViVeSecBox virtual absolute path root

DEFAULT_AIBOX = os.environ.get("AIBOX_URL", "http://127.0.0.1:8088")
DEFAULT_USER = os.environ.get("VVS_USER", "u-0001")


# ---------------------------------------------------------------------------
# HTTP helpers (stdlib urllib)
# ---------------------------------------------------------------------------
def _post(url, body_bytes, content_type, headers=None, timeout=600):
    req = urllib.request.Request(url, data=body_bytes, method="POST")
    req.add_header("Content-Type", content_type)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def post_json(base, path, obj, headers=None):
    return _post(base + path, json.dumps(obj).encode("utf-8"),
                 "application/json", headers)


def post_raw(base, path, raw, headers=None):
    return _post(base + path, raw, "application/octet-stream", headers)


def get_json(url, headers=None, timeout=30):
    req = urllib.request.Request(url, method="GET")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


# ---------------------------------------------------------------------------
# Path mapping  local <-> ViVeSecBox virtual absolute path
# ---------------------------------------------------------------------------
def vpath_for(drive, rel):
    """Build the ViVeSecBox absolute path. Forward slashes always (the box is
    a Linux filesystem); preserve spaces in the drive name verbatim."""
    rel = rel.replace(os.sep, "/")
    base = "%s/%s" % (STORAGE_PREFIX, drive)
    return base if rel in ("", ".") else "%s/%s" % (base, rel)


def drive_root_vpath(drive):
    return "%s/%s" % (STORAGE_PREFIX, drive)


def list_drives():
    if not os.path.isdir(DRIVES_ROOT):
        return []
    return sorted(d for d in os.listdir(DRIVES_ROOT)
                  if os.path.isdir(os.path.join(DRIVES_ROOT, d)))


def walk_drive(drive):
    """Yield (kind, vpath, local_path) for every dir and file in a drive."""
    root = os.path.join(DRIVES_ROOT, drive)
    for cur, dirs, files in os.walk(root):
        dirs.sort()
        rel_dir = os.path.relpath(cur, root)
        yield ("dir", vpath_for(drive, rel_dir), cur)
        for name in sorted(files):
            lp = os.path.join(cur, name)
            rel = os.path.join(rel_dir, name) if rel_dir != "." else name
            yield ("file", vpath_for(drive, rel), lp)


def headers_for(drive, user):
    """VVS-User / VVS-Drive injection. VVS-Drive is the urlsafe base64 of the
    UTF-8 drive path (always ending in '/'), per the ViVeSec v2 contract -- so
    spaces / '?' / unicode travel safely in the HTTP header."""
    drive_path = "%s/%s/" % (STORAGE_PREFIX, drive)
    drive_val = base64.urlsafe_b64encode(drive_path.encode("utf-8")).decode("ascii")
    return {"VVS-User": user, "VVS-Drive": drive_val}


# ---------------------------------------------------------------------------
# Sync (diff-based, two-phase, serial or parallel)
# ---------------------------------------------------------------------------
def _file_meta(local_path):
    st = os.stat(local_path)
    return int(st.st_mtime * 1_000_000_000), st.st_size  # mtime ns, size bytes


def _head_b64(local_path, n=256):
    with open(local_path, "rb") as f:
        return base64.b64encode(f.read(n)).decode("ascii")


def _mirror_doc(resp):
    """The adapter returns the bare metadata object; tolerate the old envelope."""
    if isinstance(resp, dict) and "document" in resp and "path" not in resp:
        return resp.get("document")
    return resp if isinstance(resp, dict) else None


def _push_one(base, drive, vpath, local_path, hdrs, log):
    """Two-phase upload of a single file IFF it is new or changed."""
    mtime, size = _file_meta(local_path)
    existing = _mirror_doc(post_json(base, "/api/v1/index/get", {"path": vpath}, hdrs))
    if existing and existing.get("mtime") == mtime and existing.get("size") == size:
        log("  skip  (unchanged) %s" % vpath)
        return "skipped"
    resp = post_json(base, "/api/v1/index/upsert/file/check",
                     {"path": vpath, "size": size, "mtime": mtime,
                      "head": _head_b64(local_path)}, hdrs)
    token = resp.get("token")
    if not token:
        log("  meta  (not indexed) %s" % vpath)
        return "metadata-only"
    with open(local_path, "rb") as f:
        raw = f.read()
    out = post_raw(base, "/api/v1/index/upsert/file/content/" + token, raw, hdrs)
    log("  push  %s  -> %d chunks" % (vpath, out.get("chunks", 0)))
    return "pushed"


def sync_drive(base, drive, user, parallel=1, verbose=True):
    hdrs = headers_for(drive, user)
    log = (lambda m: print(m)) if verbose else (lambda m: None)
    print("[sync] drive '%s'  (parallel=%d, drive=%s/%s/)"
          % (drive, parallel, STORAGE_PREFIX, drive))

    items = list(walk_drive(drive))
    dirs = [v for (k, v, _l) in items if k == "dir"]
    files = [(v, lp) for (k, v, lp) in items if k == "file"]

    # 1) directory entries
    for v in dirs:
        post_json(base, "/api/v1/index/upsert/directory", {"path": v}, hdrs)

    # 2) files (diff-based, two-phase) -- serial or parallel
    if parallel <= 1:
        results = [_push_one(base, drive, v, lp, hdrs, log) for (v, lp) in files]
    else:
        with ThreadPoolExecutor(max_workers=parallel) as ex:
            results = list(ex.map(
                lambda vl: _push_one(base, drive, vl[0], vl[1], hdrs, log), files))

    # 3) reconcile deletions: index subtree paths gone locally -> drop/tree
    local_paths = set(dirs) | {v for (v, _l) in files}
    remote = post_json(base, "/api/v1/index/get/children",
                       {"path": drive_root_vpath(drive)}, hdrs)  # warm path
    removed = _reconcile_deletions(base, drive, local_paths, hdrs, log)

    pushed = results.count("pushed")
    skipped = results.count("skipped")
    meta = results.count("metadata-only")
    print("[sync] done: %d pushed, %d skipped, %d metadata-only, %d removed"
          % (pushed, skipped, meta, removed))
    return {"pushed": pushed, "skipped": skipped,
            "metadata_only": meta, "removed": removed}


def _reconcile_deletions(base, drive, local_paths, hdrs, log):
    """Walk the AIBox subtree via get/children; drop_tree anything not present
    locally. Children-first isn't required (drop_tree is recursive) but we only
    drop top-level orphans to minimize calls."""
    root = drive_root_vpath(drive)
    removed = 0
    stack = [root]
    seen = set()
    while stack:
        cur = stack.pop()
        children = post_json(base, "/api/v1/index/get/children",
                             {"path": cur}, hdrs).get("children", [])
        for meta in children:
            p = meta.get("path")
            if p in seen:
                continue
            seen.add(p)
            if p not in local_paths:
                out = post_json(base, "/api/v1/index/drop/tree",
                                {"path": p, "keep_exact": False}, hdrs)
                removed += out.get("removed", 0)
                log("  drop  %s  -> %d docs" % (p, out.get("removed", 0)))
            elif not meta.get("file"):
                stack.append(p)  # recurse into still-present directories
    return removed


def delete_drive(base, drive, user):
    """Whole-drive/corpus deletion: one drop/tree on the drive root."""
    hdrs = headers_for(drive, user)
    out = post_json(base, "/api/v1/index/drop/tree",
                    {"path": drive_root_vpath(drive), "keep_exact": False}, hdrs)
    print("[delete] drive '%s' -> %d documents removed" % (drive, out.get("removed", 0)))
    return out


def query_drive(base, drive, user, query, top_k=4):
    hdrs = headers_for(drive, user)
    out = post_json(base, "/api/v1/ui/query",
                    {"query": query, "top_k": top_k}, hdrs)
    print("[query] drive '%s'  user=%s" % (drive, out.get("user", "")))
    for h in out.get("hits", []):
        print("  %d. score=%.4f  %s" % (h["rank"], h["score"], h["path"]))
        print("       %s" % h["snippet"][:160])
    if not out.get("hits"):
        print("  (no hits in this drive)")
    return out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="ViVeSecBox simulator")
    ap.add_argument("command", choices=["sync", "delete", "query", "drives"])
    ap.add_argument("text", nargs="?", default="", help="query text (query cmd)")
    ap.add_argument("--aibox", default=DEFAULT_AIBOX)
    ap.add_argument("--user", default=DEFAULT_USER)
    ap.add_argument("--drive", default=None, help="limit to one drive (default: all)")
    ap.add_argument("--parallel", type=int, default=1)
    ap.add_argument("--top-k", type=int, default=4)
    ap.add_argument("--drives-root", default=None,
                    help="folder whose sub-dirs are drives (default: sim/drives)")
    args = ap.parse_args()

    global DRIVES_ROOT
    if args.drives_root:
        DRIVES_ROOT = os.path.abspath(args.drives_root)

    if args.command == "drives":
        for d in list_drives():
            print(d)
        return

    drives = [args.drive] if args.drive else list_drives()
    if not drives:
        print("No drives found under %s" % DRIVES_ROOT)
        sys.exit(1)

    if args.command == "sync":
        for d in drives:
            sync_drive(args.aibox, d, args.user, args.parallel)
    elif args.command == "delete":
        for d in drives:
            delete_drive(args.aibox, d, args.user)
    elif args.command == "query":
        if not args.text:
            print("query needs text, e.g.  query --drive finance \"Q4 revenue?\"")
            sys.exit(1)
        for d in drives:
            query_drive(args.aibox, d, args.user, args.text, args.top_k)


if __name__ == "__main__":
    main()
