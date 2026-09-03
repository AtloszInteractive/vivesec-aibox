"""Are the numbers flagged as ungrounded actually dates the model reformatted?
Print the retrieved snippets and look for the flagged digit strings."""
import base64
import json
import re
import time
import urllib.request

ADAPTER = "http://100.88.253.38:8088"
DRIVE = "/storage/drives/engineering/"
HDR = {
    "VVS-Drive": base64.urlsafe_b64encode(DRIVE.encode("utf-8")).decode("ascii"),
    "VVS-User": "demo",
}


def post(path, payload, headers=None):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(ADAPTER + path, data=data,
                                 headers={"Content-Type": "application/json",
                                          **(headers or {})})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read().decode("utf-8"))


res = post("/api/v1/ui/ask", {"query": "#report", "lang": "English"}, HDR)
job = res["job_id"]
out = None
for _ in range(24):
    res = post("/api/v1/ui/poll", {"job_id": job, "timeout": 25})
    if res.get("status") == "done":
        out = res.get("result") or res
        break
    time.sleep(1)

conf = out.get("confidence") or {}
flagged = (conf.get("components", {}).get("context_adherence") or {}).get("ungrounded") or []
print("flagged:", flagged, "| score:", conf.get("score"))

snippets = " ".join((h.get("snippet") or "") for h in out.get("hits") or [])
print("\ndates visible in the retrieved snippets:")
print(" ", sorted(set(re.findall(r"\d{4}-\d{2}-\d{2}", snippets)))[:20])
print("  months:", sorted(set(re.findall(r"\d{4}-\d{2}(?!-)", snippets)))[:20])

def norm(s):
    return re.sub(r"\D", "", s)

have = set()
for h in out.get("hits") or []:
    for m in re.finditer(r"\d[\d  ,.:/-]*\d|\d", h.get("snippet") or ""):
        d = norm(m.group(0))
        if d:
            have.add(d)
print("\nper flagged value — is it present in the (truncated) snippets?")
for f in flagged:
    hit = any(f == h or f in h for h in have)
    print("  %-10s %s" % (f, "PRESENT" if hit else "absent from snippets"))
print("\nNB: hits[] snippets are the first 200 chars only, so 'absent' is a hint,")
print("not proof — but a date-shaped value is what the model reformats.")
