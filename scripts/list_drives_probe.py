"""Which drives does the box actually hold? The drives are ViVeSecBox paths in
the adapter's metadata mirror, not directories on the Jetson filesystem."""
import base64
import json
import urllib.request

ADAPTER = "http://100.88.253.38:8088"


def post(path, payload, drive=None):
    hdr = {"Content-Type": "application/json"}
    if drive:
        hdr["VVS-Drive"] = base64.urlsafe_b64encode(drive.encode()).decode()
        hdr["VVS-User"] = "demo"
    req = urllib.request.Request(ADAPTER + path,
                                 data=json.dumps(payload).encode("utf-8"),
                                 headers=hdr)
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))


roots = post("/api/v1/index/get/children", {"path": "/storage/drives"},
             drive="/storage/drives/finance/")
print("drives in the mirror:")
for e in roots:
    name = e.get("path", "").rstrip("/").rsplit("/", 1)[-1]
    kids = post("/api/v1/index/get/children", {"path": e["path"]},
                drive=e["path"] + "/")
    files = sum(1 for k in kids if k.get("file"))
    dirs = sum(1 for k in kids if not k.get("file"))
    print("  %-14s %s  (%d files, %d folders at root)"
          % (name, e.get("path"), files, dirs))
