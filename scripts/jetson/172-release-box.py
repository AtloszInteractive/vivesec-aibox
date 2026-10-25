"""Release adapter + RAG + UI on a box in place, with automatic rollback.

  python3 172-release-box.py pack <src.tgz>                (dev machine)
  python3 172-release-box.py prepare <src.tgz> <ui.tgz>    -> ~/vivesec-release-<stamp>-*/
  python3 172-release-box.py promote|check|verify|rollback|prune <stage>

verify asks a general question in the hybrid profile and expects an answer
without document citations; the model now and then cites a related passage
anyway, so it asks up to 3 times (a fresh user each time) and passes on the
first uncited answer.

prepare adds Tesseract (hun, deu, dan; eng and osd come with it) to a RAG image
that lacks it (E10b OCR), which needs the package mirrors once, and prints the
installed OCR languages; RELEASE_RAG_OCR=off skips that.

A successful verify prunes old releases: it keeps the current stage and the
previous deployed one (one rollback level), removes stopped parked containers
(vivesec-{rag,adapter,ui}-{prev,failed}-<stamp>) no newer than the previous
release, then deletes older stage directories no container binds. Stages newer
than the current one and other containers are never touched; pruning errors
only print PRUNE_WARNING. RELEASE_KEEP_STAGES (default 2, minimum 2; 0 turns
pruning off) sets how many deployed stages to keep.
"""
import hashlib
import base64
import http.client
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request


class DockerConnection(http.client.HTTPConnection):
    def __init__(self):
        super().__init__("localhost", timeout=120)

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.connect("/var/run/docker.sock")


def docker_api(path, body):
    connection = DockerConnection()
    try:
        connection.request("POST", path, json.dumps(body), {"Content-Type": "application/json"})
        response = connection.getresponse()
        payload = json.loads(response.read() or b"{}")
        if response.status >= 300:
            raise RuntimeError("Docker API failed: HTTP %d" % response.status)
        return payload
    finally:
        connection.close()


def command(*args):
    result = subprocess.run(args, text=True, capture_output=True)
    if result.returncode:
        raise RuntimeError("Command failed: " + args[0] + " " + args[1] + "\n" + result.stderr[-1800:])
    return result.stdout.strip()


def inspect(name):
    return json.loads(command("docker", "inspect", name))[0]


def environment(container):
    return dict(entry.split("=", 1) for entry in container["Config"]["Env"])


def persist(stage, manifest):
    target = stage / "manifest.json"
    temporary = stage / "manifest.tmp"
    temporary.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    os.replace(temporary, target)


def extract(archive, destination):
    with tarfile.open(archive) as payload:
        for member in payload.getmembers():
            target = (destination / member.name).resolve()
            try:
                target.relative_to(destination.resolve())
            except ValueError:
                raise ValueError("Unsafe archive path") from None
            if member.issym() or member.islnk():
                raise ValueError("Links are not supported in release archives")
        payload.extractall(destination)


def helper(mode, stage):
    stage = Path(stage)
    manifest = json.loads((stage / "manifest.json").read_text())
    if mode == "backup-adapter":
        with tarfile.open(stage / "adapter-data.tar.gz", "w:gz") as archive:
            roots = sorted({mount["Destination"] for mount in manifest["old"]["adapter"]["Mounts"]}, key=len)
            included = []
            for root in roots:
                if not any(root.startswith(parent.rstrip("/") + "/") for parent in included):
                    archive.add(root, arcname=root.lstrip("/"))
                    included.append(root)
        print("ADAPTER_BACKUP_OK", flush=True)
        return
    import sqlite3
    import sqlite_vec
    index = environment(manifest["old"]["rag"])["RAG_INDEX_PATH"]
    source = sqlite3.connect("file:" + index + "?mode=ro", uri=True)
    source.enable_load_extension(True)
    sqlite_vec.load(source)
    source.enable_load_extension(False)
    destination = sqlite3.connect(stage / ("index-before.db" if mode == "backup-index" else "index-before-rollback.db"))
    source.backup(destination)
    destination.enable_load_extension(True)
    sqlite_vec.load(destination)
    destination.enable_load_extension(False)
    assert destination.execute("PRAGMA quick_check").fetchone()[0] == "ok"
    destination.close()
    source.close()
    if mode == "restore-index":
        shutil.copyfile(stage / "index-before.db", index)
        for suffix in ("-wal", "-shm"):
            Path(index + suffix).unlink(missing_ok=True)
    print("INDEX_BACKUP_OK" if mode == "backup-index" else "INDEX_RESTORED", flush=True)


def run_helper(stage, manifest, kind, mode, container):
    return command("docker", "run", "--rm", "--network", "none", "--volumes-from", container + (":ro" if mode != "restore-index" else ""),
                   "-v", str(stage) + ":/release", "--entrypoint", "python", manifest["old"][kind]["Image"],
                   "/release/release.py", mode, "/release")


def pack(destination):
    # The release identity (E01) travels inside the archive: the stamped files
    # are git-ignored, so they are added explicitly after stamping.
    subprocess.check_call([sys.executable, "scripts/release/build_info.py", "stamp"])
    stamped = {"adapter/build_info.json", "rag_service/build_info.json"}
    paths = subprocess.check_output(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard",
                                     "--", "adapter", "rag_service", "poc"]).decode("utf-8").split("\0")
    with tarfile.open(destination, "w:gz") as archive:
        for path in sorted((set(paths) | stamped) - {""}):
            if Path(path).is_file():
                archive.add(path, arcname=path)
    print("SOURCE_SHA256=" + hashlib.sha256(Path(destination).read_bytes()).hexdigest())


def ready(url, output):
    command("curl", "-fsS", "--retry", "12", "--retry-connrefused", "--retry-max-time", "120",
            "--max-time", "10", url, "-o", str(output))


def prepare(source_archive, ui_archive):
    stamp = time.strftime("%Y%m%d-%H%M%S")
    stage = Path(tempfile.mkdtemp(prefix="vivesec-release-" + stamp + "-", dir=Path.home()))
    os.chmod(stage, 0o700)
    print("STAGE=" + str(stage), flush=True)
    shutil.copyfile(__file__, stage / "release.py")
    build = stage / "build"
    build.mkdir()
    extract(source_archive, build)
    extract(ui_archive, build)
    old = {kind: inspect("vivesec-" + kind) for kind in ("rag", "adapter", "ui")}
    for kind, container in old.items():
        host = container["HostConfig"]
        assert host["NetworkMode"] == "host" and not host["AutoRemove"]
        assert container["State"]["Running"]
        assert not host["Privileged"]
    assert environment(old["rag"]).get("RAG_STORE_BACKEND") == "sqlite"
    fingerprint = hashlib.sha256(Path(source_archive).read_bytes()).hexdigest()
    # Release identity (E01): adapter and RAG come from the stamped source tree,
    # the UI from its bundle. One release = one version, so a UI bundle built for
    # another release is refused, as 10-build-images.sh does.
    source_build = json.loads((build / "adapter" / "build_info.json").read_text())
    ui_version_file = build / ".output" / "public" / "version.json"
    assert ui_version_file.is_file(), "The UI bundle carries no version.json"
    ui_build = json.loads(ui_version_file.read_text())
    assert ui_build.get("version") == source_build["version"], \
        "UI bundle version %s does not match the source release %s" % (ui_build.get("version"), source_build["version"])
    builds = {kind: {"version": entry.get("label") or "", "commit": entry.get("commit") or ""}
              for kind, entry in (("adapter", source_build), ("rag", source_build), ("ui", ui_build))}
    version = source_build["label"]
    manifest = {"stamp": stamp, "source_sha256": fingerprint, "version": version, "builds": builds,
                "ui_sha256": hashlib.sha256(Path(ui_archive).read_bytes()).hexdigest(),
                "old": old, "images": {}, "renamed": [], "new": [], "state": "preparing"}
    persist(stage, manifest)
    for kind in ("rag", "adapter", "ui"):
        copied = ["poc", "rag_service"] if kind == "rag" else (["adapter"] if kind == "adapter" else [".output"])
        dockerfile = "FROM " + old[kind]["Image"] + "\nWORKDIR /app\n"
        if kind == "rag" and RAG_OCR_PACKAGES:
            # E10b: images built before OCR lack Tesseract; the first release
            # after it adds the packages (needs the package mirrors once), later
            # ones find them in the base and skip both steps.
            dockerfile += ("RUN command -v tesseract >/dev/null 2>&1 || (apt-get update && apt-get install -y "
                           "--no-install-recommends " + RAG_OCR_PACKAGES + " && rm -rf /var/lib/apt/lists/*)\n"
                           "RUN python -c 'import pypdfium2, PIL' 2>/dev/null || pip install --no-cache-dir pypdfium2 pillow\n")
        for directory in copied:
            dockerfile += "RUN rm -rf /app/" + directory + "\nCOPY " + directory + " /app/" + directory + "\n"
        dockerfile += ("LABEL vivesec.source-sha256=" + fingerprint + " vivesec.version=" + json.dumps(builds[kind]["version"])
                       + " vivesec.commit=" + json.dumps(builds[kind]["commit"]) + "\n")
        if kind == "adapter":
            dockerfile += "ENV ADAPTER_CHAT_POLICY=locked_hybrid\n"
        (build / "Dockerfile").write_text(dockerfile, encoding="utf-8")
        (build / ".dockerignore").write_text("*\n!Dockerfile\n" + "".join("!" + directory + "/\n!" + directory + "/**\n" for directory in copied), encoding="utf-8")
        image = "vivesec-" + kind + ":release-" + stamp
        command("docker", "build", "-q", "-t", image, str(build))
        manifest["images"][kind] = image
        persist(stage, manifest)
        print("BUILT=" + image, flush=True)
        if kind == "rag" and RAG_OCR_PACKAGES:
            langs = command("docker", "run", "--rm", "--network", "none", "--entrypoint", "tesseract", image,
                            "--list-langs").splitlines()[1:]
            print("OCR_LANGS=" + "+".join(lang.strip() for lang in langs if lang.strip()), flush=True)
        if kind != "ui":
            output = command("docker", "run", "--rm", "--network", "none", "-e", "VIVESEC_BACKEND=fallback",
                             "--entrypoint", "python", image, "-m", "unittest", "discover", "-s",
                             "rag_service" if kind == "rag" else "adapter", "-p", "*test.py")
            print("TESTS_OK=" + kind, flush=True)
    manifest["state"] = "prepared"
    persist(stage, manifest)
    print("PREPARED=" + str(stage), flush=True)


def recreate(stage, manifest, kind):
    old = manifest["old"][kind]
    config = {key: old["Config"][key] for key in
              ("User", "Env", "Cmd", "Entrypoint", "WorkingDir", "ExposedPorts", "Labels", "StopSignal")
              if old["Config"].get(key) is not None}
    env = environment(old)
    if kind == "adapter":
        env["ADAPTER_CHAT_POLICY"] = "locked_hybrid"
    if kind == "ui":
        env["ADAPTER_DEMO_DRIVE_PICKER"] = "0"
    config["Env"] = [key + "=" + value for key, value in env.items()]
    config["Image"] = manifest["images"][kind]
    # Labels given at create time override the image's, and the old container's
    # labels still name the previous release, so the identity is set explicitly.
    builds = manifest.get("builds", {}).get(kind, {})
    config["Labels"] = {**(config.get("Labels") or {}), "vivesec.source-sha256": manifest["source_sha256"],
                        **{"vivesec." + key: value for key, value in builds.items()}}
    config["HostConfig"] = json.loads(json.dumps(old["HostConfig"]))
    if kind == "ui":
        config["HostConfig"]["Binds"] = [
            str(stage / "build" / ".output") + ":/app/.output:ro" if bind.split(":")[1] == "/app/.output" else bind
            for bind in (config["HostConfig"].get("Binds") or [])]
    docker_api("/containers/create?name=vivesec-" + kind, config)
    manifest["new"].append(kind)
    persist(stage, manifest)
    command("docker", "start", "vivesec-" + kind)


def check(stage, manifest):
    env = environment(manifest["old"]["adapter"])
    ready("http://127.0.0.1:%s/api/v1/status" % env["ADAPTER_PORT"], stage / "status.json")
    status = json.loads((stage / "status.json").read_text())
    assert status.get("ok") and status["chat_policy"]["policy"] == "locked_hybrid"
    if manifest.get("version"):
        assert status["version"]["release"] == manifest["version"] and status["version"]["consistent"], \
            "Running build does not match the release"
    rag_env = environment(manifest["old"]["rag"])
    headers = {"X-API-Key": rag_env.get("RAG_API_KEY", "")}
    for endpoint in ("health", "stats"):
        request = urllib.request.Request("http://127.0.0.1:%s/%s" % (rag_env["RAG_PORT"], endpoint), headers=headers)
        with urllib.request.urlopen(request, timeout=30) as response:
            result = json.load(response)
        (stage / (endpoint + ".json")).write_text(json.dumps(result), encoding="utf-8")
    ready("http://127.0.0.1:%s/" % environment(manifest["old"]["ui"])["PORT"], stage / "ui.html")
    assert all(inspect("vivesec-" + kind)["Config"]["Labels"]["vivesec.source-sha256"] == manifest["source_sha256"]
               for kind in ("rag", "adapter", "ui"))
    print(json.dumps({"check": "ok", "stats": json.loads((stage / "stats.json").read_text()),
                      "policy": status["chat_policy"], "ws_fs": status.get("ws_fs")}), flush=True)


HYBRID_ATTEMPTS = 3
# System packages the RAG image needs for OCR (E10b); RELEASE_RAG_OCR=off
# leaves the image as it is (a box without package mirrors), and the RAG then
# reports OCR as unavailable instead of reading scans.
RAG_OCR_PACKAGES = ("" if os.environ.get("RELEASE_RAG_OCR", "on").strip().lower() == "off"
                    else "tesseract-ocr tesseract-ocr-hun tesseract-ocr-deu tesseract-ocr-dan")


def hybrid_probe(ask, attempts=HYBRID_ATTEMPTS):
    """A general-knowledge question in the hybrid profile must be answered by the
    model without document citations. The model still cites a retrieved passage
    it finds related now and then (about one release in five), which failed
    verified releases; one uncited answer out of `attempts` proves the profile.
    Every answer must be a hybrid model answer. Returns (result, attempts used)."""
    cited, result = [], {}
    for attempt in range(1, attempts + 1):
        result = ask(attempt)
        assert result.get("ok") and result.get("answer") and "ollama:" in result.get("backend", ""), \
            json.dumps({k: result.get(k) for k in ("ok", "backend", "error")}, ensure_ascii=False)[:400]
        assert result.get("profile") == "hybrid", result.get("profile")
        if result.get("citations") == []:
            return result, attempt
        cited.append([c.get("path") for c in result.get("citations") or []])
    raise AssertionError(json.dumps({"hybrid answers cited documents": cited, "graph": result.get("graph")},
                                    ensure_ascii=False)[:800])


def verify(stage, manifest):
    check(stage, manifest)
    adapter_env = environment(manifest["old"]["adapter"])
    ui_env = environment(manifest["old"]["ui"])
    headers = {"Content-Type": "application/json",
               "VVS-Drive": base64.urlsafe_b64encode(ui_env["ADAPTER_DEMO_DRIVE"].encode()).decode(),
               "VVS-User": "release-verification-" + manifest["stamp"]}
    base = "http://127.0.0.1:" + adapter_env["ADAPTER_PORT"]

    def post(url, body, request_headers):
        request = urllib.request.Request(url, data=json.dumps(body).encode(), headers=request_headers)
        with urllib.request.urlopen(request, timeout=180) as response:
            return json.load(response)

    def ask(attempt):
        # A fresh user per attempt: an earlier answer must not become history.
        return post(base + "/api/v1/ui/query", {"query": "Mi\u00e9rt k\u00e9k az \u00e9g?", "profile": "hybrid", "top_k": 5},
                    dict(headers, **{"VVS-User": headers["VVS-User"] + "-%d" % attempt}))

    result, attempts = hybrid_probe(ask)
    rag_env = environment(manifest["old"]["rag"])
    missing = post("http://127.0.0.1:" + rag_env["RAG_PORT"] + "/rag/search_context",
                   {"corpus_id": result["corpus_id"], "question": "report", "source_paths": ["release-test-file-that-does-not-exist.txt"],
                    "include_debug": True}, {"Content-Type": "application/json", "X-API-Key": rag_env.get("RAG_API_KEY", "")})
    assert missing.get("contexts") == [], "Strict file constraint failed"
    assert missing.get("debug", {}).get("retrieval") == "dense+fts5", "New retrieval not active"
    for kind, relative in (("adapter", "adapter/llm.py"), ("adapter", "adapter/service.py"),
                           ("rag", "rag_service/sqlite_store.py"), ("rag", "rag_service/retrieval.py")):
        copied = stage / ("verified-" + relative.replace("/", "-"))
        command("docker", "cp", "vivesec-" + kind + ":/app/" + relative, str(copied))
        assert copied.read_bytes() == (stage / "build" / relative).read_bytes()
    summary = {"verification": "ok", "profile": result["profile"], "backend": result["backend"], "hybrid_attempts": attempts,
               "answer": result["answer"], "scope": result["corpus_id"], "retrieval": missing.get("debug"),
               "code_hashes": "match release"}
    (stage / "verification.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    try:
        prune(stage, manifest)
    except Exception as error:
        # Housekeeping must never turn a verified release into a failed one.
        print("PRUNE_WARNING=" + str(error).replace("\n", " ")[:400], flush=True)


STAGE_RE = re.compile(r"^vivesec-release-(\d{8}-\d{6})-[A-Za-z0-9_]+$")
# Containers parked by this tool (-prev/-failed) and by the 19x helper scripts
# (-prev with a minute or second stamp).
PARKED_RE = re.compile(r"^vivesec-(?:rag|adapter|ui)-(?:prev|failed)-(\d{8}-\d{4}(?:\d{2})?)$")


def keep_count():
    # Every release leaves a ~1.6 GB stage (index backup) behind; on the eMMC
    # they filled the disk. Unset or invalid -> 2 (current + one rollback
    # level); 0 disables pruning; values below 2 are raised to 2.
    raw = os.environ.get("RELEASE_KEEP_STAGES", "2").strip()
    if raw == "0":
        return 0
    try:
        return max(2, int(raw))
    except ValueError:
        return 2


def _kept(stages, current, keep):
    current_stamp = stages[current][0]
    older = sorted((stamp, path) for path, (stamp, state) in stages.items()
                   if path != current and stamp < current_stamp and state == "deployed")
    return {current} | {path for _, path in older[max(0, len(older) - (keep - 1)):]}


def parked_to_remove(stages, current, containers, keep):
    """Stopped -prev/-failed containers no older than the oldest kept stage.

    stages: {path: (stamp, state)}, current: path of the live release stage,
    containers: {name: {"running": bool, "binds": [source, ...]}}.
    A container parked by release X is what rolling X back needs, so with the
    current and the previous release kept, only the current release's parked
    containers (and later helper ones) survive.
    """
    cutoff = min(stages[path][0] for path in _kept(stages, current, keep))
    parked = []
    for name, info in sorted(containers.items()):
        match = PARKED_RE.match(name)
        if match and not info["running"] and match.group(1).ljust(15, "0") <= cutoff:
            parked.append(name)
    return parked


def stages_to_remove(stages, current, containers, keep):
    """Stage directories older than the current one, outside the kept set and
    not bound by any existing container (running or not)."""
    kept = _kept(stages, current, keep)
    current_stamp = stages[current][0]
    binds = [source.rstrip("/") for info in containers.values() for source in info["binds"]]
    removable = []
    for path, (stamp, _) in sorted(stages.items()):
        if path in kept or stamp >= current_stamp:
            continue
        if any(source == path or source.startswith(path + "/") for source in binds):
            continue
        removable.append(path)
    return removable


def inventory(root):
    stages = {}
    for entry in sorted(root.iterdir()):
        match = STAGE_RE.match(entry.name)
        if not match or entry.is_symlink() or not entry.is_dir():
            continue
        try:
            state = json.loads((entry / "manifest.json").read_text()).get("state")
        except (OSError, ValueError):
            state = None
        stages[str(entry)] = (match.group(1), state)
    containers = {}
    ids = command("docker", "ps", "-aq").split()
    if ids:
        for container in json.loads(command("docker", "inspect", *ids)):
            containers[container["Name"].lstrip("/")] = {
                "running": bool(container["State"]["Running"]),
                "binds": [mount["Source"] for mount in container.get("Mounts") or [] if mount.get("Type") == "bind"]}
    return stages, containers


def prune(stage, manifest):
    keep = keep_count()
    if not keep:
        print("PRUNE_SKIPPED=RELEASE_KEEP_STAGES=0", flush=True)
        return
    stage = Path(stage).resolve()
    assert manifest["state"] == "deployed", "Only a deployed release stage can anchor pruning"
    live = [mount["Source"].rstrip("/") for mount in inspect("vivesec-ui").get("Mounts") or []
            if mount.get("Destination") == "/app/.output"]
    assert live and live[0].startswith(str(stage) + "/"), "The live UI does not run from this stage"
    stages, containers = inventory(stage.parent)
    parked = parked_to_remove(stages, str(stage), containers, keep)
    for name in parked:
        try:
            command("docker", "rm", name)
            print("PRUNED_CONTAINER=" + name, flush=True)
        except RuntimeError as error:
            print("PRUNE_WARNING=" + name + ": " + str(error).replace("\n", " ")[:200], flush=True)
    # Re-read the containers so a parked one that could not be removed keeps
    # protecting the stage it binds.
    stages, containers = inventory(stage.parent)
    removable = stages_to_remove(stages, str(stage), containers, keep)
    for path in removable:
        try:
            shutil.rmtree(path)
            print("PRUNED_STAGE=" + path, flush=True)
        except OSError as error:
            print("PRUNE_WARNING=" + path + ": " + str(error)[:200], flush=True)
    print("PRUNE_OK kept=%d removed_containers=%d removed_stages=%d" % (keep, len(parked), len(removable)), flush=True)


def rollback(stage, manifest):
    for kind in ("ui", "adapter", "rag"):
        if kind in manifest["new"]:
            command("docker", "stop", "-t", "45", "vivesec-" + kind)
            command("docker", "rename", "vivesec-" + kind, "vivesec-" + kind + "-failed-" + manifest["stamp"])
    if "rag" in manifest["renamed"]:
        previous = "vivesec-rag-prev-" + manifest["stamp"]
        print(run_helper(stage, manifest, "rag", "restore-index", previous), flush=True)
    for kind in ("rag", "adapter", "ui"):
        if kind in manifest["renamed"]:
            command("docker", "rename", "vivesec-" + kind + "-prev-" + manifest["stamp"], "vivesec-" + kind)
        command("docker", "start", "vivesec-" + kind)
    manifest["state"] = "rolled-back"
    persist(stage, manifest)
    print("ROLLBACK_OK=" + str(stage), flush=True)


def promote(stage):
    stage = Path(stage)
    manifest = json.loads((stage / "manifest.json").read_text())
    assert manifest["state"] == "prepared"
    for kind, old in manifest["old"].items():
        assert inspect("vivesec-" + kind)["Id"] == old["Id"], "Live state changed"
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 18095))
    candidate = "vivesec-release-check-" + manifest["stamp"]
    command("docker", "run", "-d", "--name", candidate, "--network", "host", "-e", "PORT=18095",
            "-e", "HOST=127.0.0.1", "-e", "NITRO_HOST=127.0.0.1", "-e",
            "ADAPTER_URL=" + environment(manifest["old"]["ui"])["ADAPTER_URL"], manifest["images"]["ui"],
            *manifest["old"]["ui"]["Config"]["Cmd"])
    try:
        ready("http://127.0.0.1:18095/", stage / "ui-preflight.html")
    finally:
        command("docker", "rm", "-f", candidate)
    print("UI_PREFLIGHT_OK", flush=True)
    try:
        for kind in ("ui", "adapter", "rag"):
            command("docker", "stop", "-t", "60", "vivesec-" + kind)
        print(run_helper(stage, manifest, "rag", "backup-index", "vivesec-rag"), flush=True)
        print(run_helper(stage, manifest, "adapter", "backup-adapter", "vivesec-adapter"), flush=True)
        for kind in ("rag", "adapter", "ui"):
            previous = "vivesec-" + kind + "-prev-" + manifest["stamp"]
            command("docker", "tag", manifest["old"][kind]["Image"], "vivesec-" + kind + ":prev-" + manifest["stamp"])
            command("docker", "rename", "vivesec-" + kind, previous)
            manifest["renamed"].append(kind)
            persist(stage, manifest)
            recreate(stage, manifest, kind)
            if kind == "rag":
                ready("http://127.0.0.1:%s/ready" % environment(manifest["old"][kind])["RAG_PORT"], stage / "ready.json")
        check(stage, manifest)
        for kind in ("rag", "adapter", "ui"):
            command("docker", "tag", manifest["images"][kind], "vivesec-" + kind + ":latest")
        manifest["state"] = "deployed"
        persist(stage, manifest)
        print("DEPLOYED=" + str(stage), flush=True)
    except BaseException:
        rollback(stage, manifest)
        raise


if __name__ == "__main__":
    operation = sys.argv[1]
    if operation == "pack":
        pack(sys.argv[2])
    elif operation in ("backup-index", "backup-adapter", "restore-index"):
        helper(operation, sys.argv[2])
    elif operation == "prepare":
        prepare(*sys.argv[2:4])
    elif operation == "promote":
        promote(sys.argv[2])
    elif operation in ("check", "rollback", "verify", "prune"):
        root = Path(sys.argv[2])
        globals()[operation](root, json.loads((root / "manifest.json").read_text()))
    else:
        raise SystemExit("Unknown operation")