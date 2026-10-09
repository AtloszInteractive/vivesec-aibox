"""Zip the embed SPA build for ViVeSec.

PowerShell's Compress-Archive stores Windows separators (`assets\\index.js`),
which is invalid per the ZIP spec and unpacks as literal filenames on Linux.
zipfile always writes forward slashes.

The default archive name carries the release label from dist/client/version.json
(written by vite.config.ts), e.g. aibox-ui-embed-26.10.1.zip, so a delivered
bundle can be matched to the box release it belongs to.
"""
import json
import os
import sys
import zipfile

SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "dist", "client")


def main():
    src = os.path.abspath(SRC)
    if not os.path.isdir(src):
        print("missing build dir: %s (run: npm run build:embed)" % src)
        return 1
    version_file = os.path.join(src, "version.json")
    if not os.path.isfile(version_file):
        print("missing %s (rebuild with the current vite.config.ts)" % version_file)
        return 1
    with open(version_file, encoding="utf-8") as handle:
        label = json.load(handle).get("label") or "unknown"
    out = sys.argv[1] if len(sys.argv) > 1 else "aibox-ui-embed-%s.zip" % label
    count = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for root, _dirs, files in os.walk(src):
            for name in sorted(files):
                full = os.path.join(root, name)
                rel = os.path.relpath(full, src).replace(os.sep, "/")
                z.write(full, rel)
                count += 1
    size = os.path.getsize(out)
    print("%s — %d files, %.2f MB, ui version %s" % (out, count, size / 1024.0 / 1024.0, label))
    with zipfile.ZipFile(out) as z:
        bad = [n for n in z.namelist() if "\\" in n]
        print("entries with backslashes: %d" % len(bad))
        for n in z.namelist():
            print("  %s" % n)
    return 0


if __name__ == "__main__":
    sys.exit(main())
