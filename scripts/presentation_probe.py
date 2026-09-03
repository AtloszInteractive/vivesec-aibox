"""Ask the box for an F3 presentation outline and show whether the UI parser
would turn it into slides (live-parse.ts parseSlides)."""
import base64
import json
import re
import sys
import time
import urllib.request

ADAPTER = "http://100.88.253.38:8088"
DRIVE = "/storage/drives/finance/"
FILES = [
    "/storage/drives/finance/exec/Board_Pack_2026-09-16_draft.md",
    "/storage/drives/finance/reports/KPI_Dashboard_2026-H1.md",
]


def post(path, payload, headers=None):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(ADAPTER + path, data=data,
                                 headers={"Content-Type": "application/json",
                                          **(headers or {})})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.status, json.loads(r.read().decode("utf-8"))


hdr = {
    "VVS-Drive": base64.urlsafe_b64encode(DRIVE.encode("utf-8")).decode("ascii"),
    "VVS-User": "demo",
}
names = ", ".join(f.rsplit("/", 1)[-1] for f in FILES)
body = {
    "query": "based on %s" % names,
    "lang": "English",
    "action": "presentation",
    "audience": "the board of directors",
    "purpose": "Board update",
    "files": FILES,
}
code, res = post("/api/v1/ui/ask", body, hdr)
print("ask ->", code, res.get("job_id"))
job = res["job_id"]
for _ in range(20):
    code, res = post("/api/v1/ui/poll", {"job_id": job, "timeout": 25})
    if res.get("status") == "done":
        break
    print("  polling...", res.get("status"))
    time.sleep(1)

out = res.get("result") or res
answer = out.get("answer") or ""
print("=" * 70)
print("confidence:", out.get("confidence", {}).get("score"),
      out.get("confidence", {}).get("band"))
print("citations:", [c.get("path") for c in out.get("citations", [])])
print("=" * 70)
print(answer)
print("=" * 70)

# Mirror of live-parse.ts parseSlides
RE = re.compile(r"^\s*(?:[#*\-\s]*)slide\s*(\d+)\s*[:.\-\u2013]\s*(.+)$", re.I)
LABEL = re.compile(
    r"^\*{0,2}(title|subtitle|core message|key message|message|"
    r"suggested visuals?(?:\s*[/&]\s*data points?)?|"
    r"visuals?(?:\s*[/&]\s*data points?)?|data points?)\*{0,2}\s*[:\-\u2013]\s*", re.I)
EMPTY = re.compile(r"^(none|none specified|n/a|not specified|\?|-)\.?$", re.I)
BULLET = re.compile(r"^([-*\u2022]\s+|\d+[.)]\s+)")

slides = []
lines = answer.split("\n")
for i, line in enumerate(lines):
    m = RE.match(line)
    if not m:
        continue
    cells = [c.replace("**", "").strip() for c in m.group(2).split("|")]
    title = cells.pop(0) if cells else ""
    raw = [c for c in cells if c]
    for j in range(i + 1, len(lines)):
        if len(raw) >= 8 or RE.match(lines[j]):
            break
        s = lines[j].strip()
        if not BULLET.match(s):
            if s == "":
                continue
            break
        raw.append(BULLET.sub("", s).replace("**", "").strip())
    subtitle = None
    bullets = []
    for item in raw:
        lab = LABEL.match(item)
        value = item[lab.end():].strip() if lab else item
        if not value or EMPTY.match(value):
            continue
        kind = lab.group(1).lower() if lab else None
        if kind in ("title", "subtitle") and subtitle is None:
            if value.lower() != title.lower():
                subtitle = value
            continue
        bullets.append(value)
    if title:
        slides.append({"title": title, "subtitle": subtitle, "bullets": bullets[:6]})

print("PARSED SLIDES:", len(slides), "(deck card needs >= 2)")
for s in slides:
    print(" -", s["title"], "|| sub:", s["subtitle"])
    for b in s["bullets"]:
        print("      *", b)
