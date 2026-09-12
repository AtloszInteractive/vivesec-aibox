import hashlib
import base64
import http.client
import json
import os
from pathlib import Path
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
    paths = subprocess.check_output(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard",
                                     "--", "adapter", "rag_service", "poc"]).decode("utf-8").split("\0")
    with tarfile.open(destination, "w:gz") as archive:
        for path in sorted(set(paths) - {""}):
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
    manifest = {"stamp": stamp, "source_sha256": fingerprint,
                "ui_sha256": hashlib.sha256(Path(ui_archive).read_bytes()).hexdigest(),
                "old": old, "images": {}, "renamed": [], "new": [], "state": "preparing"}
    persist(stage, manifest)
    for kind in ("rag", "adapter", "ui"):
        copied = ["poc", "rag_service"] if kind == "rag" else (["adapter"] if kind == "adapter" else [".output"])
        dockerfile = "FROM " + old[kind]["Image"] + "\nWORKDIR /app\n"
        for directory in copied:
            dockerfile += "RUN rm -rf /app/" + directory + "\nCOPY " + directory + " /app/" + directory + "\n"
        dockerfile += "LABEL vivesec.source-sha256=" + fingerprint + "\n"
        if kind == "adapter":
            dockerfile += "ENV ADAPTER_CHAT_POLICY=locked_hybrid\n"
        (build / "Dockerfile").write_text(dockerfile, encoding="utf-8")
        (build / ".dockerignore").write_text("*\n!Dockerfile\n" + "".join("!" + directory + "/\n!" + directory + "/**\n" for directory in copied), encoding="utf-8")
        image = "vivesec-" + kind + ":release-" + stamp
        command("docker", "build", "-q", "-t", image, str(build))
        manifest["images"][kind] = image
        persist(stage, manifest)
        print("BUILT=" + image, flush=True)
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
    config["Labels"] = {**(config.get("Labels") or {}), "vivesec.source-sha256": manifest["source_sha256"]}
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

    result = post(base + "/api/v1/ui/query", {"query": "Mi\u00e9rt k\u00e9k az \u00e9g?", "profile": "hybrid", "top_k": 5}, headers)
    assert result.get("ok") and result.get("answer") and "ollama:" in result.get("backend", "")
    assert result.get("profile") == "hybrid" and result.get("citations") == []
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
    summary = {"verification": "ok", "profile": result["profile"], "backend": result["backend"],
               "answer": result["answer"], "scope": result["corpus_id"], "retrieval": missing.get("debug"),
               "code_hashes": "match release"}
    (stage / "verification.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False), flush=True)


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
    elif operation in ("check", "rollback", "verify"):
        root = Path(sys.argv[2])
        globals()[operation](root, json.loads((root / "manifest.json").read_text()))
    else:
        raise SystemExit("Unknown operation")