"""Release manifest (E01), written on the build machine.

Records what a release consists of: the code (version, commit, per-component
source fingerprints), the delivered artifacts, the container images and how they
are built, the models, the configuration defaults and policies, and the
compatibility conditions. The box-side counterpart (box_manifest.py) records
what is actually installed and running.

  python scripts/release/release_manifest.py [--out FILE] [--artifact FILE ...] [--require-release]

--artifact   a delivered file (ui-output.tgz, aibox-ui-embed-*.zip, source tgz);
             its sha256 and size are recorded
--require-release  refuse to write unless this is a tagged, clean release build

Default output: release-manifest-<label>.json in the current directory.
Secret parameters are never recorded.
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_info  # noqa: E402
import config_registry  # noqa: E402

ROOT = build_info.ROOT
HERE = os.path.dirname(os.path.abspath(__file__))
# Source trees that end up in a deliverable, fingerprinted separately so a
# partial change (e.g. a UI-only fix) is visible in the manifest.
COMPONENT_TREES = {
    "adapter": ["adapter"],
    "rag": ["rag_service", "poc"],
    "ui": ["src", "public", "package.json", "package-lock.json", "vite.config.ts", "VERSION"],
    "install_kit": ["scripts/jetson/install", "scripts/jetson/Dockerfile.ui-runtime", "scripts/release"],
}
IMAGES = {
    "adapter": {"name": "vivesec-adapter", "dockerfile": "adapter/Dockerfile", "context": "adapter"},
    "rag": {"name": "vivesec-rag", "dockerfile": "rag_service/Dockerfile",
            "context": "staging tree with poc/ and rag_service/"},
    "ui": {"name": "vivesec-ui", "dockerfile": "scripts/jetson/Dockerfile.ui-runtime",
           "context": "ui-output.tgz (.output)"},
}
POLICY_CATEGORIES = ("chat", "scope", "security", "storage", "pairing")


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def tree_fingerprint(root, paths):
    """sha256 over (path, blob id) of the committed files under `paths`: it
    depends only on content, never on checkout time or line-ending conversion.
    Uncommitted changes are not covered; the release `dirty` flag reports them."""
    listed = subprocess.run(["git", "ls-tree", "-r", "-z", "HEAD", "--", *paths], cwd=root,
                            capture_output=True, check=True).stdout.decode("utf-8")
    entries = sorted(item.split("\t", 1)[::-1] for item in listed.split("\0") if "\t" in item)
    digest = hashlib.sha256()
    for name, meta in entries:
        digest.update(("%s\0%s\n" % (name, meta.split()[2])).encode("utf-8"))
    return {"sha256": digest.hexdigest(), "files": len(entries)}


def base_image(dockerfile):
    with open(os.path.join(ROOT, dockerfile), encoding="utf-8") as handle:
        for line in handle:
            if line.strip().upper().startswith("FROM "):
                return line.split()[1]
    return None


def configuration(registry):
    defaults = {"adapter": {}, "rag": {}}
    policies = {}
    for entry in registry["parameters"]:
        if entry["secret"]:
            continue
        image_default = entry.get("image_default")
        for component in defaults:
            value = image_default.get(component) if isinstance(image_default, dict) else (
                image_default if entry["component"] == component else None)
            if value is not None:
                defaults[component][entry["name"]] = value
        if entry["category"] in POLICY_CATEGORIES and entry["component"] in ("adapter", "rag", "shared"):
            policies[entry["name"]] = {
                "default": entry["default"], "image_default": image_default,
                "allowed": entry["allowed"], "fail_safe": entry["fail_safe"]}
    return defaults, policies


def build(artifacts=(), now=None):
    info = build_info.collect(ROOT, now=now)
    registry = config_registry.load()
    with open(os.path.join(HERE, "compatibility.json"), encoding="utf-8") as handle:
        compatibility = json.load(handle)
    defaults, policies = configuration(registry)
    images = {}
    for kind, image in IMAGES.items():
        images[kind] = dict(image, base=base_image(image["dockerfile"]),
                            labels={"vivesec.version": info["label"], "vivesec.commit": info["commit"]})
    return {
        "schema": 1,
        "kind": "vivesec-aibox-release-manifest",
        "release": info,
        "code": {name: tree_fingerprint(ROOT, paths) for name, paths in COMPONENT_TREES.items()},
        "artifacts": [{"name": os.path.basename(path), "sha256": sha256_file(path),
                       "bytes": os.path.getsize(path)} for path in artifacts],
        "images": images,
        "models": compatibility["models"],
        "configuration": {
            "register_sha256": sha256_file(config_registry.REGISTRY_PATH),
            "parameters": len(registry["parameters"]),
            "image_defaults": defaults,
        },
        "policies": policies,
        "compatibility": compatibility,
    }


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out")
    parser.add_argument("--artifact", action="append", default=[])
    parser.add_argument("--require-release", action="store_true")
    args = parser.parse_args(argv)
    problems = config_registry.check()
    if problems:
        for problem in problems:
            print("REGISTER: %s" % problem, file=sys.stderr)
        return 1
    manifest = build(args.artifact)
    if args.require_release:
        problems = build_info.release_problems(manifest["release"])
        for problem in problems:
            print("NOT A RELEASE BUILD: %s" % problem, file=sys.stderr)
        if problems:
            return 1
    out = args.out or "release-manifest-%s.json" % manifest["release"]["label"]
    with open(out, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(manifest, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    print("wrote %s (%s)" % (out, manifest["release"]["label"]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
