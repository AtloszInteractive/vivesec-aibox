"""Pack the supported files of a corpus folder into a UTF-8 (pax) tar."""
import os
import sys
import tarfile

src, out = os.path.abspath(sys.argv[1]), sys.argv[2]
# Windows MAX_PATH: the corpus has paths beyond 260 chars.
walk_root = "\\\\?\\" + src if os.name == "nt" else src
EXCLUDE = {".mp4", ".zip", ".gz", ".rar", ".avi", ".png", ".bmpr", ".xmind", ".graphml",
           ".jpg", ".jpeg", ".gif", ".7z", ".iso", ".exe", ".msi"}
n = skipped = 0
total = 0
with tarfile.open(out, "w", format=tarfile.PAX_FORMAT, encoding="utf-8") as tf:
    for dirpath, dirnames, filenames in os.walk(walk_root):
        dirnames.sort()
        for name in sorted(filenames):
            full = os.path.join(dirpath, name)
            if os.path.splitext(name)[1].lower() in EXCLUDE:
                skipped += 1
                continue
            rel = full[len(walk_root):].lstrip("\\/")
            arc = (os.path.basename(src) + "/" + rel).replace(os.sep, "/")
            tf.add(full, arcname=arc, recursive=False)
            n += 1
            total += os.path.getsize(full)
print("packed %d files, %.1f MB, skipped %d" % (n, total / 1e6, skipped))
