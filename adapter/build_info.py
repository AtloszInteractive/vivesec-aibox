"""Release identity of this component, read from build_info.json next to this file.

scripts/release/build_info.py stamps that file at build time with the calendar
version (YY.MM.N) and the git commit. An unstamped dev checkout falls back to the
repository VERSION file and reports itself as a dev build; a missing or
malformed record never stops the service, it reports "unknown".

This module is duplicated byte-for-byte in adapter/ and rag_service/ (the two
images have separate build contexts); scripts/release/release_test.py enforces
that the copies stay identical.
"""
import json
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
_FIELDS = ("version", "commit", "dirty", "tagged", "label", "built_at")


def load(path=None, version_file=None):
    info = {"version": None, "commit": None, "dirty": None, "tagged": False,
            "label": "unknown", "built_at": None}
    try:
        with open(path or os.path.join(_HERE, "build_info.json"), encoding="utf-8") as handle:
            stamped = json.load(handle)
        if isinstance(stamped, dict) and isinstance(stamped.get("version"), str):
            info.update({key: stamped.get(key) for key in _FIELDS if key in stamped})
            info["label"] = str(info.get("label") or info["version"])
            return info
    except (OSError, ValueError):
        pass
    try:
        with open(version_file or os.path.join(_HERE, "..", "VERSION"), encoding="utf-8") as handle:
            version = handle.read().strip()
        if version:
            info.update(version=version, label=version + "-dev")
    except OSError:
        pass
    return info


BUILD = load()
