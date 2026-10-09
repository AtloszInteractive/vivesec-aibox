"""Box manifest (E01): what is actually installed and running on this AI Box.

Run on the box (stdlib only, needs the docker CLI):
  python3 scripts/release/box_manifest.py --out /data/app/MANIFEST.json

Records the release identity reported by the services, the images and their
version labels, the models present in Ollama, the platform, the non-secret
runtime configuration of every container (validated against the
configuration register) and the pairing state, then evaluates the release's
compatibility conditions. Secret values are never written: a secret is
recorded only as set/empty.

Read-only: it calls /api/v1/version (never /api/v1/status, which would count as
a ViVeSecBox presence poll) and changes nothing on the box.
"""
import argparse
import datetime
import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config_registry  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
CONTAINERS = {"adapter": "vivesec-adapter", "rag": "vivesec-rag", "ui": "vivesec-ui"}
SECRET_NAME_RE = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL)", re.I)
OLLAMA = "http://127.0.0.1:11434"


def _run(*args):
    result = subprocess.run(args, capture_output=True, text=True, timeout=60)
    if result.returncode:
        raise RuntimeError("%s failed: %s" % (" ".join(args[:3]), result.stderr.strip()[-300:]))
    return result.stdout


def _http_json(url, timeout=10):
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.load(response)


def _read_first_line(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            return handle.readline().strip()
    except OSError:
        return None


def redact_env(env, registry):
    entries = config_registry.by_name(registry)
    out = {}
    for name, value in sorted(env.items()):
        entry = entries.get(name)
        secret = entry["secret"] if entry else bool(SECRET_NAME_RE.search(name))
        out[name] = ("<set>" if value else "<empty>") if secret else value
    return out


def _version_tuple(text):
    return tuple(int(part) for part in re.findall(r"\d+", text or "")[:3])


def parse_l4t(line):
    """'# R36 (release), REVISION: 4.4, GCID: ...' -> 'R36.4.4' (None if unknown)."""
    match = re.search(r"R(\d+)\b.*?REVISION:\s*(\d+(?:\.\d+)*)", line or "")
    return "R%s.%s" % match.groups() if match else None


def _base_version(label):
    return (label or "").split("-", 1)[0].split("+", 1)[0] or None


def compatibility_checks(manifest, compatibility):
    checks = []

    def add(name, ok, detail):
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    versions = {kind: _base_version(manifest["containers"].get(kind, {}).get("version"))
                for kind in compatibility["components"]["same_version"]}
    add("components carry the same version", len(set(versions.values())) == 1 and None not in versions.values(),
        versions)
    release = manifest.get("release") or {}
    add("adapter and RAG run the same build", release.get("consistent") is True,
        {kind: (component or {}).get("label") for kind, component in
         (release.get("components") or {}).items()})
    ollama = manifest["platform"].get("ollama")
    minimum = compatibility["platform"]["ollama_min"]
    add("ollama >= %s" % minimum, ollama and _version_tuple(ollama) >= _version_tuple(minimum), ollama)
    present = {model["name"] for model in manifest["models"]}

    def has(model):
        return model in present or (":" not in model and model + ":latest" in present)

    adapter_env = manifest["containers"].get("adapter", {}).get("env", {})
    rag_env = manifest["containers"].get("rag", {}).get("env", {})
    generation = adapter_env.get("ADAPTER_GEN_MODEL") or compatibility["models"]["generation"]
    embedding = rag_env.get("VIVESEC_EMBED_MODEL") or compatibility["models"]["embedding"]
    add("generation model is the release model", generation == compatibility["models"]["generation"], generation)
    add("generation model is present", has(generation), generation)
    add("embedding model is the release model",
        embedding.split(":")[0] == compatibility["models"]["embedding"].split(":")[0], embedding)
    add("embedding model is present", has(embedding), embedding)
    l4t = manifest["platform"].get("l4t")
    add("L4T %s" % compatibility["platform"]["l4t"], l4t == compatibility["platform"]["l4t"], l4t)
    return checks


def collect(run=_run, http_json=_http_json, registry=None, compatibility=None, now=None):
    registry = registry or config_registry.load()
    if compatibility is None:
        with open(os.path.join(HERE, "compatibility.json"), encoding="utf-8") as handle:
            compatibility = json.load(handle)
    errors = []
    containers = {}
    config_problems = []
    for kind, name in CONTAINERS.items():
        try:
            info = json.loads(run("docker", "inspect", name))[0]
        except (RuntimeError, ValueError, IndexError, OSError) as error:
            errors.append("%s: %s" % (name, error))
            continue
        env = dict(item.split("=", 1) for item in info["Config"].get("Env") or [] if "=" in item)
        labels = info["Config"].get("Labels") or {}
        containers[kind] = {
            "name": name, "image": info["Config"].get("Image"), "image_id": info.get("Image"),
            "running": info["State"].get("Running"), "started_at": info["State"].get("StartedAt"),
            "version": labels.get("vivesec.version"), "commit": labels.get("vivesec.commit"),
            "env": redact_env(env, registry),
        }
        config_problems += ["%s: %s" % (kind, problem)
                            for problem in config_registry.validate_env(env, registry)]

    adapter_port = (containers.get("adapter", {}).get("env", {}).get("ADAPTER_PORT") or "8088")
    release, pairing = None, None
    try:
        payload = http_json("http://127.0.0.1:%s/api/v1/version" % adapter_port)
        release = payload.get("version")
        pairing = {"paired": payload.get("paired"), "ws_fs": payload.get("ws_fs"),
                   "storage_locked": payload.get("storage_locked")}
    except Exception as error:  # noqa: BLE001
        errors.append("adapter /api/v1/version: %s" % error)
    ui_port = containers.get("ui", {}).get("env", {}).get("PORT")
    if ui_port:
        try:
            containers["ui"]["build"] = http_json("http://127.0.0.1:%s/version.json" % ui_port)
            containers["ui"]["version"] = containers["ui"]["build"].get("label") or containers["ui"]["version"]
        except Exception as error:  # noqa: BLE001
            errors.append("ui /version.json: %s" % error)
    for kind in ("adapter", "rag"):
        component = ((release or {}).get("components") or {}).get(kind)
        if component and kind in containers:
            containers[kind]["version"] = component.get("label")
            containers[kind]["commit"] = component.get("commit")

    models = []
    tegra_release = _read_first_line("/etc/nv_tegra_release")
    platform = {"l4t": parse_l4t(tegra_release), "nv_tegra_release": tegra_release}
    try:
        platform["ollama"] = http_json(OLLAMA + "/api/version").get("version")
        models = [{"name": m.get("name"), "digest": m.get("digest"), "bytes": m.get("size")}
                  for m in http_json(OLLAMA + "/api/tags").get("models", [])]
    except Exception as error:  # noqa: BLE001
        errors.append("ollama: %s" % error)
    for key, args in (("kernel", ("uname", "-r")),
                      ("docker", ("docker", "version", "--format", "{{.Server.Version}}"))):
        try:
            platform[key] = run(*args).strip()
        except (RuntimeError, OSError) as error:
            errors.append("%s: %s" % (key, error))

    manifest = {
        "schema": 1,
        "kind": "vivesec-aibox-box-manifest",
        "box": os.environ.get("AIBOX_HOSTNAME") or _read_first_line("/etc/hostname"),
        "generated": (now or datetime.datetime.now(datetime.timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "release": release,
        "pairing": pairing,
        "containers": containers,
        "models": models,
        "platform": platform,
        "configuration": {"register_sha256": _sha256(config_registry.REGISTRY_PATH),
                          "problems": config_problems},
        "compatibility": compatibility,
        "errors": errors,
    }
    manifest["checks"] = compatibility_checks(manifest, compatibility)
    return manifest


def _sha256(path):
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", default="/data/app/MANIFEST.json")
    args = parser.parse_args(argv)
    manifest = collect()
    temporary = args.out + ".tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    os.chmod(temporary, 0o644)
    os.replace(temporary, args.out)
    failed = [check["check"] for check in manifest["checks"] if not check["ok"]]
    print("AIBOX_BOX_MANIFEST path=%s release=%s failed_checks=%d config_problems=%d errors=%d" % (
        args.out, (manifest["release"] or {}).get("release", "unknown"), len(failed),
        len(manifest["configuration"]["problems"]), len(manifest["errors"])))
    for name in failed:
        print("CHECK_FAILED %s" % name)
    for problem in manifest["configuration"]["problems"]:
        print("CONFIG %s" % problem)
    for error in manifest["errors"]:
        print("ERROR %s" % error)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
