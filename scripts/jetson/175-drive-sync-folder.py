#!/usr/bin/env python3
"""Replay a local folder into the AI Box as a drive, the way the ViVeSecBox
syncs it: through the ADAPTER's drive-sync endpoints, so both the metadata
mirror (UI drive list / file explorer) and the RAG index are filled.

    POST /api/v1/index/upsert/directory        {path}
    POST /api/v1/index/upsert/file/check       {path,size,mtime,head}
    POST /api/v1/index/upsert/file/content/<t> raw bytes

Resumable: files whose (size, mtime) already succeeded are skipped via the
state file, so it can be re-run after an interruption or with a different
extension filter (e.g. spreadsheets first, everything else later).

    python3 175-drive-sync-folder.py --root ~/aiboxteszt --drive aiboxteszt \
        --include-ext xlsx,xlsm
    python3 175-drive-sync-folder.py --root ~/aiboxteszt --drive aiboxteszt \
        --exclude-ext xlsx,xlsm

Python 3.8 / stdlib only (runs on the box itself).
"""
import argparse
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request
from collections import Counter


def post(url, data, ctype, timeout):
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", ctype)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read().decode("utf-8", "replace")
            return r.status, (json.loads(body) if body else {})
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(body)
        except ValueError:
            return e.code, {"error": body[:200]}
    except Exception as exc:  # noqa: BLE001
        return 0, {"error": str(exc)[:200]}


def load_state(path):
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_state(path, state):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f)
    os.replace(tmp, path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True, help="local folder = drive root")
    ap.add_argument("--drive", required=True, help="drive name under /storage/drives/")
    ap.add_argument("--adapter", default="http://127.0.0.1:8088")
    ap.add_argument("--include-ext", default="", help="comma list, e.g. xlsx,xlsm")
    ap.add_argument("--exclude-ext", default="", help="comma list")
    ap.add_argument("--max-bytes", type=int, default=20 * 1024 * 1024)
    ap.add_argument("--state", default=None, help="resume state file")
    ap.add_argument("--content-timeout", type=int, default=2400)
    ap.add_argument("--limit", type=int, default=0, help="stop after N uploads (0 = all)")
    args = ap.parse_args()

    root = os.path.abspath(args.root)
    drive_root = "/storage/drives/%s" % args.drive.strip("/")
    inc = {"." + e.strip().lower().lstrip(".") for e in args.include_ext.split(",") if e.strip()}
    exc = {"." + e.strip().lower().lstrip(".") for e in args.exclude_ext.split(",") if e.strip()}
    state_path = args.state or os.path.join(os.path.expanduser("~"), ".drive-sync-%s.json" % args.drive)
    state = load_state(state_path)

    def box_path(local):
        rel = os.path.relpath(local, root).replace(os.sep, "/")
        return drive_root if rel == "." else drive_root + "/" + rel

    # Directories first, shallowest first, so the mirror tree is complete even
    # if the run is interrupted midway.
    dirs, files = [], []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        dirs.append(dirpath)
        for name in sorted(filenames):
            ext = os.path.splitext(name)[1].lower()
            if inc and ext not in inc:
                continue
            if ext in exc:
                continue
            files.append(os.path.join(dirpath, name))
    print("drive %s  dirs %d  files %d (filter inc=%s exc=%s)" % (
        drive_root, len(dirs), len(files), sorted(inc) or "-", sorted(exc) or "-"), flush=True)

    dir_ok = 0
    for d in dirs:
        key = "dir:" + box_path(d)
        if key in state:
            dir_ok += 1
            continue
        st, res = post(args.adapter + "/api/v1/index/upsert/directory",
                       json.dumps({"path": box_path(d)}).encode("utf-8"), "application/json", 60)
        if st == 200:
            state[key] = 1
            dir_ok += 1
        else:
            print("DIR FAIL %s -> %s %s" % (box_path(d), st, res), flush=True)
    save_state(state_path, state)
    print("directories ok: %d/%d" % (dir_ok, len(dirs)), flush=True)

    reasons = Counter()
    per_ext = {}
    uploaded = 0
    t0 = time.time()
    for i, local in enumerate(files, 1):
        ext = os.path.splitext(local)[1].lower() or "(none)"
        bucket = per_ext.setdefault(ext, Counter())
        stt = os.stat(local)
        size, mtime = stt.st_size, int(stt.st_mtime_ns)
        bpath = box_path(local)
        prev = state.get(bpath)
        if prev and prev.get("size") == size and prev.get("mtime") == mtime and prev.get("ok"):
            bucket["skipped_done"] += 1
            continue
        if size > args.max_bytes:
            bucket["too_large_local"] += 1
            reasons["too_large_local"] += 1
            continue
        with open(local, "rb") as f:
            raw = f.read()
        st, res = post(args.adapter + "/api/v1/index/upsert/file/check",
                       json.dumps({"path": bpath, "size": size, "mtime": mtime,
                                   "head": base64.b64encode(raw[:256]).decode("ascii")}).encode("utf-8"),
                       "application/json", 120)
        if st != 200:
            bucket["check_error"] += 1
            reasons["check_error"] += 1
            print("CHECK FAIL %s -> %s %s" % (bpath, st, res), flush=True)
            continue
        token = res.get("token")
        if not token:
            reason = res.get("reason") or "no_token"
            bucket[reason] += 1
            reasons[reason] += 1
            state[bpath] = {"size": size, "mtime": mtime, "ok": True, "reason": reason}
            continue
        t = time.time()
        st, res = post(args.adapter + "/api/v1/index/upsert/file/content/" + token,
                       raw, "application/octet-stream", args.content_timeout)
        dt = time.time() - t
        if st == 200:
            bucket["indexed"] += 1
            bucket["chunks"] += int(res.get("chunks") or 0)
            bucket["pages"] += int(res.get("pages") or 0)
            state[bpath] = {"size": size, "mtime": mtime, "ok": True,
                            "chunks": res.get("chunks"), "pages": res.get("pages")}
            uploaded += 1
            print("[%d/%d] %5.1fs p=%-3s c=%-5s %s" % (
                i, len(files), dt, res.get("pages"), res.get("chunks"), bpath[len(drive_root):]), flush=True)
        else:
            bucket["content_error"] += 1
            reasons["content_error"] += 1
            state[bpath] = {"size": size, "mtime": mtime, "ok": False, "error": str(res)[:200]}
            print("CONTENT FAIL %s -> %s %s" % (bpath, st, res), flush=True)
        if i % 20 == 0:
            save_state(state_path, state)
        if args.limit and uploaded >= args.limit:
            print("limit reached", flush=True)
            break
    save_state(state_path, state)

    print()
    print("elapsed %.0fs  uploaded %d" % (time.time() - t0, uploaded))
    print("%-8s %8s %8s %8s  %s" % ("ext", "indexed", "pages", "chunks", "other"))
    for ext in sorted(per_ext, key=lambda e: -per_ext[e]["indexed"]):
        b = per_ext[ext]
        other = {k: v for k, v in b.items() if k not in ("indexed", "pages", "chunks")}
        print("%-8s %8d %8d %8d  %s" % (ext, b["indexed"], b["pages"], b["chunks"], dict(other) or ""))
    if reasons:
        print("skip/error reasons:", dict(reasons))
    print("state:", state_path)


if __name__ == "__main__":
    sys.exit(main())
