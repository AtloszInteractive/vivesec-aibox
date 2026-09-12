import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time


def command(*args):
    return subprocess.check_output(args, text=True).strip()


def inspect(name):
    return json.loads(command("docker", "inspect", name))[0]


def main():
    candidate, baseline = map(Path, sys.argv[1:3])
    old = inspect("vivesec-adapter")
    host = old["HostConfig"]
    assert host["NetworkMode"] == "host" and not host["Privileged"]
    assert host["RestartPolicy"]["Name"] == "unless-stopped"
    for option in ("Devices", "CapAdd", "CapDrop", "SecurityOpt", "ExtraHosts", "Tmpfs"):
        assert not host.get(option), "Unsupported config: " + option
    assert not host.get("ReadonlyRootfs")
    ui_id = inspect("vivesec-ui")["Id"]
    rag_id = inspect("vivesec-rag")["Id"]
    stamp = time.strftime("%Y%m%d-%H%M%S")
    stage = Path(tempfile.mkdtemp(prefix="adapter-general-", dir=Path.home()))
    os.chmod(stage, 0o700)
    command("docker", "cp", "vivesec-adapter:/app/adapter/llm.py", str(stage / "before.py"))
    assert hashlib.sha256((stage / "before.py").read_bytes()).digest() == hashlib.sha256(baseline.read_bytes()).digest(), "Live source changed; abort"
    (stage / "container.json").write_text(json.dumps(old), encoding="utf-8")
    (stage / "llm.py").write_bytes(candidate.read_bytes())
    (stage / "Dockerfile").write_text(
        "FROM " + old["Image"] + "\nCOPY llm.py /app/adapter/llm.py\n", encoding="utf-8")
    image = "vivesec-adapter:general-" + stamp
    previous = "vivesec-adapter-prev-" + stamp
    command("docker", "build", "-q", "-t", image, str(stage))
    subprocess.run(["docker", "run", "--rm", "--workdir", "/app/adapter", "--entrypoint", "python",
                    image, "-m", "unittest", "chat_policy_test", "llm_stream_test"], check=True)
    command("docker", "tag", old["Image"], "vivesec-adapter:prev-" + stamp)
    command("docker", "rename", "vivesec-adapter", previous)
    try:
        command("docker", "stop", previous)
        args = ["docker", "run", "-d", "--name", "vivesec-adapter", "--network", "host",
                "--restart", "unless-stopped", "--volumes-from", previous]
        for entry in old["Config"]["Env"]:
            args.extend(["-e", entry])
        args.append(image)
        command(*args)
        status_file = stage / "status.json"
        command("curl", "-fsS", "--retry", "15", "--retry-connrefused", "--retry-max-time", "60",
                "--max-time", "5", "http://127.0.0.1:80/api/v1/status", "-o", str(status_file))
        status = json.loads(status_file.read_text())
        assert status.get("ok") and status["chat_policy"]["policy"] == "locked_hybrid", status
        assert inspect("vivesec-ui")["Id"] == ui_id and inspect("vivesec-rag")["Id"] == rag_id
    except BaseException:
        subprocess.run(["docker", "rm", "-f", "vivesec-adapter"], check=False, capture_output=True)
        command("docker", "rename", previous, "vivesec-adapter")
        command("docker", "start", "vivesec-adapter")
        print("ROLLBACK: original adapter restored", flush=True)
        raise
    command("docker", "tag", image, "vivesec-adapter:latest")
    print("PASS: prompt-only image deployed; UI and RAG unchanged", flush=True)
    print("IMAGE=" + image + "\nROLLBACK_CONTAINER=" + previous + "\nSTAGE=" + str(stage), flush=True)
    print("POLICY=" + json.dumps(status["chat_policy"]), flush=True)


if __name__ == "__main__":
    main()