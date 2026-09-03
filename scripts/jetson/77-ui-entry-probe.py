#!/usr/bin/env python3
"""Check the endpoints the ViVeSecBox uses when opening the AIBox UI."""
import base64
import urllib.error
import urllib.request

BASE = "http://127.0.0.1"
DRIVE = "/storage/drives/aiboxdev/"
USER = "28744CZP27222"

HDRS = {
    "VVS-Drive": base64.urlsafe_b64encode(DRIVE.encode("utf-8")).decode("ascii"),
    "VVS-User": USER,
    "VVS-Session": "probe-session-1",
}


def get(path, headers=None):
    req = urllib.request.Request(BASE + path, method="GET")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.read().decode("utf-8", "replace")[:200]
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")[:200]


print("GET /api/v1/ui-version      ->", *get("/api/v1/ui-version"))
print("GET /api/v1/ui/init         ->", *get("/api/v1/ui/init", HDRS))
print("GET /api/v1/ui/init (no hdr)->", *get("/api/v1/ui/init"))
