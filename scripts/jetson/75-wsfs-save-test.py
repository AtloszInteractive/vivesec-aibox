#!/usr/bin/env python3
"""Save generated documents to the real ViVeSecBox drive over the ws-fs channel.

Exercises every docgen format end to end: render -> session store -> WS put-file
-> the box writes it into the drive and returns the stored path.
"""
import base64
import json
import sys
import urllib.error
import urllib.request

BASE = "http://127.0.0.1"
DRIVE = "/storage/drives/aiboxdev/"
USER = "wsfs-test"

# Overrides for probing what the box accepts: 75-...py <fmt> --drive X --user Y
argv = sys.argv[1:]
if "--drive" in argv:
    i = argv.index("--drive")
    DRIVE = argv[i + 1]
    del argv[i:i + 2]
if "--user" in argv:
    i = argv.index("--user")
    USER = argv[i + 1]
    del argv[i:i + 2]

TEXT = (
    "# AIBox save test\n\n"
    "This document was generated on the AIBox and pushed to the ViVeSec drive\n"
    "over the /api/v1/ws/fs channel.\n\n"
    "## Slide 1: Scope\n"
    "- format round-trip check\n"
    "- drive write-back path\n\n"
    "## Slide 2: Result\n"
    "- see the ack path returned by the box\n"
)


def post(path, obj):
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(obj).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "VVS-Drive": base64.urlsafe_b64encode(DRIVE.encode("utf-8")).decode("ascii"),
            "VVS-User": USER,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


st, status = post("/api/v1/status", {})
print("ws_fs connected:", (status or {}).get("ws_fs"), "| fs_ready:", (status or {}).get("fs_ready"))
print()

formats = argv or ["md", "txt", "pdf", "pptx"]
ok = 0
for fmt in formats:
    st, out = post("/api/v1/ui/save",
                   {"name": "aibox-save-test", "text": TEXT,
                    "title": "AIBox save test", "format": fmt})
    if st == 200 and isinstance(out, dict) and out.get("transferred"):
        ok += 1
        print("%-5s -> OK    %6d B  path=%s" % (fmt, out.get("size", 0), out.get("path")))
    else:
        print("%-5s -> FAIL  http=%s  %s" % (fmt, st, out))
print()
print("%d/%d format delivered to the drive" % (ok, len(formats)))
