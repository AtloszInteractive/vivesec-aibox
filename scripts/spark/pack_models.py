#!/usr/bin/env python3
"""Put a Matrix AI release's models on its USB stick (E07).

    sudo python3 pack_models.py --kit /var/tmp/matrix-rel-26.11.1.2/matrix-kit.tgz \\
        --out /media/<user>/<stick>/matrix-models

Runs on the build Spark the release was packed on (pack_release.py): its own
Ollama (/var/lib/vivesec-matrix-ollama) holds the models the release kit pins.
Every file is checked against the kit's pins before it is copied; a second run
checks what is on the stick and copies only what is missing or damaged.

The stick then holds, next to matrix-usb.sh, matrix.conf and the kit:

    matrix-models/manifests/...   Ollama's layout
    matrix-models/blobs/sha256-<hex>

The generation model is one file of about 22 GB: FAT32 cannot hold it, format the
stick exFAT or ext4. On the box, matrix-usb.sh copies the folder to the disk and the
installer checks it against the signed kit again: the stick is not trusted.
"""
import argparse
import json
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import spark_install as si  # noqa: E402

FAT_LIMIT = (4 << 30) - 1


def kit_pins(kit_tgz):
    try:
        with tarfile.open(kit_tgz, "r:gz") as archive:
            info = json.load(archive.extractfile("KIT.json"))
    except (OSError, KeyError, ValueError, tarfile.TarError, AttributeError) as e:
        raise SystemExit("%s is not a release kit: %s" % (kit_tgz, e))
    pins = si.kit_models(info)
    if not pins:
        raise SystemExit("%s pins no models (a build kit, or a release from before pinned models)" % kit_tgz)
    return info.get("label"), pins


def filesystem(path):
    path = Path(path)
    while not path.exists():
        path = path.parent
    result = subprocess.run(("findmnt", "-no", "FSTYPE", "--target", str(path)), capture_output=True, text=True)
    return result.stdout.strip() if result.returncode == 0 else ""


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--kit", required=True, help="the release kit (matrix-kit.tgz of pack_release.py)")
    parser.add_argument("--out", required=True, help="the matrix-models folder on the stick")
    args = parser.parse_args(argv)
    label, pins = kit_pins(args.kit)
    out = Path(args.out)
    largest = max(pin["bytes"] for pin in pins)
    if filesystem(out) in ("vfat", "msdos", "fat") and largest > FAT_LIMIT:
        raise SystemExit("%s is FAT32, which holds no file over 4 GB: format the stick exFAT or ext4" % out)
    out.mkdir(parents=True, exist_ok=True)
    needed = sum(pin["bytes"] for pin in pins)
    if shutil.disk_usage(out).free < needed and not all(si.model_in_place(out, pin) for pin in pins):
        raise SystemExit("%s has %d GB free; the models of %s need up to %d GB"
                         % (out, shutil.disk_usage(out).free >> 30, label, needed >> 30))
    try:
        for pin in pins:
            si.log("MODEL %s (%d GB) -> %s" % (pin["name"], pin["bytes"] >> 30, out))
            copied = si.place_model(pin, si.OLLAMA_DIR, out)
            si.log("MODEL_OK %s (%d MB copied)" % (pin["name"], copied >> 20))
    except si.InstallError as e:
        raise SystemExit("%s (the build Spark must hold the models the release pinned)" % e)
    si.log("MODELS_PACKED %s for %s: put matrix-usb.sh, matrix.conf and the kit next to it" % (out, label))
    return 0


if __name__ == "__main__":
    sys.exit(main())
