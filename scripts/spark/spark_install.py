#!/usr/bin/env python3
"""Matrix AI on a DGX Spark next to the Dokumentum AI (E07 M1).

    sudo python3 spark_install.py --list
    sudo python3 spark_install.py --check            preflight only, changes nothing
    sudo python3 spark_install.py                    every phase
    sudo python3 spark_install.py --resume           skip the phases already done
    sudo python3 spark_install.py --only tunnel      one phase
    sudo python3 spark_install.py status             what runs, read-only

Runs from an unpacked kit (pack_kit.py): <kit>/KIT.json, <kit>/ui-output.tgz and
<kit>/src/ with this file in src/scripts/spark/. A release kit (pack_release.py)
lists its images in KIT.json instead of carrying ui-output.tgz: nothing is built
on the box then, the loaded images are installed. Settings: /etc/vivesec-matrix/
matrix.conf (matrix.conf.example); MATRIX_* variables of the environment win.

The Spark is shared: the Dokumentum AI owns :80, :443 and SSDP (its ViVeSecBox
adapter), :8443, its loopback ports and the Ollama on 127.0.0.1:11434. This
installer never touches their files, units, containers or firewall table. It
uses that Ollama read-only (MATRIX_OLLAMA_URL): it neither pulls nor deletes a
model, and it remembers the embedding model's digest, so a changed embedding
model stops the installation instead of mixing vectors in one index.

Phases:
    preflight  platform, docker, nginx, shared Ollama and its models, free ports
    dirs       /var/lib/vivesec-matrix and its secrets (RAG key, tunnel forward key)
    images     vivesec-adapter, -rag, -ui built from the kit (10-build-images.sh),
               or the release kit's loaded images tagged :latest
    clamav     upload scanner (clamav_deploy.py), loopback :3310
    auth       sign-in service (auth_deploy.py) with the tunnel sign-in on
    graph      relationship analysis (E26): Neo4j (loopback) + vivesec-graph, built
               and tested from the kit; MATRIX_GRAPH=off leaves it out
    core       vivesec-rag, vivesec-adapter (standalone, loopback), vivesec-ui (loopback)
    tunnel     tunnel gateway + front (MATRIX_TUNNEL_PKI), optional dev firewall rule
    updater    signed updates (matrix_updater, MATRIX_UPDATE): its libraries from the
               kit's wheels, a check timer and the approval path unit
    verify     every service answers; the gateway refuses what it must refuse
"""
import argparse
import hashlib
import json
import os
import re
import secrets
import shlex
import shutil
import socket
import ssl
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE.parents[1]
KIT = SRC.parent

PHASES = ("preflight", "dirs", "images", "clamav", "auth", "graph", "core", "tunnel", "updater", "verify")
CONF_FILE = Path("/etc/vivesec-matrix/matrix.conf")
ROOT = Path("/var/lib/vivesec-matrix")
STATE_FILE = ROOT / "install-state"
EMBED_RECORD = ROOT / "state" / "embed-model.json"
OPT = Path("/opt/vivesec-matrix")
GRAPH_SECRETS = ROOT / "graph-secrets"
UNIT_DIR = Path("/etc/systemd/system")
FRONT_DIR = Path("/etc/vivesec-matrix/front")
FILES_PKI = Path("/etc/vivesec-matrix/tunnel-pki")
FRONT_PKI = ROOT / "front-pki"
PARTNER_PKI = Path("/run/vivetech-vivesec-adapter/pki")
PARTNER_ADAPTER_UNIT = "vivetech-vivesec-adapter.service"
PREP_TABLE = "inet vivesec_prep_v2"
FIREWALL_COMMENT = "vivesec-matrix-tunnel"

# Ports and addresses of the other product: never ours, whatever the settings say.
RESERVED_TCP = (22, 80, 443, 8443, 8098, 11000, 11434, 18083, 18084, 18085, 18086, 18200)

DEFAULTS = {
    "MATRIX_OLLAMA_URL": "http://127.0.0.1:11434",
    "MATRIX_GEN_MODEL": "qwen3.6:35b",
    "MATRIX_EMBED_MODEL": "bge-m3",
    "MATRIX_NUM_CTX": "65536",
    "MATRIX_RAG_PORT": "8090",
    "MATRIX_ADAPTER_PORT": "8088",
    "MATRIX_UI_PORT": "8080",
    "MATRIX_AUTH_PORT": "8097",
    "MATRIX_CLAMAV_PORT": "3310",
    "MATRIX_GATEWAY_PORT": "18476",
    "MATRIX_TUNNEL_PORT": "7476",
    "MATRIX_TUNNEL_PKI": "off",
    "MATRIX_TUNNEL_FIREWALL": "off",
    "MATRIX_ACCEPT_EMBED_CHANGE": "no",
    "MATRIX_GRAPH": "off",
    "MATRIX_GRAPH_PROFILE": "",
    "MATRIX_GRAPH_ADMINS": "admin",
    "MATRIX_GRAPH_PORT": "8095",
    "MATRIX_BROKER_PORT": "8099",
    "MATRIX_NEO4J_PORT": "7687",
    "MATRIX_NEO4J_IMAGE": "neo4j:5-community",
    "MATRIX_GRAPH_START_WAIT": "600",
    "MATRIX_UPDATE": "off",
    "MATRIX_UPDATE_URL": "",
    "MATRIX_UPDATE_CHANNEL": "stable",
    "MATRIX_TUNNEL_AUTOCREATE": "analyst",
    "MATRIX_TUNNEL_OWNER_ADMIN": "on",
}
PKI_MODES = ("off", "files", "partner-dev")
FIREWALL_MODES = ("off", "dropin-dev")
GRAPH_MODES = ("on", "off")
UPDATE_MODES = ("on", "off")
AUTOCREATE_MODES = ("analyst", "off")
OWNER_ADMIN_MODES = ("on", "off")
UPDATE_CHANNELS = ("stable", "pilot", "labor")
PORT_KEYS = ("MATRIX_RAG_PORT", "MATRIX_ADAPTER_PORT", "MATRIX_UI_PORT", "MATRIX_AUTH_PORT",
             "MATRIX_CLAMAV_PORT", "MATRIX_GATEWAY_PORT", "MATRIX_TUNNEL_PORT",
             "MATRIX_GRAPH_PORT", "MATRIX_BROKER_PORT", "MATRIX_NEO4J_PORT")
GRAPH_PORT_KEYS = ("MATRIX_GRAPH_PORT", "MATRIX_BROKER_PORT", "MATRIX_NEO4J_PORT")
# Who may hold each port when it is already in use: our container or our unit.
PORT_OWNERS = {"MATRIX_RAG_PORT": "vivesec-rag", "MATRIX_ADAPTER_PORT": "vivesec-adapter",
               "MATRIX_UI_PORT": "vivesec-ui", "MATRIX_AUTH_PORT": "vivesec-auth",
               "MATRIX_CLAMAV_PORT": "vivesec-clamav", "MATRIX_GATEWAY_PORT": "unit:vivesec-matrix-tunnel",
               "MATRIX_TUNNEL_PORT": "unit:vivesec-matrix-front", "MATRIX_GRAPH_PORT": "vivesec-graph",
               "MATRIX_BROKER_PORT": "vivesec-adapter", "MATRIX_NEO4J_PORT": "vivesec-neo4j"}
# matrix.conf may set adapter behaviour (ADAPTER_APP_MODE, ADAPTER_PURPOSE, ...),
# but not what the installer owns: where it listens, how identity, storage and
# the services it talks to are wired.
ADAPTER_OWNED = re.compile(
    r"^ADAPTER_(HOST|PORT|TLS[A-Z_]*|DISCOVERY[A-Z_]*|INTEGRATION|IDENTITY_[A-Z_]+|DIRECTORY|"
    r"STORAGE_MODE|CLAMD|GPU_BROKER_PORT|GRAPH_ADMINS|TENANT_ID|DRIVE_PREFIX|ACL_RULES|"
    r"[A-Z_]*_DIR|[A-Z_]*_PATH|NUM_CTX|ANALYZE_NUM_CTX|GEN_MODEL)$")
# The tunnel phase replaces the prototype, so its units may hold these ports until then.
PROTOTYPE_PORT_UNITS = {"MATRIX_GATEWAY_PORT": "matrix-stub.service", "MATRIX_TUNNEL_PORT": "matrix-front.service"}
# The first prototype of the tunnel front (dev Spark, 2026-11-21) is replaced by this installer.
PROTOTYPE_UNITS = ("matrix-pki-sync.timer", "matrix-pki-sync.service", "matrix-stub.service",
                   "matrix-front.service")
PROTOTYPE_FILES = ("/etc/systemd/system/matrix-front.service", "/etc/systemd/system/matrix-stub.service",
                   "/etc/systemd/system/matrix-pki-sync.service", "/etc/systemd/system/matrix-pki-sync.timer",
                   "/etc/systemd/system/vivesec-prep-v2-firewall.service.d/matrix-7476.conf",
                   "/etc/systemd/system/vivesec-prep-v2-refresh.service.d/matrix-7476.conf",
                   "/usr/local/sbin/matrix-pki-sync.sh", "/usr/local/sbin/matrix-7476-fw.sh")
PROTOTYPE_DIRS = ("/etc/matrix-front", "/var/lib/matrix-front", "/usr/local/lib/matrix-front",
                  "/var/log/matrix-front")


class InstallError(Exception):
    pass


def log(message):
    print("[%s] %s" % (time.strftime("%H:%M:%S"), message), flush=True)


# ---------------------------------------------------------------- settings

def parse_conf(text):
    """KEY=value lines (shell quoting allowed); comments and blanks ignored."""
    values = {}
    for number, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, raw = line.partition("=")
        key = key.strip()
        if not sep or not re.match(r"^[A-Z][A-Z0-9_]*$", key):
            raise InstallError("matrix.conf line %d is not KEY=value" % number)
        try:
            parts = shlex.split(raw, comments=True)
        except ValueError as e:
            raise InstallError("matrix.conf line %d: %s" % (number, e))
        values[key] = " ".join(parts)
    return values


def settings(conf_text="", env=None):
    """The effective settings. Unknown modes fall back to the safe one (off).
    ADAPTER_* lines of matrix.conf (never of the environment) go to the adapter."""
    env = os.environ if env is None else env
    values = dict(DEFAULTS)
    from_file = parse_conf(conf_text)
    adapter_extra = {key: value for key, value in from_file.items() if key.startswith("ADAPTER_")}
    owned = sorted(key for key in adapter_extra if ADAPTER_OWNED.match(key))
    if owned:
        raise InstallError("matrix.conf may not set %s: the installer wires these" % ", ".join(owned))
    values.update({key: value for key, value in from_file.items() if key.startswith("MATRIX_")})
    values.update({key: value for key, value in env.items() if key.startswith("MATRIX_")})
    values["ADAPTER_EXTRA"] = adapter_extra
    warnings = []
    for key, allowed in (("MATRIX_TUNNEL_PKI", PKI_MODES), ("MATRIX_TUNNEL_FIREWALL", FIREWALL_MODES),
                         ("MATRIX_GRAPH", GRAPH_MODES), ("MATRIX_UPDATE", UPDATE_MODES),
                         ("MATRIX_TUNNEL_AUTOCREATE", AUTOCREATE_MODES),
                         ("MATRIX_TUNNEL_OWNER_ADMIN", OWNER_ADMIN_MODES)):
        values[key] = values[key].strip().lower()
        if values[key] not in allowed:
            warnings.append("unknown %s=%r -> off" % (key, values[key]))
            values[key] = "off"
    channel = values["MATRIX_UPDATE_CHANNEL"].strip().lower()
    values["MATRIX_UPDATE_URL"] = values["MATRIX_UPDATE_URL"].strip().rstrip("/")
    if channel not in UPDATE_CHANNELS:
        warnings.append("unknown MATRIX_UPDATE_CHANNEL=%r -> updates off" % channel)
        values["MATRIX_UPDATE"] = "off"
    values["MATRIX_UPDATE_CHANNEL"] = channel
    if values["MATRIX_UPDATE"] == "on" and not re.match(
            r"^https://[A-Za-z0-9.-]+(:\d+)?(/[A-Za-z0-9._~/-]*)?$", values["MATRIX_UPDATE_URL"]):
        raise InstallError("MATRIX_UPDATE=on needs MATRIX_UPDATE_URL, an https:// URL")
    ports = {}
    for key in PORT_KEYS:
        raw = values[key].strip()
        if not raw.isdigit() or not 1024 <= int(raw) <= 65535:
            raise InstallError("%s=%r must be a port number 1024..65535" % (key, raw))
        if int(raw) in RESERVED_TCP:
            raise InstallError("%s=%s belongs to the Dokumentum AI / the system" % (key, raw))
        if int(raw) in ports.values():
            raise InstallError("%s=%s is used twice" % (key, raw))
        ports[key] = int(raw)
    values.update(ports)
    if not values["MATRIX_NUM_CTX"].isdigit():
        raise InstallError("MATRIX_NUM_CTX must be a number")
    if not values["MATRIX_GRAPH_START_WAIT"].isdigit():
        raise InstallError("MATRIX_GRAPH_START_WAIT must be a number of seconds")
    values["MATRIX_GRAPH_START_WAIT"] = int(values["MATRIX_GRAPH_START_WAIT"])
    admins = [name.strip() for name in values["MATRIX_GRAPH_ADMINS"].split(",") if name.strip()]
    if not admins or not all(re.match(r"^[a-z0-9][a-z0-9._@-]{0,63}$", name) for name in admins):
        raise InstallError("MATRIX_GRAPH_ADMINS must name one or more users")
    values["MATRIX_GRAPH_ADMINS"] = ",".join(admins)
    if values["MATRIX_GRAPH_PROFILE"] and not re.match(r"^[a-z0-9_-]{1,32}$", values["MATRIX_GRAPH_PROFILE"]):
        raise InstallError("MATRIX_GRAPH_PROFILE must be a profile name")
    url = values["MATRIX_OLLAMA_URL"].rstrip("/")
    if not re.match(r"^http://(127\.\d+\.\d+\.\d+|localhost):\d+$", url):
        raise InstallError("MATRIX_OLLAMA_URL must be a loopback http:// URL")
    values["MATRIX_OLLAMA_URL"] = url
    values["MATRIX_ACCEPT_EMBED_CHANGE"] = values["MATRIX_ACCEPT_EMBED_CHANGE"].strip().lower() == "yes"
    return values, warnings


def load_settings():
    text = CONF_FILE.read_text(encoding="utf-8") if CONF_FILE.is_file() else ""
    values, warnings = settings(text)
    for warning in warnings:
        log("WARNING " + warning)
    return values


# ---------------------------------------------------------------- helpers

def run(*args, check=True, capture=True, env=None, input_text=None):
    result = subprocess.run(args, text=True, capture_output=capture, env=env, input=input_text)
    if check and result.returncode:
        detail = (result.stderr or result.stdout or "")[-2500:] if capture else ""
        raise InstallError("%s failed (%d)\n%s" % (" ".join(args[:3]), result.returncode, detail))
    return result


def http_status(url, timeout=5, method="GET", headers=None, body=None):
    request = urllib.request.Request(url, method=method, headers=headers or {}, data=body)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except (OSError, ValueError):
        return 0, b""


def wait_for(probe, seconds, what):
    deadline = time.time() + seconds
    while time.time() < deadline:
        if probe():
            return
        time.sleep(2)
    raise InstallError("%s did not come up within %d s" % (what, seconds))


def write_file(path, text, mode=0o644, owner=None):
    """Replace `path` atomically. Some folders here are written by containers
    (adapter, auth) as well, so nothing in them is followed: the temporary file
    gets a fresh name (O_EXCL, O_NOFOLLOW), its mode and owner are set on the
    descriptor, and the rename replaces a link instead of writing through it."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(".%s.%s.tmp" % (path.name, secrets.token_hex(8)))
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), mode)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            if hasattr(os, "fchmod"):
                os.fchmod(handle.fileno(), mode)
                if owner is not None:
                    os.fchown(handle.fileno(), *owner)
            handle.write(text)
        os.replace(temp, path)
    except BaseException:
        Path(temp).unlink(missing_ok=True)
        raise


def ensure_key(path, owner=None):
    """A 64-hex-character secret, created once, readable by its owner only.
    A link or anything but a regular file in its place is replaced, never followed."""
    path = Path(path)
    try:
        info = os.lstat(path)
        usable = stat.S_ISREG(info.st_mode) and info.st_size >= 32
    except FileNotFoundError:
        usable = False
    if usable:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            if len(os.read(fd, 4096).strip()) < 32:
                usable = False
            elif hasattr(os, "fchmod"):
                if owner is not None:
                    os.fchown(fd, *owner)
                os.fchmod(fd, 0o600)
        finally:
            os.close(fd)
    if not usable:
        write_file(path, secrets.token_hex(32) + "\n", 0o600, owner)


def listening_ports():
    output = run("ss", "-Hltn").stdout
    ports = set()
    for line in output.splitlines():
        fields = line.split()
        if len(fields) >= 4 and ":" in fields[3]:
            port = fields[3].rsplit(":", 1)[1]
            if port.isdigit():
                ports.add(int(port))
    return ports


def container_running(name):
    result = run("docker", "container", "inspect", "-f", "{{.State.Running}}", name, check=False)
    return result.returncode == 0 and result.stdout.strip() == "true"


def unit_active(unit):
    return run("systemctl", "is-active", "--quiet", unit, check=False).returncode == 0


def ollama_models(url):
    status, body = http_status(url + "/api/tags", timeout=10)
    if status != 200:
        raise InstallError("the shared Ollama at %s does not answer (%s)" % (url, status or "no connection"))
    return {model["name"]: model.get("digest", "") for model in json.loads(body).get("models", [])}


def model_digest(models, name):
    for candidate in (name, name + ":latest"):
        if candidate in models:
            return candidate, models[candidate]
    return None, None


def kit_info():
    return json.loads((KIT / "KIT.json").read_text(encoding="utf-8"))


def kit_images(info):
    """{name: image id} of a release kit that brings built images; {} when this kit builds on the box."""
    images = {}
    for entry in info.get("images") or []:
        name, image_id = entry.get("name", ""), entry.get("id", "")
        if not re.match(r"^vivesec-[a-z0-9-]+$", name) or not re.match(r"^sha256:[0-9a-f]{64}$", image_id):
            raise InstallError("KIT.json lists a bad image %r" % entry)
        images[name] = image_id
    return images


def image_present(image_id):
    result = run("docker", "image", "inspect", "-f", "{{.Id}}", image_id, check=False)
    return result.returncode == 0 and result.stdout.strip() == image_id


# ---------------------------------------------------------------- phases

def phase_preflight(conf):
    problems = []
    machine = os.uname().machine
    if machine != "aarch64":
        problems.append("this kit is for the DGX Spark (aarch64), not %s" % machine)
    if not Path("/etc/dgx-release").is_file():
        problems.append("/etc/dgx-release missing: not a DGX OS")
    for tool in ("docker", "nginx", "nft", "ss", "openssl", "systemctl"):
        if shutil.which(tool) is None and not Path("/usr/sbin/" + tool).exists():
            problems.append("%s is not installed" % tool)
    for item in ("KIT.json",):
        if not (KIT / item).is_file():
            problems.append("kit file %s missing next to src/" % item)
    images = kit_images(kit_info()) if (KIT / "KIT.json").is_file() else {}
    if not images and not (KIT / "ui-output.tgz").is_file():
        problems.append("kit file ui-output.tgz missing next to src/")
    if run("docker", "info", check=False).returncode:
        problems.append("docker does not answer")
    else:
        missing = sorted(name for name, image_id in images.items() if not image_present(image_id))
        if missing:
            problems.append("release images not loaded: %s (the updater loads them, or docker load)"
                            % ", ".join(missing))
        if conf["MATRIX_GRAPH"] == "on" and images and "vivesec-graph" not in images:
            problems.append("MATRIX_GRAPH=on but this release kit has no vivesec-graph image")
    try:
        models = ollama_models(conf["MATRIX_OLLAMA_URL"])
    except InstallError as e:
        problems.append(str(e))
        models = {}
    for key in ("MATRIX_GEN_MODEL", "MATRIX_EMBED_MODEL"):
        if models and model_digest(models, conf[key])[0] is None:
            problems.append("the shared Ollama has no %s (%s); this kit does not pull models"
                            % (conf[key], key))
    name, digest = model_digest(models, conf["MATRIX_EMBED_MODEL"])
    if digest and EMBED_RECORD.is_file():
        recorded = json.loads(EMBED_RECORD.read_text(encoding="utf-8"))
        if recorded.get("digest") != digest and not conf["MATRIX_ACCEPT_EMBED_CHANGE"]:
            problems.append("the embedding model %s changed (%s -> %s): the index was built with the old one; "
                            "reindex first, then set MATRIX_ACCEPT_EMBED_CHANGE=yes once"
                            % (name, recorded.get("digest", "?")[:12], digest[:12]))
    busy = listening_ports()
    for key in PORT_KEYS:
        if key == "MATRIX_TUNNEL_PORT" and conf["MATRIX_TUNNEL_PKI"] == "off":
            continue
        if key in GRAPH_PORT_KEYS and conf["MATRIX_GRAPH"] == "off":
            continue
        port, owner = conf[key], PORT_OWNERS[key]
        if port not in busy:
            continue
        ours = unit_active(owner[5:] + ".service") if owner.startswith("unit:") else container_running(owner)
        if not ours and key in PROTOTYPE_PORT_UNITS:
            ours = unit_active(PROTOTYPE_PORT_UNITS[key])
        if not ours:
            problems.append("port %d (%s) is taken by something that is not %s" % (port, key, owner))
    if conf["MATRIX_TUNNEL_PKI"] == "files":
        for name in ("server.crt", "server.key", "ca.crt"):
            if not (FILES_PKI / name).is_file():
                problems.append("MATRIX_TUNNEL_PKI=files needs %s" % (FILES_PKI / name))
    if conf["MATRIX_TUNNEL_PKI"] == "partner-dev":
        log("WARNING MATRIX_TUNNEL_PKI=partner-dev reuses the Dokumentum AI pairing key: development box only")
    if conf["MATRIX_TUNNEL_FIREWALL"] == "dropin-dev":
        if run("nft", "list", "table", *PREP_TABLE.split(), check=False).returncode:
            problems.append("MATRIX_TUNNEL_FIREWALL=dropin-dev needs the %s table" % PREP_TABLE)
    if problems:
        raise InstallError("preflight:\n  - " + "\n  - ".join(problems))
    log("PREFLIGHT_OK kit=%s ollama=%s" % (json.loads((KIT / "KIT.json").read_text()).get("label"),
                                           conf["MATRIX_OLLAMA_URL"]))


def phase_dirs(conf):
    for path, mode in ((ROOT, 0o711), (ROOT / "secrets", 0o700), (ROOT / "adapter", 0o700),
                       (ROOT / "rag", 0o700), (ROOT / "state", 0o700), (ROOT / "build", 0o700),
                       (OPT, 0o755)):
        path.mkdir(parents=True, exist_ok=True)
        os.chmod(path, mode)
    ensure_key(ROOT / "secrets" / "rag-api-key")
    ensure_key(ROOT / "secrets" / "tunnel-forward.key")
    models = ollama_models(conf["MATRIX_OLLAMA_URL"])
    name, digest = model_digest(models, conf["MATRIX_EMBED_MODEL"])
    write_file(EMBED_RECORD, json.dumps({"model": name, "digest": digest,
                                         "recorded": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}) + "\n",
               0o600)
    log("DIRS_OK %s (embedding model %s %s)" % (ROOT, name, digest[:12]))


def phase_images(conf):
    images = kit_images(kit_info())
    if images:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        for name, image_id in sorted(images.items()):
            if not image_present(image_id):
                raise InstallError("release image %s (%s) is not loaded" % (name, image_id[:19]))
            # The image :latest named before stays one docker tag away, as after a build.
            old = run("docker", "image", "inspect", "-f", "{{.Id}}", name + ":latest", check=False)
            if old.returncode == 0 and old.stdout.strip() != image_id:
                run("docker", "tag", name + ":latest", "%s:prev-%s" % (name, stamp))
            run("docker", "tag", image_id, name + ":latest")
        log("IMAGES_OK prebuilt %s" % " ".join(sorted(images)))
        return
    build = ROOT / "build"
    env = dict(os.environ, SOURCE_DIR=str(SRC), UI_BUNDLE=str(KIT / "ui-output.tgz"),
               UI_STAGE=str(build / "ui-src"), RAG_STAGE=str(build / "rag-src"))
    run("bash", str(SRC / "scripts/jetson/install/10-build-images.sh"), env=env, capture=False)
    log("IMAGES_OK")


def phase_clamav(conf):
    env = dict(os.environ, AIBOX_CLAMAV_PORT=str(conf["MATRIX_CLAMAV_PORT"]),
               AIBOX_CLAMAV_DB_DIR=str(ROOT / "clamav"))
    if "vivesec-clamav" in kit_images(kit_info()):
        env["AIBOX_CLAMAV_IMAGE"] = "vivesec-clamav:latest"
    run(sys.executable, str(SRC / "scripts/jetson/standalone/clamav_deploy.py"), "deploy", env=env, capture=False)
    log("CLAMAV_OK")


AUTH_UID = (10001, 10001)


def phase_auth(conf):
    auth_dir = ROOT / "auth"
    auth_dir.mkdir(exist_ok=True)
    os.chown(auth_dir, *AUTH_UID)
    os.chmod(auth_dir, 0o700)
    # Created before the service starts, so the tunnel sign-in is on from the first start.
    ensure_key(auth_dir / "tunnel.key", AUTH_UID)
    env = dict(os.environ, AIBOX_AUTH_DIR=str(auth_dir), AIBOX_AUTH_PORT=str(conf["MATRIX_AUTH_PORT"]),
               AUTH_TUNNEL_KEY_FILE="/data/auth/tunnel.key",
               # A ViVeSecBox user's first visit creates its own analyst without rooms.
               AUTH_TUNNEL_AUTOCREATE=conf["MATRIX_TUNNEL_AUTOCREATE"],
               # The ViVeSecBox owner (VVS-Permissions: owner) is the administrator.
               AUTH_TUNNEL_OWNER_ADMIN=conf["MATRIX_TUNNEL_OWNER_ADMIN"])
    if "vivesec-auth" in kit_images(kit_info()):
        env["AIBOX_AUTH_IMAGE"] = "vivesec-auth:latest"
    run(sys.executable, str(SRC / "scripts/jetson/standalone/auth_deploy.py"), "deploy", str(SRC),
        env=env, capture=False)
    forget_old_probe()
    log("AUTH_OK")


AUTH_MANAGE = ("docker", "exec", "vivesec-auth", "python", "/app/auth_service/manage.py")
INSTALL_PROBE = "matrix install probe"
# Until 26.10.1.5 the verify phase used this valid VVS-User; with autocreate on it
# got a user of its own (vvs-<10 hex of its sha256>), which an administrator may
# even have given rooms, so that every later verify failed.
OLD_PROBE = "matrix-install-probe"


def forget_old_probe():
    shown = run(*AUTH_MANAGE, "tunnel-map", "show", check=False)
    try:
        users = json.loads(shown.stdout or "{}").get("users") or {}
    except ValueError:
        return
    name = users.get(OLD_PROBE)
    if not name:
        return
    run(*AUTH_MANAGE, "tunnel-map", "remove", OLD_PROBE)
    if name == "vvs-" + hashlib.sha256(OLD_PROBE.encode("utf-8")).hexdigest()[:10]:
        run(*AUTH_MANAGE, "delete-user", name, check=False)
    log("removed the user %s of the old install probe" % name)


def adapter_args(conf, rag_key):
    data = ROOT / "adapter"
    auth = ROOT / "auth"
    env = {
        "ADAPTER_HOST": "127.0.0.1", "ADAPTER_PORT": str(conf["MATRIX_ADAPTER_PORT"]),
        "ADAPTER_TLS": "off", "ADAPTER_DISCOVERY": "off", "ADAPTER_STORAGE_MODE": "off",
        "ADAPTER_INTEGRATION": "standalone", "ADAPTER_IDENTITY_MODE": "require",
        "ADAPTER_DIRECTORY": "token", "ADAPTER_IDENTITY_ALGORITHMS": "HS256",
        "ADAPTER_IDENTITY_HS256_SECRET_FILE": "/run/aibox-auth/identity-hs256.key",
        "ADAPTER_IDENTITY_ISSUER": "vivesec-aibox-auth", "ADAPTER_IDENTITY_AUDIENCE": "vivesec-aibox-adapter",
        "ADAPTER_TENANT_ID": "default", "ADAPTER_DRIVE_PREFIX": "/storage/drives",
        "ADAPTER_META_PATH": "/data/meta.json", "ADAPTER_LIFECYCLE_PATH": "/data/lifecycle.json",
        "ADAPTER_PKI_DIR": "/data/pki", "ADAPTER_FILES_DIR": "/data/generated",
        "ADAPTER_SESSION_DIR": "/data/sessions", "ADAPTER_JOBS_DIR": "/data/jobs",
        "ADAPTER_FEEDBACK_DIR": "/data/feedback", "ADAPTER_STORE_DIR": "/data/files",
        "ADAPTER_TEMPLATES_DIR": "/data/templates", "ADAPTER_ACL_RULES": "/data/acl-rules.json",
        "ADAPTER_QUERY_LOG_DIR": "/data/querylog", "ADAPTER_UPDATES_DIR": "/data/updates",
        "ADAPTER_CLAMD": "127.0.0.1:%d" % conf["MATRIX_CLAMAV_PORT"],
        "ADAPTER_CHAT_POLICY": "locked_hybrid", "ADAPTER_GEN_MODEL": conf["MATRIX_GEN_MODEL"],
        "ADAPTER_GENERATE": "auto", "ADAPTER_THINK": "off", "ADAPTER_NUM_PREDICT": "2048",
        # The shared Ollama keeps one context size loaded; asking for another reloads the model.
        "ADAPTER_NUM_CTX": conf["MATRIX_NUM_CTX"], "ADAPTER_ANALYZE_NUM_CTX": conf["MATRIX_NUM_CTX"],
        "ADAPTER_ANALYZE_MAX_CONTEXT_TOKENS": "60000", "ADAPTER_ANALYZE_MAX_CHARS": "100000",
        "RAG_URL": "http://127.0.0.1:%d" % conf["MATRIX_RAG_PORT"], "RAG_API_KEY": rag_key,
        "OLLAMA_URL": conf["MATRIX_OLLAMA_URL"],
    }
    env.update(conf["ADAPTER_EXTRA"])
    if conf["MATRIX_GRAPH"] == "on":
        if not (GRAPH_SECRETS / "graph_api_key").is_file():
            raise InstallError("MATRIX_GRAPH=on but the graph phase has not run yet (--only graph)")
        env.update({"GRAPH_URL": "http://127.0.0.1:%d" % conf["MATRIX_GRAPH_PORT"],
                    "GRAPH_API_KEY": (GRAPH_SECRETS / "graph_api_key").read_text().strip(),
                    "ADAPTER_GPU_BROKER_PORT": str(conf["MATRIX_BROKER_PORT"]),
                    "ADAPTER_GRAPH_ADMINS": conf["MATRIX_GRAPH_ADMINS"]})
    args = ["-v", "%s:/data" % data, "-v", "%s/identity-hs256.key:/run/aibox-auth/identity-hs256.key:ro" % auth]
    for key, value in sorted(env.items()):
        args += ["-e", "%s=%s" % (key, value)]
    return args


def rag_args(conf, rag_key):
    env = {"RAG_HOST": "127.0.0.1", "RAG_PORT": str(conf["MATRIX_RAG_PORT"]), "RAG_STORE_BACKEND": "sqlite",
           "RAG_INDEX_PATH": "/data/rag/index.db", "RAG_DEFAULT_TENANT": "default", "RAG_API_KEY": rag_key,
           "OLLAMA_URL": conf["MATRIX_OLLAMA_URL"], "VIVESEC_BACKEND": "ollama",
           "VIVESEC_EMBED_MODEL": conf["MATRIX_EMBED_MODEL"]}
    args = ["-v", "%s:/data/rag" % (ROOT / "rag")]
    for key, value in sorted(env.items()):
        args += ["-e", "%s=%s" % (key, value)]
    return args


def ui_args(conf):
    env = {"PORT": str(conf["MATRIX_UI_PORT"]), "HOST": "127.0.0.1", "NITRO_HOST": "127.0.0.1",
           "ADAPTER_URL": "http://127.0.0.1:%d" % conf["MATRIX_ADAPTER_PORT"],
           "ADAPTER_INTEGRATION": "standalone", "AUTH_URL": "http://127.0.0.1:%d" % conf["MATRIX_AUTH_PORT"],
           "AUTH_SERVICE_KEY_FILE": "/run/aibox-auth/service.key"}
    args = ["-v", "%s/service.key:/run/aibox-auth/service.key:ro" % (ROOT / "auth")]
    for key, value in sorted(env.items()):
        args += ["-e", "%s=%s" % (key, value)]
    return args


def replace_container(name, image, args, ready_url, wait=120):
    """Start `name` anew; the running one is parked and comes back if the new one fails."""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    parked = None
    if run("docker", "container", "inspect", name, check=False).returncode == 0:
        parked = "%s-prev-%s" % (name, stamp)
        run("docker", "rename", name, parked)
        run("docker", "stop", "-t", "30", parked, check=False)
    try:
        run("docker", "run", "-d", "--name", name, "--network", "host", "--restart", "unless-stopped",
            *args, image)
        wait_for(lambda: http_status(ready_url)[0] == 200, wait, name)
    except BaseException:
        logs = run("docker", "logs", "--tail", "30", name, check=False)
        run("docker", "rm", "-f", name, check=False)
        if parked:
            run("docker", "rename", parked, name)
            run("docker", "start", name, check=False)
            log("ROLLED_BACK %s" % name)
        raise InstallError("%s did not start:\n%s" % (name, (logs.stdout + logs.stderr)[-2000:]))
    if parked:
        run("docker", "rm", parked, check=False)


def build_graph_image():
    """Build vivesec-graph from the kit and run its tests inside the image (a kit without images)."""
    label = kit_info().get("label", "kit")
    image = "vivesec-graph:" + re.sub(r"[^A-Za-z0-9_.-]", "_", label)
    run("docker", "build", "-q", "-f", str(SRC / "graph_service" / "Dockerfile"), "-t", image, str(SRC))
    tests = run("docker", "run", "--rm", "--network", "none", "-e", "VIVESEC_BACKEND=fallback",
                "--entrypoint", "sh", image, "-c",
                'cd /app/graph_service && python -m unittest discover -p "*_test.py" 2>&1', check=False)
    if tests.returncode:
        raise InstallError("graph tests failed in %s; nothing changed:\n%s" % (image, tests.stdout[-2000:]))
    log("GRAPH_TESTS_OK " + (tests.stdout.strip().splitlines() or [""])[-1])
    run("docker", "tag", image, "vivesec-graph:latest")
    return image


def phase_graph(conf):
    if conf["MATRIX_GRAPH"] == "off":
        log("GRAPH_SKIPPED (MATRIX_GRAPH=off)")
        return
    GRAPH_SECRETS.mkdir(parents=True, exist_ok=True)
    os.chmod(GRAPH_SECRETS, 0o700)
    for name, size in (("neo4j_password", 24), ("graph_api_key", 24), ("person_key_salt", 32)):
        path = GRAPH_SECRETS / name
        if not path.is_file() or not path.read_text().strip():
            # The person-key salt must stay with the graph data: a new one no
            # longer matches the stored keys of structured records.
            write_file(path, secrets.token_hex(size) + "\n", 0o644)
        # Readable inside the containers (other users); the 0700 directory keeps them private.
        os.chmod(path, 0o644)
    password = (GRAPH_SECRETS / "neo4j_password").read_text().strip()
    write_file(GRAPH_SECRETS / "neo4j_auth", "neo4j/%s" % password, 0o644)
    data = ROOT / "neo4j"
    for sub in ("data", "logs"):
        (data / sub).mkdir(parents=True, exist_ok=True)
    (ROOT / "graph").mkdir(exist_ok=True)

    images = kit_images(kit_info())
    neo4j_image = "vivesec-neo4j:latest" if "vivesec-neo4j" in images else conf["MATRIX_NEO4J_IMAGE"]
    if run("docker", "container", "inspect", "vivesec-neo4j", check=False).returncode == 0:
        run("docker", "start", "vivesec-neo4j")
        log("NEO4J kept (%s)" % run("docker", "inspect", "-f", "{{.Config.Image}}", "vivesec-neo4j").stdout.strip())
    else:
        if "vivesec-neo4j" not in images:
            run("docker", "pull", "-q", neo4j_image)
        run("docker", "run", "-d", "--name", "vivesec-neo4j", "--restart", "unless-stopped",
            "-p", "127.0.0.1:%d:7687" % conf["MATRIX_NEO4J_PORT"],
            "-v", "%s:/data" % (data / "data"), "-v", "%s:/logs" % (data / "logs"),
            "-v", "%s:/run/secrets/neo4j_auth:ro" % (GRAPH_SECRETS / "neo4j_auth"),
            "-e", "NEO4J_AUTH_FILE=/run/secrets/neo4j_auth",
            "-e", "NEO4J_server_memory_heap_initial__size=2g", "-e", "NEO4J_server_memory_heap_max__size=2g",
            "-e", "NEO4J_server_memory_pagecache_size=1g", neo4j_image)
    def neo4j_started():
        logs = run("docker", "logs", "vivesec-neo4j", check=False)
        return "Started." in logs.stdout + logs.stderr

    wait_for(neo4j_started, 180, "vivesec-neo4j")

    if "vivesec-graph" in images:
        # Built and tested when the release was packed; the images phase tagged it.
        image = "vivesec-graph:latest"
    else:
        image = build_graph_image()
    env = {"GRAPH_HOST": "127.0.0.1", "GRAPH_PORT": str(conf["MATRIX_GRAPH_PORT"]),
           "GRAPH_API_KEY": (GRAPH_SECRETS / "graph_api_key").read_text().strip(),
           "GRAPH_STORE": "neo4j", "GRAPH_NEO4J_URI": "bolt://127.0.0.1:%d" % conf["MATRIX_NEO4J_PORT"],
           "GRAPH_NEO4J_PASSWORD_FILE": "/run/secrets/neo4j_password",
           "GRAPH_PERSON_KEY_SALT_FILE": "/run/secrets/person_key_salt",
           "GRAPH_PROFILE_PATH": "/data/graph/profile.json", "GRAPH_REVIEW_PATH": "/data/graph/review.jsonl",
           "GRAPH_INGEST": "on", "GRAPH_LLM_URL": "http://127.0.0.1:%d" % conf["MATRIX_BROKER_PORT"],
           "GRAPH_LLM_MODEL": conf["MATRIX_GEN_MODEL"], "GRAPH_LLM_NUM_CTX": conf["MATRIX_NUM_CTX"]}
    if conf["MATRIX_GRAPH_PROFILE"]:
        env["GRAPH_PROFILE"] = conf["MATRIX_GRAPH_PROFILE"]
    args = ["-v", "%s:/data/graph" % (ROOT / "graph"),
            "-v", "%s:/run/secrets/neo4j_password:ro" % (GRAPH_SECRETS / "neo4j_password"),
            "-v", "%s:/run/secrets/person_key_salt:ro" % (GRAPH_SECRETS / "person_key_salt")]
    for key, value in sorted(env.items()):
        args += ["-e", "%s=%s" % (key, value)]
    replace_container("vivesec-graph", "vivesec-graph:latest", args,
                      "http://127.0.0.1:%d/health" % conf["MATRIX_GRAPH_PORT"], conf["MATRIX_GRAPH_START_WAIT"])
    log("GRAPH_OK %s graph=:%d neo4j=127.0.0.1:%d profile=%s" % (image, conf["MATRIX_GRAPH_PORT"],
                                                               conf["MATRIX_NEO4J_PORT"],
                                                               conf["MATRIX_GRAPH_PROFILE"] or "default"))


def phase_core(conf):
    rag_key = (ROOT / "secrets" / "rag-api-key").read_text().strip()
    (ROOT / "rag").mkdir(exist_ok=True)
    # As auth_deploy.py seeds it: an empty rule set the admin page can edit.
    rules = ROOT / "adapter" / "acl-rules.json"
    # The adapter writes this folder; a link here is replaced, not written through.
    if rules.is_symlink() or not rules.exists():
        write_file(rules, "{}\n", 0o600)
    replace_container("vivesec-rag", "vivesec-rag:latest", rag_args(conf, rag_key),
                      "http://127.0.0.1:%d/health" % conf["MATRIX_RAG_PORT"])
    replace_container("vivesec-adapter", "vivesec-adapter:latest", adapter_args(conf, rag_key),
                      "http://127.0.0.1:%d/api/v1/version" % conf["MATRIX_ADAPTER_PORT"])
    replace_container("vivesec-ui", "vivesec-ui:latest", ui_args(conf),
                      "http://127.0.0.1:%d/login" % conf["MATRIX_UI_PORT"])
    log("CORE_OK rag=:%d adapter=:%d ui=127.0.0.1:%d" % (conf["MATRIX_RAG_PORT"], conf["MATRIX_ADAPTER_PORT"],
                                                         conf["MATRIX_UI_PORT"]))


# ---------------------------------------------------------------- tunnel

def gateway_unit(conf):
    auth = ROOT / "auth"
    return """[Unit]
Description=Matrix AI tunnel gateway (ViVeSecBox tunnel -> standalone adapter)
After=docker.service network-online.target

[Service]
DynamicUser=yes
LoadCredential=forward.key:{secrets}/tunnel-forward.key
LoadCredential=service.key:{auth}/service.key
LoadCredential=tunnel.key:{auth}/tunnel.key
Environment=MATRIX_TUNNEL_PORT={gateway} MATRIX_ADAPTER_URL=http://127.0.0.1:{adapter} MATRIX_AUTH_URL=http://127.0.0.1:{auth_port}
Environment=MATRIX_TUNNEL_FORWARD_KEY_FILE=%d/forward.key AUTH_SERVICE_KEY_FILE=%d/service.key AUTH_TUNNEL_KEY_FILE=%d/tunnel.key
Environment=MATRIX_TUNNEL_APP_DIR={opt}/app
ExecStart=/usr/bin/python3 -I -B {opt}/matrix_tunnel/gateway.py
Restart=on-failure
RestartSec=3
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
PrivateDevices=yes
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX

[Install]
WantedBy=multi-user.target
""".format(secrets=ROOT / "secrets", auth=auth, gateway=conf["MATRIX_GATEWAY_PORT"],
           adapter=conf["MATRIX_ADAPTER_PORT"], auth_port=conf["MATRIX_AUTH_PORT"], opt=OPT)


def front_conf(conf, forward_key):
    return """# Matrix AI tunnel front: written by spark_install.py, holds the forward key (root 0600).
user www-data;
worker_processes 2;
pid /run/vivesec-matrix-front/nginx.pid;
error_log /var/log/vivesec-matrix-front/error.log warn;
events {{ worker_connections 256; }}
http {{
    access_log /var/log/vivesec-matrix-front/access.log;
    client_body_temp_path /run/vivesec-matrix-front/body;
    proxy_temp_path /run/vivesec-matrix-front/proxy;
    fastcgi_temp_path /run/vivesec-matrix-front/fastcgi;
    uwsgi_temp_path /run/vivesec-matrix-front/uwsgi;
    scgi_temp_path /run/vivesec-matrix-front/scgi;
    server_tokens off;
    server {{
        listen {port} ssl;
        server_name _;
        ssl_certificate {pki}/server.crt;
        ssl_certificate_key {pki}/server.key;
        ssl_client_certificate {pki}/ca.crt;
        ssl_verify_client on;
        ssl_verify_depth 2;
        ssl_protocols TLSv1.2 TLSv1.3;
        client_max_body_size 64m;
        location ^~ /api/v1/matrix/ {{
            proxy_pass http://127.0.0.1:{gateway};
            proxy_http_version 1.1;
            proxy_buffering off;
            proxy_read_timeout 330s;
            proxy_send_timeout 330s;
            # Set here only; whatever a client sends under these names is replaced.
            proxy_set_header X-Matrix-Forward-Key "{key}";
            proxy_set_header X-Matrix-Client-Verify $ssl_client_verify;
            proxy_set_header X-Matrix-Peer $remote_addr;
            proxy_set_header X-Forwarded-For "";
            proxy_set_header Connection "";
        }}
        location / {{ return 404; }}
    }}
}}
""".format(port=conf["MATRIX_TUNNEL_PORT"], pki=FRONT_PKI, gateway=conf["MATRIX_GATEWAY_PORT"], key=forward_key)


FRONT_UNIT = """[Unit]
Description=Matrix AI tunnel front (mutual TLS towards the ViVeSecBox)
After=network-online.target vivesec-matrix-tunnel.service
ConditionPathExists={pki}/server.crt

[Service]
Type=forking
PIDFile=/run/vivesec-matrix-front/nginx.pid
RuntimeDirectory=vivesec-matrix-front
LogsDirectory=vivesec-matrix-front
ExecStartPre=/usr/sbin/nginx -t -q -p /run/vivesec-matrix-front -c {conf}
ExecStart=/usr/sbin/nginx -p /run/vivesec-matrix-front -c {conf}
ExecReload=/usr/sbin/nginx -p /run/vivesec-matrix-front -c {conf} -s reload
ExecStop=/usr/sbin/nginx -p /run/vivesec-matrix-front -c {conf} -s quit

[Install]
WantedBy=multi-user.target
""".format(pki=FRONT_PKI, conf=FRONT_DIR / "nginx.conf")

# partner-dev: mirrors the Dokumentum AI pairing PKI and fails closed when it goes away.
PKI_SYNC = """#!/bin/sh
# Written by spark_install.py (MATRIX_TUNNEL_PKI=partner-dev): development Spark only.
SRC={src}
DST={dst}
FILES="server.crt server.key ca.crt"
drop() {{
  if [ -e "$DST/server.crt" ]; then logger -t vivesec-matrix-pki "$1: copy removed, front stopped"; fi
  systemctl stop vivesec-matrix-front.service 2>/dev/null
  rm -f "$DST"/server.crt "$DST"/server.key "$DST"/ca.crt
  exit 0
}}
systemctl is-active --quiet {partner} || drop "pairing adapter not running"
for f in $FILES; do [ -s "$SRC/$f" ] || drop "source $f missing"; done
a=$(openssl x509 -in "$SRC/server.crt" -noout -pubkey 2>/dev/null | sha256sum)
b=$(openssl pkey -in "$SRC/server.key" -pubout 2>/dev/null | sha256sum)
[ -n "$a" ] && [ "$a" = "$b" ] || drop "certificate and key do not match"
openssl verify -CAfile "$SRC/ca.crt" "$SRC/server.crt" >/dev/null 2>&1 || drop "server.crt not signed by ca.crt"
changed=0
for f in $FILES; do cmp -s "$SRC/$f" "$DST/$f" || changed=1; done
if [ $changed = 1 ]; then
  mkdir -p "$DST"; chmod 700 "$DST"
  for f in $FILES; do install -m 600 "$SRC/$f" "$DST/.$f.new" && mv -f "$DST/.$f.new" "$DST/$f"; done
  logger -t vivesec-matrix-pki "pairing PKI copied"
  systemctl is-active --quiet vivesec-matrix-front.service && systemctl reload vivesec-matrix-front.service
fi
systemctl is-active --quiet vivesec-matrix-front.service || systemctl start vivesec-matrix-front.service
""".format(src=PARTNER_PKI, dst=FRONT_PKI, partner=PARTNER_ADAPTER_UNIT)

PKI_SYNC_UNITS = {
    "vivesec-matrix-pki-sync.service": """[Unit]
Description=Matrix AI dev: mirror the Dokumentum AI pairing PKI (fails closed)

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/vivesec-matrix-pki-sync
""",
    "vivesec-matrix-pki-sync.timer": """[Unit]
Description=Matrix AI dev: check the pairing PKI every 15 s

[Timer]
OnBootSec=20s
OnUnitActiveSec=15s
AccuracySec=2s

[Install]
WantedBy=timers.target
""",
}

FIREWALL_HELPER = """#!/bin/sh
# Written by spark_install.py (MATRIX_TUNNEL_FIREWALL=dropin-dev): development Spark only.
# The ViVeSec prep firewall regenerates its table every 30 s; this adds the tunnel port back.
nft list chain {table} input 2>/dev/null | grep -q '{comment}' && exit 0
exec nft 'insert rule {table} input ip saddr @lan4 tcp dport {port} ct state new accept comment "{comment}"'
"""
FIREWALL_DROPINS = ("vivesec-prep-v2-firewall.service", "vivesec-prep-v2-refresh.service")


def remove_prototype():
    """The hand-made first tunnel front of the dev Spark, if it is still there."""
    found = [unit for unit in PROTOTYPE_UNITS if Path(UNIT_DIR / unit).exists()]
    found += [path for path in PROTOTYPE_FILES + PROTOTYPE_DIRS if Path(path).exists()]
    if not found:
        return
    for unit in PROTOTYPE_UNITS:
        run("systemctl", "disable", "--now", unit, check=False)
    for path in PROTOTYPE_FILES:
        Path(path).unlink(missing_ok=True)
    for path in PROTOTYPE_DIRS:
        shutil.rmtree(path, ignore_errors=True)
    run("systemctl", "daemon-reload")
    for handle in re.findall(r'comment "matrix-7476" # handle (\d+)',
                             run("nft", "-a", "list", "chain", *PREP_TABLE.split(), "input", check=False).stdout):
        run("nft", "delete", "rule", *PREP_TABLE.split(), "input", "handle", handle, check=False)
    log("PROTOTYPE_REMOVED %d item(s)" % len(found))


def validate_pki(directory):
    crt, key, ca = (str(Path(directory) / name) for name in ("server.crt", "server.key", "ca.crt"))
    a = run("openssl", "x509", "-in", crt, "-noout", "-pubkey").stdout
    b = run("openssl", "pkey", "-in", key, "-pubout").stdout
    if a.strip() != b.strip():
        raise InstallError("%s: server.crt and server.key do not belong together" % directory)
    run("openssl", "verify", "-CAfile", ca, crt)


def install_app():
    """The bootloader's UI (kit app/): bundle first, then the manifest naming it,
    so the gateway never announces a bundle that is not there yet."""
    source = KIT / "app"
    if not (source / "manifest.json").is_file():
        log("WARNING the kit has no app/: the Matrix bootloader gets 503 until one is installed")
        return
    target = OPT / "app"
    target.mkdir(parents=True, exist_ok=True)
    os.chmod(target, 0o755)
    for name in ("bundle.json", "manifest.json"):
        temp = target / ("." + name + ".tmp")
        shutil.copyfile(source / name, temp)
        os.chmod(temp, 0o644)
        os.replace(temp, target / name)
    label = json.loads((target / "manifest.json").read_text(encoding="utf-8")).get("label")
    log("APP_INSTALLED %s" % label)


def phase_tunnel(conf):
    remove_prototype()
    (OPT / "matrix_tunnel").mkdir(parents=True, exist_ok=True)
    shutil.copyfile(SRC / "matrix_tunnel" / "gateway.py", OPT / "matrix_tunnel" / "gateway.py")
    os.chmod(OPT / "matrix_tunnel" / "gateway.py", 0o644)
    install_app()
    write_file(UNIT_DIR / "vivesec-matrix-tunnel.service", gateway_unit(conf))
    run("systemctl", "daemon-reload")
    run("systemctl", "enable", "vivesec-matrix-tunnel.service")
    run("systemctl", "restart", "vivesec-matrix-tunnel.service")
    gateway = "http://127.0.0.1:%d/api/v1/matrix/init" % conf["MATRIX_GATEWAY_PORT"]
    wait_for(lambda: http_status(gateway)[0] == 403, 30, "vivesec-matrix-tunnel")
    mode = conf["MATRIX_TUNNEL_PKI"]
    if mode == "off":
        for unit in ("vivesec-matrix-front.service", "vivesec-matrix-pki-sync.timer"):
            run("systemctl", "disable", "--now", unit, check=False)
        log("TUNNEL_OK gateway only (MATRIX_TUNNEL_PKI=off: no front)")
        return
    forward_key = (ROOT / "secrets" / "tunnel-forward.key").read_text().strip()
    write_file(FRONT_DIR / "nginx.conf", front_conf(conf, forward_key), 0o600)
    write_file(UNIT_DIR / "vivesec-matrix-front.service", FRONT_UNIT)
    FRONT_PKI.mkdir(parents=True, exist_ok=True)
    os.chmod(FRONT_PKI, 0o700)
    if mode == "files":
        validate_pki(FILES_PKI)
        for name in ("server.crt", "server.key", "ca.crt"):
            shutil.copyfile(FILES_PKI / name, FRONT_PKI / name)
            os.chmod(FRONT_PKI / name, 0o600)
        run("systemctl", "disable", "--now", "vivesec-matrix-pki-sync.timer", check=False)
        for unit in PKI_SYNC_UNITS:
            (UNIT_DIR / unit).unlink(missing_ok=True)
        Path("/usr/local/sbin/vivesec-matrix-pki-sync").unlink(missing_ok=True)
        run("systemctl", "daemon-reload")
        run("systemctl", "enable", "vivesec-matrix-front.service")
        run("systemctl", "restart", "vivesec-matrix-front.service")
    else:
        write_file("/usr/local/sbin/vivesec-matrix-pki-sync", PKI_SYNC, 0o755)
        for unit, text in PKI_SYNC_UNITS.items():
            write_file(UNIT_DIR / unit, text)
        run("systemctl", "daemon-reload")
        run("systemctl", "enable", "vivesec-matrix-front.service", "vivesec-matrix-pki-sync.timer")
        run("systemctl", "restart", "vivesec-matrix-pki-sync.timer")
        run("systemctl", "start", "vivesec-matrix-pki-sync.service")
        if unit_active("vivesec-matrix-front.service"):
            run("systemctl", "reload", "vivesec-matrix-front.service")
    firewall(conf)
    log("TUNNEL_OK front :%d (pki=%s, firewall=%s)" % (conf["MATRIX_TUNNEL_PORT"], mode,
                                                       conf["MATRIX_TUNNEL_FIREWALL"]))


def firewall(conf):
    helper = Path("/usr/local/sbin/vivesec-matrix-firewall")
    dropins = [UNIT_DIR / (unit + ".d") / "vivesec-matrix-tunnel.conf" for unit in FIREWALL_DROPINS]
    for handle in re.findall(r'comment "%s" # handle (\d+)' % FIREWALL_COMMENT,
                             run("nft", "-a", "list", "chain", *PREP_TABLE.split(), "input", check=False).stdout):
        run("nft", "delete", "rule", *PREP_TABLE.split(), "input", "handle", handle, check=False)
    if conf["MATRIX_TUNNEL_FIREWALL"] != "dropin-dev":
        helper.unlink(missing_ok=True)
        for dropin in dropins:
            dropin.unlink(missing_ok=True)
        run("systemctl", "daemon-reload")
        return
    write_file(helper, FIREWALL_HELPER.format(table=PREP_TABLE, comment=FIREWALL_COMMENT,
                                              port=conf["MATRIX_TUNNEL_PORT"]), 0o755)
    for dropin in dropins:
        write_file(dropin, "[Service]\nExecStartPost=-%s\n" % helper)
    run("systemctl", "daemon-reload")
    run(str(helper))


# ---------------------------------------------------------------- verify

def front_certificate(port):
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    with socket.create_connection(("127.0.0.1", port), timeout=5) as raw:
        with context.wrap_socket(raw) as tls:
            return hashlib.sha256(tls.getpeercert(binary_form=True)).hexdigest()


def pem_fingerprint(path):
    der = ssl.PEM_cert_to_DER_cert(Path(path).read_text())
    return hashlib.sha256(der).hexdigest()


UPDATER_DIR = OPT / "updater"
UPDATER_LIB = UPDATER_DIR / "lib"
UPDATER_SHARE = ROOT / "adapter" / "updates"
UPDATER_UNITS = ("vivesec-matrix-updater-check.timer", "vivesec-matrix-updater-request.path")


def updater_units():
    """systemd units of the updater: a check timer and the approval path unit.
    The updater runs from the installed kit, so an update brings its new code."""
    # -S: not even the host's dist-packages (boto3, an older pyOpenSSL) are
    # seen; they break the kit's cryptography, which securesystemslib imports.
    command = "/usr/bin/python3 -S -B %s/src/matrix_updater/updater.py" % Path("/opt/vivesec-matrix-kit").as_posix()
    environment = "Environment=PYTHONPATH=%s\nEnvironment=PYTHONDONTWRITEBYTECODE=1\n" % UPDATER_LIB.as_posix()
    return {
        "vivesec-matrix-updater-check.service": """[Unit]
Description=Matrix AI update check (signed release channel, MATRIX_UPDATE_URL)
After=network-online.target docker.service
Wants=network-online.target

[Service]
Type=oneshot
%sExecStart=%s check
TimeoutStartSec=3h
Nice=10
IOSchedulingClass=idle
""" % (environment, command),
        "vivesec-matrix-updater-check.timer": """[Unit]
Description=Matrix AI update check every 6 hours

[Timer]
OnBootSec=15min
OnUnitActiveSec=6h
RandomizedDelaySec=30min
Persistent=true

[Install]
WantedBy=timers.target
""",
        "vivesec-matrix-updater-request.service": """[Unit]
Description=Matrix AI update request of an administrator (install / roll back)
After=docker.service

[Service]
Type=oneshot
%sExecStart=%s request
# Longer than every step's own limit added up (data copy, image loads, two installer runs).
TimeoutStartSec=24h
""" % (environment, command),
        "vivesec-matrix-updater-request.path": """[Unit]
Description=Matrix AI update requests from the administration page

[Path]
PathExists=%s/request.json
MakeDirectory=yes
DirectoryMode=0755

[Install]
WantedBy=multi-user.target
""" % UPDATER_SHARE.as_posix(),
    }


def phase_updater(conf):
    # The adapter container (root) may have put anything here; a link is replaced, never followed.
    if UPDATER_SHARE.is_symlink() or (UPDATER_SHARE.exists() and not UPDATER_SHARE.is_dir()):
        UPDATER_SHARE.unlink()
    UPDATER_SHARE.mkdir(parents=True, exist_ok=True)
    fd = os.open(UPDATER_SHARE, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fchmod(fd, 0o755)
    finally:
        os.close(fd)
    if conf["MATRIX_UPDATE"] == "off":
        for unit in UPDATER_UNITS:
            run("systemctl", "disable", "--now", unit, check=False)
        log("UPDATER_OFF (MATRIX_UPDATE=off; the admin page shows updates as off)")
        return
    wheels = sorted((KIT / "wheels").glob("*.whl"))
    trusted = SRC / "matrix_updater" / "trusted_root.json"
    if not wheels or not trusted.is_file():
        raise InstallError("MATRIX_UPDATE=on but this kit has no wheels/ or matrix_updater/trusted_root.json")
    # Wheels unpacked into a private folder: nothing is installed into the host's Python.
    staged = UPDATER_DIR / "lib.new"
    shutil.rmtree(staged, ignore_errors=True)
    staged.mkdir(parents=True)
    for wheel in wheels:
        with zipfile.ZipFile(wheel) as archive:
            for member in archive.namelist():
                target = (staged / member).resolve()
                if not str(target).startswith(str(staged.resolve()) + os.sep):
                    raise InstallError("%s holds a path outside its folder: %s" % (wheel.name, member))
            archive.extractall(staged)
    probe = run("/usr/bin/python3", "-S", "-c", "import tuf.ngclient, cryptography; print(tuf.__version__)",
                check=False, env=dict(os.environ, PYTHONPATH=str(staged)))
    if probe.returncode:
        raise InstallError("the updater libraries do not load:\n%s" % (probe.stderr or probe.stdout)[-1500:])
    shutil.rmtree(UPDATER_DIR / "lib.prev", ignore_errors=True)
    if UPDATER_LIB.exists():
        os.replace(UPDATER_LIB, UPDATER_DIR / "lib.prev")
    os.replace(staged, UPDATER_LIB)
    for name, text in updater_units().items():
        write_file(UNIT_DIR / name, text)
    run("systemctl", "daemon-reload")
    for unit in UPDATER_UNITS:
        run("systemctl", "enable", "--now", unit)
    log("UPDATER_OK tuf %s channel=%s %s" % (probe.stdout.strip(), conf["MATRIX_UPDATE_CHANNEL"],
                                             conf["MATRIX_UPDATE_URL"]))


def phase_verify(conf):
    checks = []

    def check(name, ok, detail=""):
        checks.append(ok)
        log("%s %s%s" % ("PASS" if ok else "FAIL", name, (" (%s)" % detail) if detail else ""))

    rag = http_status("http://127.0.0.1:%d/health" % conf["MATRIX_RAG_PORT"])[0]
    check("rag /health", rag == 200, rag)
    status, body = http_status("http://127.0.0.1:%d/api/v1/version" % conf["MATRIX_ADAPTER_PORT"])
    check("adapter /api/v1/version", status == 200, status)
    status = http_status("http://127.0.0.1:%d/api/v1/ui/scope" % conf["MATRIX_ADAPTER_PORT"],
                         headers={"VVS-User": "probe", "VVS-Drive": "L3N0b3JhZ2UvZHJpdmVzL3Byb2JlLw=="})[0]
    check("adapter refuses a call without a token", status == 401, status)
    status = http_status("http://127.0.0.1:%d/auth/health" % conf["MATRIX_AUTH_PORT"])[0]
    check("auth /auth/health", status == 200, status)
    status = http_status("http://127.0.0.1:%d/login" % conf["MATRIX_UI_PORT"])[0]
    check("ui /login (loopback)", status == 200, status)
    gateway = "http://127.0.0.1:%d/api/v1/matrix/init" % conf["MATRIX_GATEWAY_PORT"]
    status = http_status(gateway, headers={"VVS-User": "probe"})[0]
    check("gateway refuses a caller without the forward key", status == 403, status)
    forward_key = (ROOT / "secrets" / "tunnel-forward.key").read_text().strip()
    # A VVS-User the auth service refuses by its form: a valid one would get a
    # user of its own with AUTH_TUNNEL_AUTOCREATE.
    status = http_status(gateway, headers={"X-Matrix-Forward-Key": forward_key, "X-Matrix-Client-Verify": "SUCCESS",
                                           "VVS-User": INSTALL_PROBE})[0]
    check("gateway asks the auth service (malformed probe -> 400)", status == 400, status)
    app = "http://127.0.0.1:%d/api/v1/matrix/app/manifest" % conf["MATRIX_GATEWAY_PORT"]
    status = http_status(app, headers={"X-Matrix-Forward-Key": forward_key, "X-Matrix-Client-Verify": "SUCCESS"})[0]
    check("gateway serves the bootloader app manifest", status == 200, status)
    if conf["MATRIX_GRAPH"] == "on":
        status = http_status("http://127.0.0.1:%d/health" % conf["MATRIX_GRAPH_PORT"], timeout=10)[0]
        check("graph /health", status == 200, status)
        status = http_status("http://127.0.0.1:%d/health" % conf["MATRIX_BROKER_PORT"], timeout=10)[0]
        check("adapter GPU broker /health", status == 200, status)
    models = ollama_models(conf["MATRIX_OLLAMA_URL"])
    check("shared Ollama lists %s and %s" % (conf["MATRIX_GEN_MODEL"], conf["MATRIX_EMBED_MODEL"]),
          all(model_digest(models, conf[key])[0] for key in ("MATRIX_GEN_MODEL", "MATRIX_EMBED_MODEL")))
    if conf["MATRIX_TUNNEL_PKI"] != "off":
        front_ok = unit_active("vivesec-matrix-front.service")
        check("tunnel front active on :%d" % conf["MATRIX_TUNNEL_PORT"], front_ok)
        if front_ok:
            presented = front_certificate(conf["MATRIX_TUNNEL_PORT"])
            check("front presents the tunnel certificate", presented == pem_fingerprint(FRONT_PKI / "server.crt"),
                  presented[:16])
            context = ssl.create_default_context()
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
            request = urllib.request.Request("https://127.0.0.1:%d/api/v1/matrix/init" % conf["MATRIX_TUNNEL_PORT"],
                                             headers={"VVS-User": "probe"})
            try:
                with urllib.request.urlopen(request, timeout=5, context=context) as response:
                    status = response.status
            except urllib.error.HTTPError as e:
                status = e.code
            except OSError:
                status = 0
            check("front refuses a client without a certificate", status in (0, 400), status)
    if conf["MATRIX_UPDATE"] == "on":
        for unit in UPDATER_UNITS:
            check("%s active" % unit, unit_active(unit))
    if not all(checks):
        raise InstallError("verify: %d check(s) failed" % checks.count(False))
    log("MATRIX_READY %d checks" % len(checks))


# ---------------------------------------------------------------- driver

PHASE_FUNCTIONS = {"preflight": phase_preflight, "dirs": phase_dirs, "images": phase_images,
                   "clamav": phase_clamav, "auth": phase_auth, "graph": phase_graph, "core": phase_core,
                   "tunnel": phase_tunnel, "updater": phase_updater, "verify": phase_verify}


def done_phases():
    return set(STATE_FILE.read_text().split()) if STATE_FILE.is_file() else set()


def mark_done(phase):
    done = done_phases() | {phase}
    write_file(STATE_FILE, "\n".join(sorted(done)) + "\n", 0o600)


def status(conf):
    for name in ("vivesec-rag", "vivesec-adapter", "vivesec-ui", "vivesec-auth", "vivesec-clamav",
                 "vivesec-neo4j", "vivesec-graph"):
        result = run("docker", "container", "inspect", "-f", "{{.State.Status}} {{.Config.Image}}", name, check=False)
        print("%-24s %s" % (name, result.stdout.strip() if result.returncode == 0 else "absent"))
    for unit in ("vivesec-matrix-tunnel.service", "vivesec-matrix-front.service", "vivesec-matrix-pki-sync.timer",
                 *UPDATER_UNITS):
        print("%-34s %s" % (unit, run("systemctl", "is-active", unit, check=False).stdout.strip()))
    print("phases done: %s" % " ".join(phase for phase in PHASES if phase in done_phases()))
    print("tunnel: pki=%s firewall=%s port=%d" % (conf["MATRIX_TUNNEL_PKI"], conf["MATRIX_TUNNEL_FIREWALL"],
                                                 conf["MATRIX_TUNNEL_PORT"]))


def main(argv=None):
    parser = argparse.ArgumentParser(description="Matrix AI on a DGX Spark (E07 M1)")
    parser.add_argument("command", nargs="?", default="install", choices=("install", "status"))
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--check", action="store_true", help="preflight only")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--only", choices=PHASES)
    args = parser.parse_args(argv)
    if args.list:
        print("\n".join(PHASES))
        return 0
    try:
        conf = load_settings()
        if args.command == "status":
            status(conf)
            return 0
        if os.geteuid() != 0:
            raise InstallError("run as root (sudo)")
        if args.check:
            phase_preflight(conf)
            return 0
        phases = [args.only] if args.only else list(PHASES)
        skip = done_phases() if args.resume else set()
        for phase in phases:
            if phase in skip and phase not in ("preflight", "verify"):
                log("SKIP %s (done)" % phase)
                continue
            log("PHASE %s" % phase)
            PHASE_FUNCTIONS[phase](conf)
            mark_done(phase)
    except InstallError as e:
        log("ERROR %s" % e)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
