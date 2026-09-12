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


FILES = {
    "rag": ["rag_service/" + name for name in
            ("store.py", "sqlite_store.py", "service.py", "retrieval.py")],
    "adapter": ["adapter/" + name for name in
                ("service.py", "llm.py", "session.py", "chat_policy.py")],
}


def command(*args):
    return subprocess.check_output(args, text=True).strip()


def env_for(container):
    return dict(entry.split("=", 1) for entry in container["Config"]["Env"])


def get_json(url, body=None, headers=None):
    request = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Content-Type": "application/json", **(headers or {})})
    with urllib.request.urlopen(request, timeout=600) as response:
        return json.load(response)


def backup(source, destination):
    import sqlite3
    import sqlite_vec
    original = sqlite3.connect("file:" + source + "?mode=ro", uri=True)
    copied = sqlite3.connect(destination)
    for connection in (original, copied):
        connection.enable_load_extension(True)
        sqlite_vec.load(connection)
        connection.enable_load_extension(False)
    try:
        original.backup(copied)
        assert copied.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        print(json.dumps({"backup": "ok", "documents": copied.execute("SELECT count(*) FROM documents").fetchone()[0],
                          "chunks": copied.execute("SELECT count(*) FROM chunks").fetchone()[0],
                          "corpora": copied.execute("SELECT corpus_id, count(*) FROM documents GROUP BY corpus_id").fetchall()}))
    finally:
        copied.close()
        original.close()


def prepare(archive):
    live = {kind: inspect("vivesec-" + kind) for kind in ("rag", "adapter", "ui")}
    for port in (18092, 18093, 18082, 18083, 18084):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", port))
    stage = Path(tempfile.mkdtemp(prefix="retrieval-trial-", dir=Path.home()))
    os.chmod(stage, 0o700)
    print("STAGE=" + str(stage), flush=True)
    with tarfile.open(archive) as payload:
        for name in sum(FILES.values(), []):
            target = stage / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload.extractfile(name).read())
    shutil.copyfile(__file__, stage / "trial.py")
    command("docker", "run", "--rm", "--network", "none", "--volumes-from", "vivesec-rag:ro",
            "-v", str(stage) + ":/trial", "--entrypoint", "python", live["rag"]["Image"],
            "/trial/trial.py", "backup", env_for(live["rag"])["RAG_INDEX_PATH"], "/trial/snapshot.db")
    print("BACKUP_BYTES=" + str((stage / "snapshot.db").stat().st_size), flush=True)
    for version in ("baseline", "candidate"):
        (stage / version).mkdir()
        shutil.copyfile(stage / "snapshot.db", stage / version / "index.db")
    images = {}
    (stage / ".dockerignore").write_text(
        "*\n!Dockerfile\n!adapter/\n!adapter/**\n!rag_service/\n!rag_service/**\n", encoding="utf-8")
    for kind in ("rag", "adapter"):
        dockerfile = "FROM " + live[kind]["Image"] + "\n"
        dockerfile += "".join("COPY " + name + " /app/" + name + "\n" for name in FILES[kind])
        (stage / "Dockerfile").write_text(dockerfile, encoding="utf-8")
        images[kind] = command("docker", "build", "-q", str(stage))
    manifest = {"stage": str(stage), "live_ids": {kind: value["Id"] for kind, value in live.items()},
                "containers": [], "drive": env_for(live["ui"]).get("ADAPTER_DEMO_DRIVE", "/storage/drives/aiboxdev"),
                "user": "retrieval-trial-" + stage.name, "ports": {"baseline": 18082, "candidate": 18083, "ui": 18084}}
    manifest_path = stage / "manifest.json"

    def start(name, image, environment, mounts):
        env_path = stage / (name + ".env")
        env_path.write_text("".join(key + "=" + value + "\n" for key, value in environment.items()), encoding="utf-8")
        os.chmod(env_path, 0o600)
        args = ["docker", "run", "-d", "--name", name, "--network", "host", "--restart", "no",
                "--env-file", str(env_path)]
        for mount in mounts:
            args.extend(["-v", mount])
        command(*args, image)
        manifest["containers"].append(name)
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    try:
        for version, rag_port, adapter_port in (("baseline", 18092, 18082), ("candidate", 18093, 18083)):
            environment = env_for(live["rag"])
            environment.update(RAG_HOST="127.0.0.1", RAG_PORT=str(rag_port), RAG_INDEX_PATH="/trial-data/index.db")
            start("vivesec-trial-rag-" + version,
                  images["rag"] if version == "candidate" else live["rag"]["Image"],
                  environment, [str(stage / version) + ":/trial-data"])
            command("curl", "-fsS", "--retry", "12", "--retry-connrefused", "--retry-max-time", "120",
                    "http://127.0.0.1:%d/ready" % rag_port)
            environment = env_for(live["adapter"])
            environment.update(ADAPTER_HOST="127.0.0.1", ADAPTER_PORT=str(adapter_port),
                               RAG_URL="http://127.0.0.1:%d" % rag_port, ADAPTER_DISCOVERY="off",
                               ADAPTER_TLS="off", ADAPTER_STORAGE_MODE="off", ADAPTER_WATCHDOG_ENABLED="off",
                               ADAPTER_META_PATH="/trial-data/meta.json", ADAPTER_PKI_DIR="/trial-data/pki",
                               ADAPTER_SESSION_DIR="/trial-data/sessions", ADAPTER_JOBS_DIR="/trial-data/jobs",
                               ADAPTER_FILES_DIR="/trial-data/generated", ADAPTER_FEEDBACK_DIR="/trial-data/feedback")
            data = stage / (version + "-adapter")
            data.mkdir()
            start("vivesec-trial-adapter-" + version,
                  images["adapter"] if version == "candidate" else live["adapter"]["Image"],
                  environment, [str(data) + ":/trial-data"])
            command("curl", "-fsS", "--retry", "12", "--retry-connrefused", "--retry-max-time", "120",
                    "http://127.0.0.1:%d/api/v1/status" % adapter_port)
        environment = env_for(live["ui"])
        environment.update(ADAPTER_URL="http://127.0.0.1:18083", PORT="18084", HOST="127.0.0.1",
                           NITRO_HOST="127.0.0.1", ADAPTER_DEMO_USER=manifest["user"])
        start("vivesec-trial-ui", live["ui"]["Image"], environment, [])
        command("curl", "-fsS", "--retry", "12", "--retry-connrefused", "--retry-max-time", "120",
                "http://127.0.0.1:18084/", "-o", "/dev/null")
        assert all(inspect("vivesec-" + kind)["Id"] == value for kind, value in manifest["live_ids"].items())
        print("PASS: isolated baseline/candidate and UI ready; production containers unchanged", flush=True)
    except BaseException:
        for name in reversed(manifest["containers"]):
            subprocess.run(["docker", "stop", name], capture_output=True)
        print("FAILED: trial stopped, production untouched; stage preserved " + str(stage), flush=True)
        raise


def inspect(name):
    return json.loads(subprocess.check_output(["docker", "inspect", name], text=True))[0]


def main():
    allowed = {
        "RAG_INDEX_PATH", "RAG_VECTOR_BACKEND", "RAG_HOST", "RAG_PORT", "VIVESEC_BACKEND",
        "OLLAMA_URL", "OLLAMA_HOST", "EMBED_MODEL", "VIVESEC_EMBED_MODEL", "VIVESEC_OLLAMA_URL",
        "ADAPTER_PORT", "ADAPTER_HOST", "RAG_URL", "ADAPTER_RAG_URL", "ADAPTER_GEN_MODEL",
        "ADAPTER_THINK", "ADAPTER_NUM_CTX", "ADAPTER_NUM_PREDICT", "ADAPTER_CHAT_POLICY",
        "ADAPTER_SESSION_DIR", "ADAPTER_JOBS_DIR", "ADAPTER_STORAGE_MODE", "ADAPTER_PKI_DIR",
        "ADAPTER_BASE_URL", "ADAPTER_URL", "DEMO_USER", "DEMO_DRIVE", "PORT", "HOST",
    }
    for name in ("vivesec-rag", "vivesec-adapter", "vivesec-ui"):
        container = inspect(name)
        environment = dict(entry.split("=", 1) for entry in container["Config"]["Env"])
        print(json.dumps({
            "name": name, "id": container["Id"], "image": container["Image"],
            "network": container["HostConfig"]["NetworkMode"],
            "command": container["Config"]["Cmd"], "entrypoint": container["Config"]["Entrypoint"],
            "mounts": [{key: mount.get(key) for key in ("Source", "Destination", "RW")}
                       for mount in container["Mounts"]],
            "environment_keys": sorted(environment),
            "settings": {key: value for key, value in environment.items() if key in allowed},
        }), flush=True)


if __name__ == "__main__":
    if len(sys.argv) == 1:
        main()
    elif sys.argv[1] == "backup":
        backup(*sys.argv[2:4])
    elif sys.argv[1] == "prepare":
        prepare(sys.argv[2])
    else:
        raise SystemExit("Unknown operation")