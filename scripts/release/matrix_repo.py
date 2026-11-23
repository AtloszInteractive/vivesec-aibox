"""Matrix AI update repository (TUF) on the publishing side.

The repository is plain static files: any HTTPS host serves it (a cloud bucket
now, aibox-files.vivesec.com/matrix/ once the ViVeSec agrees). Boxes trust only
the root metadata shipped in their kit; everything else must be signed by the
keys the root names. Run it where the signing keys are.

    python matrix_repo.py init      --repo REPO --keys KEYS [--encrypt]  keys + metadata
    python matrix_repo.py publish   --repo REPO --keys KEYS --channel stable RELEASE_DIR
    python matrix_repo.py refresh   --repo REPO --keys KEYS      re-sign timestamp (and snapshot)
    python matrix_repo.py status    --repo REPO

Roles: root (two offline keys, both must sign), targets (offline release key),
snapshot and timestamp (one online key, so a scheduled job can keep them
fresh). KEYS/keys.json names each key by a URI: "file:<pem next to keys.json>"
for software keys, any securesystemslib signer URI (e.g. "hsm:") for hardware keys.

Software keys: `init --encrypt` writes them encrypted with the passphrase in
MATRIX_REPO_PASSPHRASE (at least 16 characters), which every later command
needs too. Without --encrypt they are development keys, for the labor channel
only. Moving a repository to other root keys (hardware) needs a root rotation,
which this tool does not do yet.

Targets:
    channels/<channel>.json             the newest release of the channel
    releases/<version>/release.json     what a release consists of
    kits/<sha256>.tgz                   installer source of a release
    images/<name>-<image id>.tar.zst    container images, content-addressed (or .tar.gz)

RELEASE_DIR holds release.json (see `load_release_input`) and the files it names.
Needs python-tuf (`pip install -r matrix_updater/requirements.txt`).
"""
import argparse
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from securesystemslib.signer import CryptoSigner, Signer, SSlibKey
from tuf.api.metadata import Metadata, MetaFile, Root, Snapshot, TargetFile, Targets, Timestamp
from tuf.api.serialization.json import JSONSerializer

ROLES = ("root", "targets", "snapshot", "timestamp")
EXPIRY_DAYS = {"root": 365, "targets": 90, "snapshot": 30, "timestamp": 14}
# The snapshot is re-signed by `refresh` once fewer days than this are left.
SNAPSHOT_REFRESH_DAYS = 10
CHANNELS = ("stable", "pilot", "labor")
VERSION = re.compile(r"^(\d{2})\.(\d{2})\.(\d{1,3})(?:\.(\d{1,4}))?$")
IMAGE_NAME = re.compile(r"^vivesec-[a-z0-9-]{1,40}$")
IMAGE_ID = re.compile(r"^sha256:[0-9a-f]{64}$")
# docker load reads both; zstd is smaller, gzip is there when zstd is not.
IMAGE_SUFFIXES = (".tar.zst", ".tar.gz")
SERIALIZER = JSONSerializer(compact=False)


class RepoError(Exception):
    pass


def now():
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def expiry(role, days=None):
    return now() + dt.timedelta(days=days if days is not None else EXPIRY_DAYS[role])


def parse_version(text, channel=None):
    """(YY, MM, N, B) for ordering; B (a build of the pilot or labor channel) is -1 when absent."""
    match = VERSION.match(text or "")
    if not match:
        raise RepoError("version %r is not YY.MM.N[.B]" % text)
    if channel == "stable" and match.group(4) is not None:
        raise RepoError("the stable channel takes YY.MM.N releases only, not %s" % text)
    return tuple(int(part) if part is not None else -1 for part in match.groups())


def write_bytes(path, data, mode=0o644):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name("." + path.name + ".tmp")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    with os.fdopen(fd, "wb") as handle:
        handle.write(data)
    os.replace(temp, path)


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


# ---------------------------------------------------------------- keys

PASSPHRASE_MIN = 16


def keys_encrypted(keys_dir):
    config = json.loads((Path(keys_dir) / "keys.json").read_text(encoding="utf-8"))
    return config.get("encrypted") is True or any(
        not entry.get("uri", "").startswith("file:") for role in ROLES for entry in config.get(role, []))


def passphrase_from_env():
    value = os.environ.get("MATRIX_REPO_PASSPHRASE") or None
    if value is not None and len(value) < PASSPHRASE_MIN:
        raise RepoError("MATRIX_REPO_PASSPHRASE must have at least %d characters" % PASSPHRASE_MIN)
    return value


def load_signers(keys_dir, passphrase=None):
    """{role: [Signer]} from keys.json; a role may have several keys."""
    keys_dir = Path(keys_dir)
    config = json.loads((keys_dir / "keys.json").read_text(encoding="utf-8"))
    password = passphrase.encode("utf-8") if passphrase else None
    signers = {}
    for role in ROLES:
        for entry in config.get(role, []):
            public = SSlibKey.from_dict(entry["keyid"], dict(entry["public"]))
            uri = entry["uri"]
            if uri.startswith("file:"):
                pem = (keys_dir / uri[5:]).read_bytes()
                try:
                    private = serialization.load_pem_private_key(pem, password=password)
                except TypeError:
                    # Encrypted without a passphrase given, or a passphrase for a plain key.
                    raise RepoError("%s: %s" % (entry.get("name", uri), "the keys are encrypted: set "
                                    "MATRIX_REPO_PASSPHRASE" if password is None else
                                    "this key is not encrypted; unset MATRIX_REPO_PASSPHRASE"))
                except ValueError:
                    raise RepoError("%s: wrong passphrase" % entry.get("name", uri))
                signer = CryptoSigner(private, public)
            else:
                signer = Signer.from_priv_key_uri(uri, public)
            signers.setdefault(role, []).append(signer)
    missing = [role for role in ROLES if not signers.get(role)]
    if missing:
        raise RepoError("keys.json has no key for %s" % ", ".join(missing))
    return signers


def generate_keys(keys_dir, passphrase=None):
    """Software keys: two root keys, one targets key, one online key; encrypted with a passphrase."""
    keys_dir = Path(keys_dir)
    if (keys_dir / "keys.json").exists():
        raise RepoError("%s already has keys.json" % keys_dir)
    keys_dir.mkdir(parents=True, exist_ok=True)
    encryption = (serialization.BestAvailableEncryption(passphrase.encode("utf-8")) if passphrase
                  else serialization.NoEncryption())
    plan = (("root", "root-1"), ("root", "root-2"), ("targets", "targets"),
            ("snapshot", "online"), ("timestamp", "online"))
    made, config = {}, {role: [] for role in ROLES}
    for role, name in plan:
        if name not in made:
            private = Ed25519PrivateKey.generate()
            pem = private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, encryption)
            write_bytes(keys_dir / (name + ".pem"), pem, 0o600)
            made[name] = CryptoSigner(private).public_key
        public = made[name]
        config[role].append({"name": name, "uri": "file:%s.pem" % name, "keyid": public.keyid,
                             "public": public.to_dict()})
    config["encrypted"] = bool(passphrase)
    write_bytes(keys_dir / "keys.json", (json.dumps(config, indent=2) + "\n").encode("utf-8"), 0o600)
    return load_signers(keys_dir, passphrase)


# ---------------------------------------------------------------- metadata

def sign(metadata, signers):
    metadata.signatures.clear()
    for signer in signers:
        metadata.sign(signer, append=True)
    return metadata


def save(repo, name, metadata):
    write_bytes(Path(repo) / "metadata" / name, metadata.to_bytes(SERIALIZER))


def load(repo, name):
    path = Path(repo) / "metadata" / name
    if not path.is_file():
        raise RepoError("%s missing: run init first" % path)
    return Metadata.from_bytes(path.read_bytes())


def current(repo):
    """The metadata the repository serves now: (timestamp, snapshot, targets)."""
    timestamp = load(repo, "timestamp.json")
    snapshot = load(repo, "%d.snapshot.json" % timestamp.signed.snapshot_meta.version)
    targets = load(repo, "%d.targets.json" % snapshot.signed.meta["targets.json"].version)
    return timestamp, snapshot, targets


def init(repo, keys_dir, root_threshold=2, passphrase=None):
    repo = Path(repo)
    if (repo / "metadata").exists():
        raise RepoError("%s/metadata exists already" % repo)
    signers = generate_keys(keys_dir, passphrase)
    root = Root(expires=expiry("root"), consistent_snapshot=True)
    for role in ROLES:
        for signer in signers[role]:
            root.add_key(signer.public_key, role)
    root.roles["root"].threshold = min(root_threshold, len(signers["root"]))
    root_md = sign(Metadata(root), signers["root"])
    save(repo, "1.root.json", root_md)
    save(repo, "1.targets.json", sign(Metadata(Targets(expires=expiry("targets"))), signers["targets"]))
    snapshot = Snapshot(expires=expiry("snapshot"))
    snapshot.meta["targets.json"] = MetaFile(version=1)
    save(repo, "1.snapshot.json", sign(Metadata(snapshot), signers["snapshot"]))
    timestamp = Timestamp(expires=expiry("timestamp"), snapshot_meta=MetaFile(version=1))
    save(repo, "timestamp.json", sign(Metadata(timestamp), signers["timestamp"]))
    # The box trusts this file and nothing else; it goes into every kit.
    write_bytes(repo / "trusted_root.json", root_md.to_bytes(SERIALIZER))
    return repo / "trusted_root.json"


def commit(repo, signers, targets_md, snapshot_md, timestamp_md, resign_targets=True):
    """Bump and sign targets (optional), snapshot and timestamp; timestamp is written last."""
    if resign_targets:
        targets_md.signed.version += 1
        targets_md.signed.expires = expiry("targets")
        save(repo, "%d.targets.json" % targets_md.signed.version, sign(targets_md, signers["targets"]))
    snapshot_md.signed.meta["targets.json"] = MetaFile(version=targets_md.signed.version)
    snapshot_md.signed.version += 1
    snapshot_md.signed.expires = expiry("snapshot")
    save(repo, "%d.snapshot.json" % snapshot_md.signed.version, sign(snapshot_md, signers["snapshot"]))
    timestamp_md.signed.snapshot_meta = MetaFile(version=snapshot_md.signed.version)
    timestamp_md.signed.version += 1
    timestamp_md.signed.expires = expiry("timestamp")
    save(repo, "timestamp.json", sign(timestamp_md, signers["timestamp"]))


def add_target(repo, targets_md, target_path, local_path, replace=False):
    """Copy a file into targets/ under its hash prefix and list it. Only a mutable
    target (a channel pointer) may change content; an equal one is kept."""
    info = TargetFile.from_file(target_path, str(local_path), ["sha256"])
    existing = targets_md.signed.targets.get(target_path)
    if existing is not None and existing.hashes != info.hashes and not replace:
        raise RepoError("%s is published already with other content" % target_path)
    directory, _, name = target_path.rpartition("/")
    stored = Path(repo) / "targets" / directory / ("%s.%s" % (info.hashes["sha256"], name))
    if not stored.is_file():
        stored.parent.mkdir(parents=True, exist_ok=True)
        temp = stored.with_name("." + stored.name + ".tmp")
        shutil.copyfile(local_path, temp)
        os.replace(temp, stored)
    targets_md.signed.targets[target_path] = info
    return info


def add_bytes_target(repo, targets_md, target_path, data, work_dir, replace=False):
    local = Path(work_dir) / target_path.replace("/", "_")
    write_bytes(local, data)
    return add_target(repo, targets_md, target_path, local, replace)


def read_target(repo, targets_md, target_path):
    info = targets_md.signed.targets.get(target_path)
    if info is None:
        return None
    directory, _, name = target_path.rpartition("/")
    stored = Path(repo) / "targets" / directory / ("%s.%s" % (info.hashes["sha256"], name))
    return json.loads(stored.read_text(encoding="utf-8"))


# ---------------------------------------------------------------- releases

def load_release_input(release_dir, channel):
    """release.json of a release directory, checked:

    {"format": 1, "product": "matrix", "version": "26.11.1", "label": "...", "commit": "...",
     "min_from": "26.10.1", "security": false, "notes": {"hu": "...", "en": "..."},
     "compat": {"snapshot": false, "reindex": false},
     "kit": {"file": "matrix-kit.tgz"},
     "images": [{"name": "vivesec-adapter", "id": "sha256:...", "file": "images/adapter.tar.zst"}]}
    """
    release_dir = Path(release_dir)
    data = json.loads((release_dir / "release.json").read_text(encoding="utf-8"))
    if data.get("format") != 1 or data.get("product") != "matrix":
        raise RepoError("release.json is not a format 1 Matrix release")
    parse_version(data.get("version"), channel)
    if data.get("min_from"):
        parse_version(data["min_from"])
    for key in ("kit",):
        if not (release_dir / data.get(key, {}).get("file", "")).is_file():
            raise RepoError("release.json %s file missing" % key)
    names = set()
    for image in data.get("images") or []:
        if not IMAGE_NAME.match(image.get("name", "")) or not IMAGE_ID.match(image.get("id", "")):
            raise RepoError("bad image entry %r" % image)
        if image["name"] in names:
            raise RepoError("image %s listed twice" % image["name"])
        names.add(image["name"])
        if not (release_dir / image.get("file", "")).is_file():
            raise RepoError("image file %s missing" % image.get("file"))
        if not image["file"].endswith(IMAGE_SUFFIXES):
            raise RepoError("image file %s is not %s" % (image["file"], " or ".join(IMAGE_SUFFIXES)))
    if not names:
        raise RepoError("a release names at least one image")
    if not isinstance(data.get("notes", {}), dict):
        raise RepoError("notes must map a language to a text")
    return data


def publish(repo, keys_dir, channel, release_dir, passphrase=None):
    if channel not in CHANNELS:
        raise RepoError("channel must be one of %s" % ", ".join(CHANNELS))
    if channel != "labor" and not keys_encrypted(keys_dir):
        raise RepoError("development keys (not encrypted) publish to the labor channel only")
    signers = load_signers(keys_dir, passphrase)
    release = load_release_input(release_dir, channel)
    release_dir = Path(release_dir)
    timestamp_md, snapshot_md, targets_md = current(repo)
    version = release["version"]
    pointer_path = "channels/%s.json" % channel
    pointer = read_target(repo, targets_md, pointer_path)
    if pointer and parse_version(version) <= parse_version(pointer["version"]):
        raise RepoError("%s is not newer than %s on %s" % (version, pointer["version"], channel))
    release_path = "releases/%s/release.json" % version
    if release_path in targets_md.signed.targets:
        raise RepoError("%s is published already" % version)

    kit_file = release_dir / release["kit"]["file"]
    kit_hash = sha256_file(kit_file)
    kit_target = "kits/matrix-kit-%s.tgz" % kit_hash[:16]
    info = add_target(repo, targets_md, kit_target, kit_file)
    out = {key: release.get(key) for key in ("format", "product", "version", "label", "commit", "min_from",
                                             "security", "notes", "compat")}
    out["security"] = bool(out.get("security"))
    out["compat"] = {"snapshot": bool((release.get("compat") or {}).get("snapshot")),
                     "reindex": bool((release.get("compat") or {}).get("reindex"))}
    out["kit"] = {"target": kit_target, "length": info.length, "sha256": info.hashes["sha256"]}
    out["images"] = []
    for image in release["images"]:
        suffix = next(s for s in IMAGE_SUFFIXES if image["file"].endswith(s))
        target = "images/%s-%s%s" % (image["name"], image["id"][7:23], suffix)
        # The image id names the content; docker save of the same image is not
        # byte-for-byte the same, so an image published before is reused as it is.
        info = targets_md.signed.targets.get(target) or add_target(repo, targets_md, target,
                                                                   release_dir / image["file"])
        out["images"].append({"name": image["name"], "id": image["id"], "target": target,
                              "length": info.length, "sha256": info.hashes["sha256"]})
    out["published"] = now().isoformat()
    work = Path(repo) / ".work"
    work.mkdir(exist_ok=True)
    add_bytes_target(repo, targets_md, release_path,
                     (json.dumps(out, indent=2, ensure_ascii=False) + "\n").encode("utf-8"), work)
    add_bytes_target(repo, targets_md, pointer_path, (json.dumps(
        {"format": 1, "channel": channel, "version": version, "release": release_path,
         "published": out["published"]}, indent=2) + "\n").encode("utf-8"), work, replace=True)
    shutil.rmtree(work, ignore_errors=True)
    commit(repo, signers, targets_md, snapshot_md, timestamp_md)
    return out


def refresh(repo, keys_dir, passphrase=None):
    """Keep a quiet repository valid: a new timestamp, and a new snapshot when it gets old."""
    signers = load_signers(keys_dir, passphrase)
    timestamp_md, snapshot_md, targets_md = current(repo)
    if snapshot_md.signed.expires - now() < dt.timedelta(days=SNAPSHOT_REFRESH_DAYS):
        commit(repo, signers, targets_md, snapshot_md, timestamp_md, resign_targets=False)
    else:
        timestamp_md.signed.version += 1
        timestamp_md.signed.expires = expiry("timestamp")
        save(repo, "timestamp.json", sign(timestamp_md, signers["timestamp"]))
    return load(repo, "timestamp.json").signed.expires


def status(repo):
    timestamp_md, snapshot_md, targets_md = current(repo)
    lines = []
    for name, md in (("timestamp", timestamp_md), ("snapshot", snapshot_md), ("targets", targets_md)):
        lines.append("%-9s v%-4d expires %s" % (name, md.signed.version, md.signed.expires.isoformat()))
    for channel in CHANNELS:
        pointer = read_target(repo, targets_md, "channels/%s.json" % channel)
        if pointer:
            lines.append("channel %-6s %s (%s)" % (channel, pointer["version"], pointer["published"]))
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Matrix AI update repository (TUF)")
    parser.add_argument("command", choices=("init", "publish", "refresh", "status"))
    parser.add_argument("release_dir", nargs="?")
    parser.add_argument("--repo", required=True)
    parser.add_argument("--keys")
    parser.add_argument("--channel", default="stable")
    parser.add_argument("--encrypt", action="store_true", help="init: encrypt the keys (MATRIX_REPO_PASSPHRASE)")
    args = parser.parse_args(argv)
    try:
        if args.command != "status" and not args.keys:
            raise RepoError("--keys is required")
        passphrase = passphrase_from_env()
        if args.command == "init":
            if args.encrypt and not passphrase:
                raise RepoError("--encrypt needs MATRIX_REPO_PASSPHRASE")
            print("trusted root: %s" % init(args.repo, args.keys, passphrase=passphrase if args.encrypt else None))
            if not args.encrypt:
                print("DEVELOPMENT KEYS in %s (not encrypted): labor channel only" % args.keys)
        elif args.command == "publish":
            if not args.release_dir:
                raise RepoError("publish needs RELEASE_DIR")
            out = publish(args.repo, args.keys, args.channel, args.release_dir, passphrase)
            print("published %s on %s: %d image(s), kit %s" % (out["version"], args.channel,
                                                                len(out["images"]), out["kit"]["target"]))
        elif args.command == "refresh":
            print("timestamp valid until %s" % refresh(args.repo, args.keys, passphrase).isoformat())
        else:
            print(status(args.repo))
    except (RepoError, OSError, ValueError, KeyError) as e:
        print("ERROR: %s" % e, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
