#!/usr/bin/env python3
"""Pack a Matrix AI release with built images on a build Spark (E07).

    sudo python3 pack_release.py --version 26.11.1 --out /var/tmp/matrix-26.11.1 \\
        --notes-hu "..." --notes-en "..." [--min-from 26.10.1] [--security] [--data-copy] [--reindex]

Release notes with accents are safer in a UTF-8 JSON file (--notes-file): a
PowerShell here-string piped to ssh turns them into '?'.

Runs from an unpacked build kit (pack_kit.py, with --wheels and --trusted-root)
on an aarch64 Spark with docker and internet. It builds every image the way the
installer would (10-build-images.sh, auth_deploy.py build with the auth tests,
the graph image with its tests), takes ClamAV and Neo4j from their registries,
saves each image, and writes OUT/:

    release.json        the input of scripts/release/matrix_repo.py publish
    matrix-kit.tgz      the release kit: no ui-output.tgz, KIT.json lists the images
                        and carries the release version as its label
    images/<name>.tar.zst (or .tar.gz without zstd)

Building retags :latest of vivesec-adapter, -rag and -ui on this Spark, as an
installation from the build kit would. Signing and publishing happen where the
keys are (matrix_repo.py); this script holds no key.
"""
import argparse
import hashlib
import io
import json
import re
import shutil
import subprocess
import sys
import tarfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1] / "scripts" / "jetson" / "standalone"))

import spark_install as si  # noqa: E402

PUBLIC_IMAGES = {"vivesec-clamav": "clamav/clamav-debian:1.4", "vivesec-neo4j": "neo4j:5-community"}
BUILT_IMAGES = ("vivesec-adapter", "vivesec-rag", "vivesec-ui", "vivesec-auth", "vivesec-graph")
VERSION = re.compile(r"^(\d{2}\.\d{2}\.\d{1,3})(?:\.\d{1,4})?$")
# Left out of a release kit: what only a build on the box needs.
BUILD_ONLY = ("ui-output.tgz",)


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def check_version(version, kit):
    match = VERSION.match(version or "")
    if not match:
        raise SystemExit("--version must be YY.MM.N or YY.MM.N.B")
    if match.group(1) != kit.get("version"):
        raise SystemExit("--version %s does not belong to the kit's VERSION %s" % (version, kit.get("version")))
    if version == match.group(1) and kit.get("label") != kit.get("version"):
        # YY.MM.N is a release (stable channel): it must come from the release tag (E01).
        raise SystemExit("%s is a release version but the kit is a development build (%s); "
                         "use YY.MM.N.B for the pilot or labor channel" % (version, kit.get("label")))


def build_images(kit_dir, src):
    """Build and tag every image as <name>:release-<stamp>; returns {name: ref}."""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    si.phase_images(None)
    refs = {}
    for name in ("vivesec-adapter", "vivesec-rag", "vivesec-ui"):
        refs[name] = name + ":latest"
    import auth_deploy
    refs["vivesec-auth"] = auth_deploy.build(auth_deploy.settings(), src, stamp)
    refs["vivesec-graph"] = si.build_graph_image()
    for name, public in PUBLIC_IMAGES.items():
        si.run("docker", "pull", "-q", public)
        refs[name] = public
    tagged = {}
    for name, ref in refs.items():
        tag = "%s:release-%s" % (name, stamp)
        si.run("docker", "tag", ref, tag)
        tagged[name] = tag
    return tagged


def image_id(ref):
    return si.run("docker", "image", "inspect", "-f", "{{.Id}}", ref).stdout.strip()


def save_image(ref, out_dir, name):
    compressor = ("zstd", "-T0", "-10", "-q") if shutil.which("zstd") else ("gzip", "-6")
    suffix = ".tar.zst" if compressor[0] == "zstd" else ".tar.gz"
    target = out_dir / "images" / (name + suffix)
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "wb") as handle:
        save = subprocess.Popen(("docker", "save", ref), stdout=subprocess.PIPE)
        packed = subprocess.run(compressor, stdin=save.stdout, stdout=handle)
        save.stdout.close()
        if save.wait() or packed.returncode:
            raise SystemExit("saving %s failed" % ref)
    return target


def release_kit(kit_dir, destination, version, images):
    """The build kit without what only a build needs, its KIT.json listing the images."""
    kit_dir = Path(kit_dir)
    kit = json.loads((kit_dir / "KIT.json").read_text(encoding="utf-8"))
    out = {key: kit.get(key) for key in ("format", "kind", "version", "commit", "ui_commit")}
    out.update({"label": version, "build_label": kit.get("label"),
                "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "files": {},
                "images": [{"name": name, "id": images[name]} for name in sorted(images)]})
    with tarfile.open(destination, "w:gz") as archive:
        for path in sorted(p for p in kit_dir.rglob("*") if p.is_file()):
            name = path.relative_to(kit_dir).as_posix()
            if name == "KIT.json" or name in BUILD_ONLY or "__pycache__" in name:
                continue
            data = path.read_bytes()
            out["files"][name] = sha256(data)
            info = tarfile.TarInfo(name)
            info.size, info.mtime, info.mode = len(data), int(time.time()), path.stat().st_mode & 0o755 | 0o644
            archive.addfile(info, io.BytesIO(data))
        data = (json.dumps(out, indent=2, sort_keys=True) + "\n").encode("utf-8")
        info = tarfile.TarInfo("KIT.json")
        info.size, info.mtime, info.mode = len(data), int(time.time()), 0o644
        archive.addfile(info, io.BytesIO(data))
    return out


def release_json(version, kit, args, image_files):
    notes = {lang: text for lang, text in (("hu", args.notes_hu), ("en", args.notes_en),
                                          ("da", args.notes_da), ("de", args.notes_de)) if text}
    return {"format": 1, "product": "matrix", "version": version, "label": version, "commit": kit.get("commit"),
            "min_from": args.min_from, "security": args.security, "notes": notes,
            "compat": {"snapshot": args.data_copy, "reindex": args.reindex},
            "kit": {"file": "matrix-kit.tgz"},
            "images": [{"name": name, "id": image_id_, "file": path}
                       for name, (image_id_, path) in sorted(image_files.items())]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--version", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--min-from")
    parser.add_argument("--security", action="store_true")
    parser.add_argument("--data-copy", action="store_true", help="the release changes how data is stored")
    parser.add_argument("--reindex", action="store_true", help="the release rebuilds the search index")
    for lang in ("hu", "en", "da", "de"):
        parser.add_argument("--notes-" + lang, default="")
    parser.add_argument("--notes-file", help='UTF-8 JSON {"hu": "...", "en": "..."}; wins over --notes-*')
    args = parser.parse_args(argv)
    if args.notes_file:
        notes = json.loads(Path(args.notes_file).read_text(encoding="utf-8"))
        if not isinstance(notes, dict) or not all(isinstance(v, str) for v in notes.values()):
            raise SystemExit("--notes-file must map a language to a text")
        for lang in ("hu", "en", "da", "de"):
            if notes.get(lang):
                setattr(args, "notes_" + lang, notes[lang])
    kit = si.kit_info()
    check_version(args.version, kit)
    if args.min_from and not VERSION.match(args.min_from):
        raise SystemExit("--min-from must be YY.MM.N or YY.MM.N.B")
    if not (args.notes_hu or args.notes_en):
        raise SystemExit("write release notes (--notes-hu and/or --notes-en)")
    if si.kit_images(kit):
        raise SystemExit("this is a release kit already; run from a build kit")
    for needed in ("wheels", "src/matrix_updater/trusted_root.json"):
        if not (si.KIT / needed).exists():
            raise SystemExit("the build kit has no %s (pack_kit.py --wheels --trusted-root)" % needed)
    out = Path(args.out)
    if out.exists() and any(out.iterdir()):
        raise SystemExit("%s is not empty" % out)
    out.mkdir(parents=True, exist_ok=True)
    try:
        refs = build_images(si.KIT, si.SRC)
    except si.InstallError as e:
        raise SystemExit("build failed: %s" % e)
    image_files = {}
    for name, ref in sorted(refs.items()):
        target = save_image(ref, out, name)
        image_files[name] = (image_id(ref), target.relative_to(out).as_posix())
        si.log("SAVED %s %s (%d MB)" % (name, image_files[name][0][:19], target.stat().st_size >> 20))
    release_kit(si.KIT, out / "matrix-kit.tgz", args.version,
                {name: value[0] for name, value in image_files.items()})
    release = release_json(args.version, kit, args, image_files)
    (out / "release.json").write_text(json.dumps(release, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    si.log("RELEASE_PACKED %s in %s; next, where the keys are: "
           "python scripts/release/matrix_repo.py publish --repo REPO --keys KEYS --channel CHANNEL %s"
           % (args.version, out, out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
