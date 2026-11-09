#!/usr/bin/env python3
"""Authentication service and standalone switch on a box (E27 S4).

    python3 auth-deploy.py deploy <source-dir>   build + test the vivesec-auth image, prepare
                                                 the data directory, (re)start vivesec-auth
    python3 auth-deploy.py enable                adapter + web UI -> standalone mode (login gate)
    python3 auth-deploy.py disable               adapter + web UI -> back to vivesecbox mode
    python3 auth-deploy.py status

<source-dir> holds auth_service/ and adapter/ (the auth tests check the tokens
with the adapter's verifier): a git checkout, or the build directory of a
release stage (172-release-box.py pack includes auth_service).

`deploy` never touches the adapter or the UI. `enable` and `disable` recreate
them with the same image, configuration and binds, changing only the
standalone settings and the two read-only key mounts; when a container does not
come up healthy, both are put back as they were. Users are created afterwards
with `docker exec -it vivesec-auth python /app/auth_service/manage.py ...`.

Only docker is needed (no sudo): the data directory is created through a
short-lived container. Settings: AIBOX_AUTH_DIR (default /data/auth),
AIBOX_AUTH_BASE_IMAGE (default python:3.12-slim), AIBOX_AUTH_PORT (default
8097); AUTH_* variables in the environment are passed to the service.
"""
import hashlib
import http.client
import json
import os
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

AUTH_CONTAINER = "vivesec-auth"
AUTH_UID = "10001:10001"
DATA_IN_CONTAINER = "/data/auth"
KEY_MOUNT_DIR = "/run/aibox-auth"
ADAPTER_KEY = KEY_MOUNT_DIR + "/identity-hs256.key"
UI_KEY = KEY_MOUNT_DIR + "/service.key"
DEFAULT_ISSUER = "vivesec-aibox-auth"
DEFAULT_AUDIENCE = "vivesec-aibox-adapter"

# Settings the switch owns. `enable` sets them; `disable` removes them, so the
# box falls back to the defaults (vivesecbox mode, identity off).
ADAPTER_STANDALONE_KEYS = ("ADAPTER_INTEGRATION", "ADAPTER_IDENTITY_MODE", "ADAPTER_DIRECTORY",
                           "ADAPTER_IDENTITY_ALGORITHMS", "ADAPTER_IDENTITY_HS256_SECRET_FILE",
                           "ADAPTER_IDENTITY_ISSUER", "ADAPTER_IDENTITY_AUDIENCE")
UI_STANDALONE_KEYS = ("ADAPTER_INTEGRATION", "AUTH_URL", "AUTH_SERVICE_KEY_FILE")
# Must not be on in standalone mode: it grants every drive to every user.
ADAPTER_FORBIDDEN_KEYS = ("ADAPTER_SCOPE_ALL_DRIVES",)


def settings(env=None):
    env = env if env is not None else os.environ
    port = (env.get("AIBOX_AUTH_PORT") or "8097").strip()
    if not port.isdigit() or not 0 < int(port) < 65536:
        raise SystemExit("AIBOX_AUTH_PORT must be a port number")
    data_dir = (env.get("AIBOX_AUTH_DIR") or "/data/auth").strip().rstrip("/")
    if not data_dir.startswith("/") or ".." in data_dir.split("/") or data_dir.count("/") < 2:
        raise SystemExit("AIBOX_AUTH_DIR must be an absolute path below a top-level directory")
    return {"data_dir": data_dir, "port": int(port),
            "base_image": (env.get("AIBOX_AUTH_BASE_IMAGE") or "python:3.12-slim").strip()}


# ---------------------------------------------------------------- docker

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
            raise RuntimeError("Docker API %s failed: HTTP %d %s"
                               % (path.split("?")[0], response.status, payload.get("message", "")))
        return payload
    finally:
        connection.close()


def command(*args, stdin=None):
    result = subprocess.run(args, text=True, capture_output=True, input=stdin)
    if result.returncode:
        raise RuntimeError("Command failed: %s\n%s" % (shlex.join(args[:3]), result.stderr[-1800:]))
    return result.stdout.strip()


def inspect(name):
    try:
        return json.loads(command("docker", "inspect", name))[0]
    except RuntimeError:
        return None


def environment(container):
    return dict(entry.split("=", 1) for entry in container["Config"]["Env"] or [])


def http_json(url, timeout=5, method="GET"):
    request = urllib.request.Request(url, data=b"{}" if method == "POST" else None, method=method,
                                     headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.load(e)
        except ValueError:
            return e.code, {}


def wait_until(probe, seconds=90):
    deadline = time.time() + seconds
    last = None
    while time.time() < deadline:
        try:
            if probe():
                return True
        except (OSError, ValueError, urllib.error.URLError) as e:
            last = e
        time.sleep(1)
    if last:
        print("last probe error: %s" % last, flush=True)
    return False


# ---------------------------------------------------------------- pure changes (tested)

def with_settings(env, set_values, remove):
    """`env` with `remove` keys dropped and `set_values` applied."""
    out = {key: value for key, value in env.items() if key not in remove}
    out.update(set_values)
    return out


def with_binds(binds, add, remove_targets):
    """Binds without any mount on `remove_targets`, plus `add`."""
    kept = [bind for bind in (binds or []) if bind.split(":")[1] not in remove_targets]
    return kept + [bind for bind in add if bind not in kept]


def adapter_change(env, binds, data_dir, auth_env, enable):
    keys = ADAPTER_STANDALONE_KEYS + ADAPTER_FORBIDDEN_KEYS
    targets = {ADAPTER_KEY}
    if not enable:
        return with_settings(env, {}, keys), with_binds(binds, [], targets)
    values = {"ADAPTER_INTEGRATION": "standalone", "ADAPTER_IDENTITY_MODE": "require",
              "ADAPTER_DIRECTORY": "token", "ADAPTER_IDENTITY_ALGORITHMS": "HS256",
              "ADAPTER_IDENTITY_HS256_SECRET_FILE": ADAPTER_KEY,
              "ADAPTER_IDENTITY_ISSUER": auth_env.get("AUTH_JWT_ISSUER") or DEFAULT_ISSUER,
              "ADAPTER_IDENTITY_AUDIENCE": auth_env.get("AUTH_JWT_AUDIENCE") or DEFAULT_AUDIENCE}
    bind = data_dir + "/identity-hs256.key:" + ADAPTER_KEY + ":ro"
    return with_settings(env, values, keys), with_binds(binds, [bind], targets)


def ui_change(env, binds, data_dir, port, enable):
    targets = {UI_KEY}
    if not enable:
        return with_settings(env, {}, UI_STANDALONE_KEYS), with_binds(binds, [], targets)
    values = {"ADAPTER_INTEGRATION": "standalone", "AUTH_URL": "http://127.0.0.1:%d" % port,
              "AUTH_SERVICE_KEY_FILE": UI_KEY}
    bind = data_dir + "/service.key:" + UI_KEY + ":ro"
    return with_settings(env, values, UI_STANDALONE_KEYS), with_binds(binds, [bind], targets)


def auth_environment(previous, env=None):
    """AUTH_* settings for the service: the running container's, overridden by
    the caller's environment. The data directory and port are fixed here."""
    env = env if env is not None else os.environ
    values = {key: value for key, value in previous.items() if key.startswith("AUTH_")}
    values.update({key: value for key, value in env.items() if key.startswith("AUTH_")})
    for fixed in ("AUTH_DATA_DIR", "AUTH_HOST", "AUTH_PORT", "AUTH_SERVICE_KEY_FILE",
                  "AUTH_JWT_SECRET_FILE"):
        values.pop(fixed, None)
    return values


def dockerfile(base_image, fingerprint):
    return "\n".join([
        "FROM " + base_image,
        "WORKDIR /app",
        "COPY auth_service /app/auth_service",
        "ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 AUTH_DATA_DIR=" + DATA_IN_CONTAINER,
        "LABEL vivesec.component=auth vivesec.source-sha256=" + fingerprint,
        "USER " + AUTH_UID,
        'ENTRYPOINT ["python", "/app/auth_service/service.py"]',
        "",
    ])


# ---------------------------------------------------------------- deploy

def source_files(source):
    """The service files that go into the image: no tests, no caches."""
    root = source / "auth_service"
    files = sorted(path for path in root.glob("*.py") if not path.name.endswith("_test.py"))
    if not (root / "service.py").is_file() or not (root / "manage.py").is_file():
        raise SystemExit("%s has no auth_service/service.py" % source)
    return files


def deploy(source):
    config = settings()
    source = Path(source).resolve()
    files = source_files(source)
    if not (source / "adapter" / "identity.py").is_file():
        raise SystemExit("%s has no adapter/identity.py (needed by the auth tests)" % source)
    digest = hashlib.sha256()
    for path in files:
        digest.update(path.name.encode() + b"\0" + path.read_bytes())
    fingerprint = digest.hexdigest()
    stamp = time.strftime("%Y%m%d-%H%M%S")
    image = "vivesec-auth:release-" + stamp

    print("TESTS: auth_service in %s" % config["base_image"], flush=True)
    output = subprocess.run(["docker", "run", "--rm", "--network", "none", "-e", "PYTHONDONTWRITEBYTECODE=1",
                             "-v", str(source) + ":/src:ro", "-w", "/src/auth_service",
                             config["base_image"], "python", "-m", "unittest", "discover", "-p", "*_test.py"],
                            text=True, capture_output=True)
    if output.returncode:
        raise SystemExit("auth_service tests failed:\n" + output.stderr[-3000:])
    print("TESTS_OK " + output.stderr.strip().splitlines()[-1], flush=True)

    with tempfile.TemporaryDirectory() as context:
        target = Path(context) / "auth_service"
        target.mkdir()
        for path in files:
            shutil.copyfile(path, target / path.name)
        (Path(context) / "Dockerfile").write_text(dockerfile(config["base_image"], fingerprint))
        command("docker", "build", "-q", "-t", image, context)
    print("BUILT=" + image, flush=True)

    parent, name = config["data_dir"].rsplit("/", 1)
    command("docker", "run", "--rm", "--network", "none", "-v", parent + ":/host", "--entrypoint", "sh",
            config["base_image"], "-c",
            "mkdir -p /host/{0} && chown {1} /host/{0} && chmod 700 /host/{0}".format(name, AUTH_UID))
    print(command("docker", "run", "--rm", "--network", "none", "--user", AUTH_UID,
                  "-v", config["data_dir"] + ":" + DATA_IN_CONTAINER, "--entrypoint", "python",
                  image, "/app/auth_service/manage.py", "init"), flush=True)

    previous = inspect(AUTH_CONTAINER)
    auth_env = auth_environment(environment(previous) if previous else {})
    parked = None
    if previous:
        parked = AUTH_CONTAINER + "-prev-" + stamp
        command("docker", "rename", AUTH_CONTAINER, parked)
        command("docker", "stop", "-t", "20", parked)
    run = ["docker", "run", "-d", "--name", AUTH_CONTAINER, "--network", "host",
           "--restart", "unless-stopped", "--read-only", "--tmpfs", "/tmp:size=8m",
           "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--user", AUTH_UID,
           "--memory", "256m", "--pids-limit", "64",
           "-e", "AUTH_HOST=127.0.0.1", "-e", "AUTH_PORT=%d" % config["port"],
           "-v", config["data_dir"] + ":" + DATA_IN_CONTAINER]
    for key, value in sorted(auth_env.items()):
        run += ["-e", key + "=" + value]
    try:
        command(*run, image)
        healthy = wait_until(lambda: http_json("http://127.0.0.1:%d/auth/health" % config["port"])[0] == 200, 60)
        if not healthy:
            raise RuntimeError("vivesec-auth did not answer /auth/health")
    except BaseException:
        subprocess.run(["docker", "rm", "-f", AUTH_CONTAINER], capture_output=True)
        if parked:
            command("docker", "rename", parked, AUTH_CONTAINER)
            command("docker", "start", AUTH_CONTAINER)
            print("ROLLED_BACK: the previous vivesec-auth runs again", flush=True)
        raise
    command("docker", "tag", image, "vivesec-auth:latest")
    print("AUTH_DEPLOYED=" + image + (" (previous container kept stopped: %s)" % parked if parked else ""),
          flush=True)


# ---------------------------------------------------------------- switch

def recreate(name, env, binds, stamp):
    """Recreate container `name` with a new environment and binds; the old one
    is parked as <name>-prev-<stamp>. Returns the parked name."""
    old = inspect(name)
    config = {key: old["Config"][key] for key in
              ("User", "Cmd", "Entrypoint", "WorkingDir", "ExposedPorts", "Labels", "StopSignal",
               "Image") if old["Config"].get(key) is not None}
    config["Env"] = [key + "=" + value for key, value in env.items()]
    config["HostConfig"] = json.loads(json.dumps(old["HostConfig"]))
    config["HostConfig"]["Binds"] = binds
    parked = name + "-prev-" + stamp
    command("docker", "rename", name, parked)
    command("docker", "stop", "-t", "60", parked)
    try:
        docker_api("/containers/create?name=" + name, config)
        command("docker", "start", name)
    except BaseException:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)
        command("docker", "rename", parked, name)
        command("docker", "start", name)
        raise
    return parked


def restore(name, parked):
    subprocess.run(["docker", "rm", "-f", name], capture_output=True)
    command("docker", "rename", parked, name)
    command("docker", "start", name)


def adapter_ready(port, mode):
    def probe():
        status, body = http_json("http://127.0.0.1:%s/api/v1/version" % port)
        return status == 200 and body.get("integration") == mode
    return probe


def ui_ready(port, standalone):
    def probe():
        status, body = http_json("http://127.0.0.1:%s/auth/session" % port)
        return status == 200 and body.get("standalone") is standalone
    return probe


def switch(enable):
    config = settings()
    adapter, ui = inspect("vivesec-adapter"), inspect("vivesec-ui")
    if not adapter or not ui:
        raise SystemExit("vivesec-adapter and vivesec-ui must exist")
    auth = inspect(AUTH_CONTAINER)
    if enable:
        if not auth or not auth["State"]["Running"]:
            raise SystemExit("deploy the authentication service first (vivesec-auth is not running)")
        status, _ = http_json("http://127.0.0.1:%d/auth/health" % config["port"])
        if status != 200:
            raise SystemExit("vivesec-auth does not answer on port %d" % config["port"])
    auth_env = environment(auth) if auth else {}
    adapter_env, ui_env = environment(adapter), environment(ui)
    new_adapter = adapter_change(adapter_env, adapter["HostConfig"].get("Binds"), config["data_dir"],
                                 auth_env, enable)
    new_ui = ui_change(ui_env, ui["HostConfig"].get("Binds"), config["data_dir"], config["port"], enable)
    mode = "standalone" if enable else "vivesecbox"
    if (new_adapter, new_ui) == ((adapter_env, adapter["HostConfig"].get("Binds") or []),
                                 (ui_env, ui["HostConfig"].get("Binds") or [])):
        print("UNCHANGED: already in %s mode" % mode, flush=True)
        return
    stamp = time.strftime("%Y%m%d-%H%M%S")
    adapter_port, ui_port = adapter_env.get("ADAPTER_PORT", "8088"), ui_env.get("PORT", "8080")
    parked = []
    try:
        parked.append(("vivesec-adapter", recreate("vivesec-adapter", *new_adapter, stamp)))
        if not wait_until(adapter_ready(adapter_port, mode), 120):
            raise RuntimeError("the adapter did not come up in %s mode" % mode)
        print("ADAPTER_OK mode=" + mode, flush=True)
        parked.append(("vivesec-ui", recreate("vivesec-ui", *new_ui, stamp)))
        if not wait_until(ui_ready(ui_port, enable), 60):
            raise RuntimeError("the web UI did not come up in %s mode" % mode)
        print("UI_OK mode=" + mode, flush=True)
    except BaseException:
        for name, previous in reversed(parked):
            restore(name, previous)
        print("ROLLED_BACK: adapter and web UI run as before", flush=True)
        raise
    print("SWITCHED=%s (previous containers kept stopped: %s)"
          % (mode, ", ".join(previous for _name, previous in parked)), flush=True)


def status():
    config = settings()
    report = {}
    for name in ("vivesec-adapter", "vivesec-ui", AUTH_CONTAINER):
        container = inspect(name)
        if not container:
            report[name] = None
            continue
        env = environment(container)
        entry = {"running": container["State"]["Running"], "image": container["Config"]["Image"],
                 "binds": [bind for bind in container["HostConfig"].get("Binds") or []
                           if KEY_MOUNT_DIR in bind or DATA_IN_CONTAINER in bind]}
        if name != AUTH_CONTAINER:
            entry["integration"] = env.get("ADAPTER_INTEGRATION") or "vivesecbox"
        report[name] = entry
    report["auth_health"] = http_json("http://127.0.0.1:%d/auth/health" % config["port"])[0] \
        if report[AUTH_CONTAINER] else None
    print(json.dumps(report, indent=2))


def main(argv):
    if len(argv) == 3 and argv[1] == "deploy":
        deploy(argv[2])
    elif len(argv) == 2 and argv[1] in ("enable", "disable"):
        switch(argv[1] == "enable")
    elif len(argv) == 2 and argv[1] == "status":
        status()
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main(sys.argv)
