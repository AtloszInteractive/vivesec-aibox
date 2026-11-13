"""Shared helpers of the standalone installation kit (E27 S8).

Settings come from /etc/aibox/standalone.conf (KEY=value lines, shell-style
quoting; AIBOX_STANDALONE_CONF overrides the path) and the environment, which
wins. Secrets never pass through here as arguments: commands that need a key
get it on stdin (`run(..., stdin=key)`), and nothing logs stdin.

Python 3.8+ (the Jetson host has 3.8), stdlib only.
"""
import json
import os
import shlex
import subprocess
import sys
import time

CONF_PATH = "/etc/aibox/standalone.conf"

# Start order of the application containers; the reverse is the stop order.
CONTAINER_ORDER = ("vivesec-neo4j", "vivesec-graph", "vivesec-rag", "vivesec-clamav",
                   "vivesec-auth", "vivesec-adapter", "vivesec-ui", "vivesec-https")


class KitError(Exception):
    """An expected failure: printed without a traceback, exit code 1."""


def parse_conf(text):
    """KEY=value lines; comments, blank lines and non-AIBOX keys are ignored."""
    out = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key.startswith("export "):
            key = key[len("export "):].strip()
        if not key.startswith("AIBOX_") or not key.replace("_", "").isalnum():
            continue
        try:
            parts = shlex.split(value, comments=True)
        except ValueError as e:
            raise KitError("%s: cannot parse %s: %s" % (CONF_PATH, key, e))
        out[key] = " ".join(parts)
    return out


def load_settings(env=None, path=None):
    """The config file overlaid with AIBOX_* variables of the environment."""
    env = os.environ if env is None else env
    path = path or env.get("AIBOX_STANDALONE_CONF") or CONF_PATH
    merged = {}
    try:
        with open(path, encoding="utf-8") as f:
            merged.update(parse_conf(f.read()))
    except FileNotFoundError:
        pass
    merged.update({k: v for k, v in env.items() if k.startswith("AIBOX_")})
    return merged


def log(message):
    print("[%s] %s" % (time.strftime("%H:%M:%S"), message), flush=True)


def run(args, stdin=None, check=True, timeout=None):
    """Run a command; `stdin` (bytes) is the only channel for secrets."""
    try:
        result = subprocess.run(list(args), input=stdin, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, timeout=timeout,
                                **({} if stdin is not None else {"stdin": subprocess.DEVNULL}))
    except FileNotFoundError:
        raise KitError("command not found: %s" % args[0])
    out = result.stdout.decode("utf-8", "replace")
    err = result.stderr.decode("utf-8", "replace")
    if check and result.returncode:
        raise KitError("%s failed (exit %d): %s" % (" ".join(args[:3]), result.returncode,
                                                    err.strip()[-1500:]))
    return result.returncode, out, err


def require_root():
    if os.geteuid() != 0:
        raise KitError("run as root (sudo)")


def docker_inspect(names):
    if not names:
        return []
    code, out, _ = run(["docker", "inspect"] + list(names), check=False)
    if code and not out.strip():
        return []
    try:
        return json.loads(out or "[]")
    except ValueError:
        return []


def docker_containers():
    _, out, _ = run(["docker", "ps", "-a", "--format", "{{.Names}}"])
    return docker_inspect([n for n in out.split() if n])


def order_containers(names):
    """Known containers in CONTAINER_ORDER, the others after vivesec-rag."""
    rank = {name: i * 10 for i, name in enumerate(CONTAINER_ORDER)}
    return sorted(names, key=lambda n: (rank.get(n, rank["vivesec-rag"] + 5), n))


def under(path, root):
    path, root = path.replace(os.sep, "/"), root.replace(os.sep, "/")
    path, root = path.rstrip("/") or "/", root.rstrip("/") or "/"
    return path == root or path.startswith(root + "/") or root == "/"


def prompt_secret(label, confirm=False):
    import getpass
    if not sys.stdin.isatty():
        raise KitError("%s: an interactive terminal is needed" % label)
    first = getpass.getpass(label + ": ")
    if confirm and getpass.getpass(label + " (again): ") != first:
        raise KitError("the two entries differ")
    return first


def main_guard(func, argv):
    try:
        return func(argv)
    except KitError as e:
        print("ERROR: %s" % e, file=sys.stderr, flush=True)
        return 1
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130
