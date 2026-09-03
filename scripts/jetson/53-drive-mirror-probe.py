#!/usr/bin/env python3
"""List what the adapter mirror knows about the demo drives."""
import json
import urllib.request

BASE = "http://127.0.0.1:8088/api/v1"


def post(path, payload):
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode("utf-8"))


st = post("/status", {})
print("mirror:", st.get("mirror"))
print("index :", st.get("index"))

for root in ("/storage/drives", "/storage/drives/"):
    ch = post("/index/get/children", {"path": root})
    print("children of", root, "->", len(ch.get("children") or []))
    for c in (ch.get("children") or [])[:20]:
        print("   ", c)

# What does the mirror hold overall (files-mode search on each drive root)?
import base64
for name in ("engineering", "finance", "hr", "legal", "public"):
    drive = "/storage/drives/%s/" % name
    req = urllib.request.Request(
        BASE + "/ui/query",
        data=json.dumps({"action": "search", "mode": "files", "query": "files:"}).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "VVS-Drive": base64.urlsafe_b64encode(drive.encode()).decode().rstrip("="),
            "VVS-User": "probe",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            out = json.loads(r.read().decode("utf-8"))
        files = out.get("files") or []
        print("%-12s files=%d  first=%s" % (name, len(files), files[0]["path"] if files else "-"))
    except Exception as e:  # noqa: BLE001
        print("%-12s ERROR %s" % (name, e))
