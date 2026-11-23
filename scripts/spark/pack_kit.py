#!/usr/bin/env python3
"""Pack the Matrix AI kit for a DGX Spark (E07 M1): source tree + web UI bundle.

    python scripts/spark/pack_kit.py matrix-kit.tgz --ui ui-output.tgz

Run from the repository root on the build machine, after
`python scripts/pack_ui_bundle.py ui-output.tgz --build` of the same commit.
The archive holds:

    KIT.json         version label, commit, sha256 of every file
    ui-output.tgz    the web UI bundle (.output at its root)
    app/             the Matrix UI for the bootloader (--app dist/matrix/app, from
                     `npm run build:matrix`), served by the tunnel gateway
    wheels/          the update client's libraries for the Spark (--wheels)
    src/...          what the box builds and installs: the services, the tunnel
                     gateway, the update client (with --trusted-root its channel's
                     root metadata), the Spark installer and the kit scripts it calls

The release identity (E01) is stamped into the source first, as
172-release-box.py pack does. This kit builds the images on the box; a signed
release with built images comes from pack_release.py on a build Spark.
"""
import argparse
import hashlib
import io
import json
import subprocess
import sys
import tarfile
import time
from pathlib import Path

SOURCE_PATHS = ("adapter", "rag_service", "poc", "auth_service", "matrix_tunnel", "matrix_updater", "graph_service",
                "scripts/spark",
                "scripts/release/build_info.py", "scripts/jetson/install/10-build-images.sh",
                "scripts/jetson/Dockerfile.ui-runtime", "scripts/jetson/standalone/auth_deploy.py",
                "scripts/jetson/standalone/clamav_deploy.py", "VERSION")
STAMPED = ("adapter/build_info.json", "rag_service/build_info.json")
EXECUTABLE = (".sh", "spark_install.py")


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def ui_version(bundle):
    with tarfile.open(bundle, "r:gz") as archive:
        try:
            member = archive.extractfile(".output/public/version.json")
        except KeyError:
            member = None
        if member is None:
            raise SystemExit("%s carries no .output/public/version.json" % bundle)
        return json.load(member)


def source_files():
    listed = subprocess.check_output(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard",
                                      "--", *SOURCE_PATHS]).decode("utf-8").split("\0")
    files = sorted((set(listed) | set(STAMPED)) - {""})
    return [path for path in files if Path(path).is_file()
            and "__pycache__" not in path and not path.endswith(".pyc")]


def add(archive, name, data, mode=0o644):
    info = tarfile.TarInfo(name)
    info.size, info.mtime, info.mode = len(data), int(time.time()), mode
    archive.addfile(info, io.BytesIO(data))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("destination")
    parser.add_argument("--ui", required=True, help="ui-output.tgz of the same commit")
    parser.add_argument("--app", help="dist/matrix/app of the same commit (npm run build:matrix)")
    parser.add_argument("--wheels", help="aarch64 wheels of matrix_updater/requirements.txt (pip download, "
                                         "see scripts/spark/README.md); needed for MATRIX_UPDATE=on")
    parser.add_argument("--trusted-root", help="the update channel's root metadata (matrix_repo.py init)")
    args = parser.parse_args(argv)
    if not Path("VERSION").is_file() or not Path("scripts/spark").is_dir():
        raise SystemExit("run from the repository root")
    subprocess.check_call([sys.executable, "scripts/release/build_info.py", "stamp"])
    build = json.loads(Path("adapter/build_info.json").read_text(encoding="utf-8"))
    ui = ui_version(args.ui)
    if ui.get("version") != build.get("version"):
        raise SystemExit("UI bundle version %s differs from the source version %s"
                         % (ui.get("version"), build.get("version")))
    if ui.get("commit") != build.get("commit"):
        print("WARNING: UI bundle commit %s differs from the source commit %s"
              % (ui.get("commit"), build.get("commit")))
    files = source_files()
    ui_bytes = Path(args.ui).read_bytes()
    app_files = {}
    if args.app:
        app = json.loads((Path(args.app) / "manifest.json").read_text(encoding="utf-8"))
        if app.get("version") != build.get("version"):
            raise SystemExit("app version %s differs from the source version %s"
                             % (app.get("version"), build.get("version")))
        app_files = {name: (Path(args.app) / name).read_bytes() for name in ("manifest.json", "bundle.json")}
        if sha256(app_files["bundle.json"]) != app["bundle"]["sha256"]:
            raise SystemExit("%s/bundle.json does not match its manifest" % args.app)
    extra = {}
    if args.wheels:
        wheels = sorted(Path(args.wheels).glob("*.whl"))
        if not any(w.name.startswith("tuf-") for w in wheels):
            raise SystemExit("%s has no tuf wheel" % args.wheels)
        bad = [w.name for w in wheels if not (w.name.endswith("-none-any.whl") or "aarch64" in w.name)]
        if bad:
            raise SystemExit("not for the Spark (aarch64): %s" % ", ".join(bad))
        extra.update({"wheels/" + w.name: w.read_bytes() for w in wheels})
    if args.trusted_root:
        root = json.loads(Path(args.trusted_root).read_text(encoding="utf-8"))
        if root.get("signed", {}).get("_type") != "root":
            raise SystemExit("%s is not TUF root metadata" % args.trusted_root)
        extra["src/matrix_updater/trusted_root.json"] = Path(args.trusted_root).read_bytes()
    kit = {"format": 1, "kind": "vivesec-matrix-kit", "label": build.get("label"),
           "version": build.get("version"), "commit": build.get("commit"),
           "ui_commit": ui.get("commit"), "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "files": {"ui-output.tgz": sha256(ui_bytes)}}
    with tarfile.open(args.destination, "w:gz") as archive:
        for path in files:
            data = Path(path).read_bytes()
            kit["files"]["src/" + path] = sha256(data)
            add(archive, "src/" + path, data, 0o755 if path.endswith(EXECUTABLE) else 0o644)
        add(archive, "ui-output.tgz", ui_bytes)
        for name, data in app_files.items():
            kit["files"]["app/" + name] = sha256(data)
            add(archive, "app/" + name, data)
        for name, data in sorted(extra.items()):
            kit["files"][name] = sha256(data)
            add(archive, name, data)
        add(archive, "KIT.json", (json.dumps(kit, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    print("KIT %s label=%s files=%d sha256=%s" % (args.destination, kit["label"], len(files),
                                                   sha256(Path(args.destination).read_bytes())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
