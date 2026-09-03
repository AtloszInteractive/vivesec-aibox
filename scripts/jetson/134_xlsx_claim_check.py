#!/usr/bin/env python3
"""Search the xlsx sources the model cited for the Q3 figures it claimed.

An xlsx is a zip of XML, so a plain grep over the corpus cannot see inside it --
and calling something a hallucination without checking there would be sloppy.
"""
import glob
import os
import re
import zipfile

NEEDLES = ("15.6", "15,6", "not closed", "Q3")
ROOT = os.path.expanduser("~/demo-corpus/out/drives")

for path in glob.glob(os.path.join(ROOT, "**", "*.xlsx"), recursive=True):
    if "Budget" not in path and "Management" not in path and "KPI" not in path:
        continue
    print("==", path)
    with zipfile.ZipFile(path) as z:
        blob = ""
        for name in z.namelist():
            if name.endswith(".xml"):
                blob += z.read(name).decode("utf-8", "replace")
    text = re.sub(r"<[^>]+>", " ", blob)
    for needle in NEEDLES:
        hits = [m.start() for m in re.finditer(re.escape(needle), text)]
        print("   %-12s %d hit(s)" % (needle, len(hits)))
        for h in hits[:3]:
            print("      ...%s..." % " ".join(text[max(0, h - 90):h + 90].split()))
