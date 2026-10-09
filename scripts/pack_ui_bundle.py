"""Pack the built web UI into the ui-output.tgz the installer expects.

The Jetson does not run the web toolchain, so the interface is compiled here
and shipped as a tarball. The layout is fixed: `.output` must sit at the root
of the archive, because the installer extracts it and then looks for
`.output/server/index.mjs`. Packing from inside `.output` produces an archive
that fails the image build phase.

Usage:
  python scripts/pack_ui_bundle.py [output.tgz] [--build]

  --build   run `npm run build` first (otherwise an existing .output is packed)

The bundle carries .output/public/version.json (the release identity written by
vite.config.ts); 10-build-images.sh labels the UI image from it.
"""
import json
import os
import subprocess
import sys
import tarfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
SOURCE = os.path.join(ROOT, ".output")
ENTRYPOINT = os.path.join("server", "index.mjs")


def build():
    print("running: npm run build")
    npm = "npm.cmd" if os.name == "nt" else "npm"
    result = subprocess.run([npm, "run", "build"], cwd=ROOT)
    if result.returncode != 0:
        print("npm run build failed (exit %d)" % result.returncode)
    return result.returncode


def main():
    arguments = sys.argv[1:]
    should_build = "--build" in arguments
    arguments = [a for a in arguments if a != "--build"]
    out = os.path.abspath(arguments[0] if arguments else "ui-output.tgz")

    if should_build:
        code = build()
        if code != 0:
            return code

    if not os.path.isdir(SOURCE):
        print("missing build dir: %s (run: npm run build)" % SOURCE)
        return 1
    if not os.path.isfile(os.path.join(SOURCE, ENTRYPOINT)):
        print("incomplete build: %s is missing" % os.path.join(".output", ENTRYPOINT))
        return 1

    count = 0
    with tarfile.open(out, "w:gz") as archive:
        for root, dirs, files in os.walk(SOURCE):
            dirs.sort()
            for name in sorted(files):
                full = os.path.join(root, name)
                rel = os.path.relpath(full, ROOT).replace(os.sep, "/")
                archive.add(full, arcname=rel)
                count += 1

    size = os.path.getsize(out)
    print("%s - %d files, %.2f MB" % (out, count, size / 1024.0 / 1024.0))

    with tarfile.open(out) as archive:
        names = archive.getnames()
    if not all(n == ".output" or n.startswith(".output/") for n in names):
        print("ERROR: the archive root is not .output - the installer would reject it")
        return 1
    if ".output/" + ENTRYPOINT.replace(os.sep, "/") not in names:
        print("ERROR: .output/%s is missing from the archive" % ENTRYPOINT.replace(os.sep, "/"))
        return 1
    print("layout OK: .output at the archive root, entrypoint present")
    if ".output/public/version.json" not in names:
        print("ERROR: .output/public/version.json is missing - rebuild with the current vite.config.ts")
        return 1
    with open(os.path.join(SOURCE, "public", "version.json"), encoding="utf-8") as handle:
        print("ui version: %s" % json.load(handle).get("label"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
