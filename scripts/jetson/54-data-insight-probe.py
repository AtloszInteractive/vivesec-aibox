#!/usr/bin/env python3
"""Verify the data behind the /data (Corporate Data Insight) card.

Replicates exactly what the `platformInsight` server function does:
adapter /status + per-drive mirror rollup. Prints what is real and what has to
be rendered as "no data".
"""
import base64
import json
import urllib.request

BASE = "http://127.0.0.1:8088/api/v1"


def post(path, payload, headers=None):
    h = {"Content-Type": "application/json"}
    h.update(headers or {})
    req = urllib.request.Request(
        BASE + path, data=json.dumps(payload).encode("utf-8"), headers=h, method="POST")
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))


st = post("/status", {})
roots = [c for c in post("/index/get/children", {"path": "/storage/drives"}).get("children", [])
         if not c.get("file")]

total_bytes = 0
per_drive = []
by_ext = {}
for root in roots:
    name = root["path"].rstrip("/").split("/")[-1]
    drive = root["path"] + "/"
    out = post("/ui/query", {"action": "search", "mode": "files", "query": "files:"},
               {"VVS-Drive": base64.urlsafe_b64encode(drive.encode()).decode().rstrip("="),
                "VVS-User": "probe"})
    files = [f for f in (out.get("files") or []) if f.get("file")]
    b = sum(f.get("size") or 0 for f in files)
    total_bytes += b
    per_drive.append((name, len(files), b))
    for f in files:
        ext = f["path"].rsplit("/", 1)[-1].rsplit(".", 1)
        key = ext[1].lower() if len(ext) == 2 and len(ext[1]) <= 5 else "other"
        c = by_ext.setdefault(key, [0, 0])
        c[0] += 1
        c[1] += f.get("size") or 0


def mb(n):
    return "%.1f MB" % (n / 1048576.0)


print("== ViVeSecBox side (left column) ==")
print("  drives            :", len(roots))
print("  files (mirror)    :", st.get("mirror", {}).get("files"))
print("  folders           :", st.get("mirror", {}).get("directories"))
print("  data size         :", mb(total_bytes))
print("  storage mode      :", st.get("storage", {}).get("mode"))
print("  ws-fs connected   :", st.get("ws_fs", {}).get("connected"))
print("  users / messages  : NO DATA (box does not report)")
print("  last backup       : NO DATA (box does not report)")
print("  per drive         :", ", ".join("%s %d (%s)" % (n, c, mb(b)) for n, c, b in
                                         sorted(per_drive, key=lambda x: -x[1])))
print("  by type           :", ", ".join(".%s %d" % (k, v[0]) for k, v in
                                         sorted(by_ext.items(), key=lambda x: -x[1][0])))

idx = st.get("index", {})
print("== AI Box side (right column) ==")
print("  documents/pages   :", idx.get("documents"), "/", idx.get("pages"))
print("  chunks / corpora  :", idx.get("chunks"), "/", idx.get("corpora"))
print("  active sessions   :", st.get("sessions", {}).get("active"))
print("  generated files   :", st.get("files", {}).get("files"))
print("  features          :", ", ".join(st.get("features") or []))
print("  watchdog seconds  :", st.get("watchdog_seconds"))
print("  model/tok-s/GPU/W : NO DATA (no telemetry endpoint yet)")
print("  total requests    : NO DATA (not counted)")
