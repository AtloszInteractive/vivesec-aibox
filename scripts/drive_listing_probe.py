"""What the drive panel actually lists: the UI calls the `#search files:` quick
action with an empty pattern, which reads the adapter's metadata mirror.
This checks each drive for the mirror total vs. what that call returns."""
import base64
import json
import urllib.request

ADAPTER = "http://100.88.253.38:8088"
DRIVES = ["finance", "engineering", "hr", "legal", "public"]


def post(path, payload, drive):
    hdr = {
        "Content-Type": "application/json",
        "VVS-Drive": base64.urlsafe_b64encode(drive.encode()).decode(),
        "VVS-User": "demo",
    }
    req = urllib.request.Request(ADAPTER + path,
                                 data=json.dumps(payload).encode("utf-8"),
                                 headers=hdr)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def walk(drive, path):
    """Count every file under path by recursing the mirror children."""
    files, dirs = 0, 0
    stack = [path]
    while stack:
        cur = stack.pop()
        for e in post("/api/v1/index/get/children", {"path": cur}, drive):
            if e.get("file"):
                files += 1
            else:
                dirs += 1
                stack.append(e["path"])
    return files, dirs


print("%-14s %-22s %-12s %s" % ("DRIVE", "mirror files (walk)", "UI list", "note"))
print("-" * 72)
for name in DRIVES:
    drive = "/storage/drives/%s/" % name
    total, dirs = walk(drive, "/storage/drives/" + name)
    listed = post("/api/v1/ui/query",
                  {"action": "search", "mode": "files", "query": "files:"},
                  drive)
    n = len(listed.get("files") or [])
    note = "TRUNCATED" if n < total else "complete"
    print("%-14s %-22s %-12s %s" % (name, "%d (in %d folders)" % (total, dirs), n, note))
