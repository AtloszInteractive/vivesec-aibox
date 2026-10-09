"""Release identity of a build: one calendar version and one source id for every component.

The repository-root VERSION file holds the release number (YY.MM.N, e.g. 26.10.1);
the git commit is the source identifier. `stamp` writes both into
adapter/build_info.json and rag_service/build_info.json, the files the services
report from /api/v1/status, /api/v1/version and the RAG /health and /stats. The
web UI computes the same record in vite.config.ts.

A build is a *release* only when HEAD carries the tag v<VERSION> and no tracked
file is modified; anything else is labelled <VERSION>-dev+<commit>[.dirty] so a
dev build can never be mistaken for a release. Untracked files (e.g. a freshly
packed ui-output.tgz) do not make a build dirty.

Usage:
  python scripts/release/build_info.py show            # print the record
  python scripts/release/build_info.py stamp           # write the component files
  python scripts/release/build_info.py check           # exit 1 unless this is a release build
"""
import datetime
import json
import os
import re
import subprocess
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
VERSION_RE = re.compile(r"^\d{2}\.(0[1-9]|1[0-2])\.(0|[1-9]\d*)$")
STAMP_TARGETS = ("adapter/build_info.json", "rag_service/build_info.json")
SCHEMA = 1


def read_version(root=ROOT):
    with open(os.path.join(root, "VERSION"), encoding="utf-8") as handle:
        version = handle.read().strip()
    if not VERSION_RE.match(version):
        raise ValueError("VERSION must be YY.MM.N (e.g. 26.10.1), got %r" % version)
    return version


def _git(root, *args):
    try:
        result = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def label(version, commit, dirty, tagged):
    if tagged and not dirty and commit:
        return version
    suffix = "-dev"
    if commit:
        suffix += "+" + commit[:7] + (".dirty" if dirty else "")
    return version + suffix


def collect(root=ROOT, now=None):
    version = read_version(root)
    commit = _git(root, "rev-parse", "HEAD")
    if commit is None:
        dirty, tagged = None, False
    else:
        dirty = bool(_git(root, "status", "--porcelain", "--untracked-files=no"))
        tags = (_git(root, "tag", "--points-at", "HEAD") or "").split()
        tagged = ("v" + version) in tags
    built = (now or datetime.datetime.now(datetime.timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"schema": SCHEMA, "version": version, "commit": commit, "dirty": dirty,
            "tagged": tagged, "label": label(version, commit, bool(dirty), tagged),
            "built_at": built}


def _shipped_stamp(path, version):
    """The stamp a git-less tree was shipped with, if it is still valid for this
    tree: it must carry a commit and the same VERSION. Anything else is stale."""
    try:
        with open(path, encoding="utf-8") as handle:
            stamped = json.load(handle)
    except (OSError, ValueError):
        return None
    if isinstance(stamped, dict) and stamped.get("commit") and stamped.get("version") == version:
        return stamped
    return None


def stamp(root=ROOT, info=None, targets=STAMP_TARGETS):
    info = info or collect(root)
    written = []
    for relative in targets:
        path = os.path.join(root, *relative.split("/"))
        if info["commit"] is None and _shipped_stamp(path, info["version"]):
            # A delivered source tree without .git keeps the identity it was
            # stamped with on the build machine instead of losing the commit.
            print("kept existing %s (no git metadata here)" % relative)
            continue
        temporary = path + ".tmp"
        with open(temporary, "w", encoding="utf-8") as handle:
            json.dump(info, handle, indent=2)
            handle.write("\n")
        os.replace(temporary, path)
        written.append(relative)
    return written


def release_problems(info):
    problems = []
    if not info["commit"]:
        problems.append("no git commit available")
    if info["dirty"]:
        problems.append("tracked files have uncommitted changes")
    if not info["tagged"]:
        problems.append("HEAD is not tagged v%s" % info["version"])
    return problems


def main(argv):
    command = argv[0] if argv else "show"
    try:
        info = collect()
    except (OSError, ValueError) as error:
        print("ERROR: %s" % error, file=sys.stderr)
        return 1
    if command == "show":
        print(json.dumps(info, indent=2))
        return 0
    if command == "stamp":
        for relative in stamp(info=info):
            print("stamped %s -> %s" % (relative, info["label"]))
        return 0
    if command == "check":
        problems = release_problems(info)
        for problem in problems:
            print("NOT A RELEASE BUILD: %s" % problem, file=sys.stderr)
        if not problems:
            print("release build %s (%s)" % (info["label"], info["commit"]))
        return 1 if problems else 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
