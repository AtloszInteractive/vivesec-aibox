"""Configuration register (E01): every deployment parameter with its default,
allowed range, fail-safe behaviour and impact, in config_registry.json.

  python scripts/release/config_registry.py check     # register vs. source, Dockerfiles, install.conf
  python scripts/release/config_registry.py render    # rewrite CONFIGURATION.md from the register
  python scripts/release/config_registry.py validate ENV_FILE
        # check a box's runtime environment (KEY=value lines, e.g. from
        # `docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' vivesec-adapter`)

`check` fails when the code reads a parameter the register does not describe,
when a Dockerfile default drifts from the register, or when an entry is
malformed; release_test.py runs it, so the register cannot silently go stale.
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
REGISTRY_PATH = os.path.join(HERE, "config_registry.json")
DOC_PATH = os.path.join(ROOT, "CONFIGURATION.md")

COMPONENTS = ("adapter", "rag", "shared", "ui", "ui-build", "install")
CATEGORIES = {"network", "security", "scope", "storage", "model", "retrieval", "generation",
              "chat", "jobs", "sync", "files", "voice", "discovery", "pairing", "limits",
              "install", "build", "dev"}
TYPES = {"int", "float", "bool", "enum", "string", "path", "url", "list", "secret"}
CHANGES = {"install", "restart", "reindex"}
REQUIRED = ("name", "component", "category", "type", "default", "allowed", "fail_safe",
            "impact", "change", "secret", "internal")
SOURCE_DIRS = ("adapter", "rag_service", "poc")
PARAM_RE = re.compile(r"""["']((?:ADAPTER|RAG|VIVESEC|OLLAMA)_[A-Z0-9_]+)["']""")
# String literals that match the pattern but are not environment variables.
NOT_PARAMETERS = {"ollama_running", "vivesec_data"}
DOCKERFILES = {"adapter": "adapter/Dockerfile", "rag": "rag_service/Dockerfile"}
INSTALL_CONF = "scripts/jetson/install/install.conf.example"
# Prefixes the runtime validator treats as ours: an unregistered name with one
# of these is almost certainly a typo that silently leaves a default in force.
OWN_PREFIXES = ("ADAPTER_", "RAG_", "VIVESEC_")
TRUE_STRINGS = {"1", "true", "yes", "on"}
FALSE_STRINGS = {"0", "false", "no", "off", ""}


def load(path=REGISTRY_PATH):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def by_name(registry):
    return {entry["name"]: entry for entry in registry["parameters"]}


def schema_problems(registry):
    problems = []
    seen = set()
    for index, entry in enumerate(registry.get("parameters", [])):
        name = entry.get("name", "#%d" % index)
        missing = [key for key in REQUIRED if key not in entry]
        if missing:
            problems.append("%s: missing %s" % (name, ", ".join(missing)))
            continue
        if name in seen:
            problems.append("%s: duplicate entry" % name)
        seen.add(name)
        for key, allowed in (("component", COMPONENTS), ("category", CATEGORIES),
                             ("type", TYPES), ("change", CHANGES)):
            if entry[key] not in allowed:
                problems.append("%s: invalid %s %r" % (name, key, entry[key]))
        if entry["type"] == "enum" and not entry.get("values"):
            problems.append("%s: enum without values" % name)
        if entry["secret"] and entry["default"] not in (None, ""):
            problems.append("%s: a secret must not carry a default value" % name)
        for key in ("allowed", "fail_safe", "impact"):
            if not str(entry[key]).strip():
                problems.append("%s: empty %s" % (name, key))
    return problems


def source_parameters(root=ROOT):
    found = {}
    for directory in SOURCE_DIRS:
        for folder, dirs, files in os.walk(os.path.join(root, directory)):
            dirs[:] = [d for d in dirs if d != "__pycache__"]
            for file_name in files:
                if not file_name.endswith(".py") or file_name.endswith("_test.py") \
                        or file_name == "smoke_test.py":
                    continue
                path = os.path.join(folder, file_name)
                with open(path, encoding="utf-8") as handle:
                    for name in PARAM_RE.findall(handle.read()):
                        if name not in NOT_PARAMETERS:
                            found.setdefault(name, os.path.relpath(path, root).replace(os.sep, "/"))
    return found


def dockerfile_env(path):
    """ENV KEY=value pairs of a Dockerfile, continuation lines joined."""
    with open(path, encoding="utf-8") as handle:
        text = re.sub(r"\\\r?\n", " ", handle.read())
    values = {}
    for line in text.splitlines():
        line = line.strip()
        if not line.upper().startswith("ENV "):
            continue
        for key, value in re.findall(r"([A-Za-z_][A-Za-z0-9_]*)=(\"[^\"]*\"|\S*)", line[4:]):
            values[key] = value.strip('"')
    return values


def install_conf_keys(path):
    keys = {}
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            match = re.match(r"^([A-Z_][A-Z0-9_]*)=(.*)$", line.strip())
            if match:
                keys[match.group(1)] = match.group(2).strip().strip('"')
    return keys


def check(root=ROOT, registry=None):
    registry = registry or load()
    problems = schema_problems(registry)
    entries = by_name(registry)
    for name, where in sorted(source_parameters(root).items()):
        if name not in entries:
            problems.append("%s: read in %s but not in the register" % (name, where))
    for component, relative in DOCKERFILES.items():
        for name, value in dockerfile_env(os.path.join(root, relative)).items():
            if name in ("PYTHONUNBUFFERED",):
                continue
            entry = entries.get(name)
            if entry is None:
                problems.append("%s: set in %s but not in the register" % (name, relative))
                continue
            expected = entry.get("image_default")
            if isinstance(expected, dict):
                # A shared parameter may be baked differently into each image.
                expected = expected.get(component)
            if expected != value:
                problems.append("%s: %s sets %r, register image_default is %r"
                                % (name, relative, value, expected))
    for name in install_conf_keys(os.path.join(root, INSTALL_CONF)):
        if name not in entries:
            problems.append("%s: in %s but not in the register" % (name, INSTALL_CONF))
    return problems


def _number(entry, raw):
    try:
        return (int if entry["type"] == "int" else float)(raw)
    except ValueError:
        return None


def validate_env(env, registry=None):
    """Problems of a running configuration. Secret values are never echoed."""
    registry = registry or load()
    entries = by_name(registry)
    problems = []
    for name, raw in sorted(env.items()):
        entry = entries.get(name)
        if entry is None:
            if name.startswith(OWN_PREFIXES):
                problems.append("%s: not a known parameter (typo?)" % name)
            continue
        shown = "<secret>" if entry["secret"] else repr(raw)
        value = raw.strip()
        if entry["type"] in ("int", "float") and value:
            number = _number(entry, value)
            if number is None:
                problems.append("%s=%s: not a valid %s" % (name, shown, entry["type"]))
            elif ("min" in entry and number < entry["min"]) or ("max" in entry and number > entry["max"]):
                problems.append("%s=%s: outside %s" % (name, shown, entry["allowed"]))
        elif entry["type"] == "enum" and value and value.lower() not in entry["values"]:
            problems.append("%s=%s: not one of %s" % (name, shown, ", ".join(entry["values"])))
        elif entry["type"] == "bool" and value.lower() not in TRUE_STRINGS | FALSE_STRINGS \
                and value.lower() not in [str(v).lower() for v in entry.get("values", [])]:
            problems.append("%s=%s: not a recognised boolean" % (name, shown))
        if entry["internal"] and value.lower() not in FALSE_STRINGS:
            problems.append("%s: dev/test-only parameter is set on this box" % name)
    return problems


def parse_env_lines(text):
    env = {}
    for line in text.splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, _, value = line.partition("=")
            env[key.strip()] = value
    return env


def _cell(value):
    text = "" if value is None else str(value)
    return text.replace("|", "\\|").replace("\n", " ")


def render(registry=None):
    registry = registry or load()
    titles = {"adapter": "Adapter", "rag": "RAG service", "shared": "Shared (several components)",
              "ui": "Web UI server", "ui-build": "Web UI build", "install": "Installation kit"}
    lines = [
        "# Configuration register",
        "",
        "<!-- Generated from scripts/release/config_registry.json by",
        "     `python scripts/release/config_registry.py render`. Do not edit by hand. -->",
        "",
        "Every deployment parameter of the AI Box. **Default** is the value the code uses",
        "when the variable is unset; **Image** is the value baked into the container image",
        "(empty cell: not set by the image). **Change** says what a change requires:",
        "`install` - the installation/redeploy procedure, `restart` - recreating the",
        "container with the new environment, `reindex` - additionally rebuilding the index.",
        "Parameters marked *dev only* must not be set on a production box. Secret values",
        "are never recorded in manifests or logs.",
        "",
        "Check a running box: `python scripts/release/config_registry.py validate env.txt`.",
        "",
    ]
    for component in COMPONENTS:
        entries = [e for e in registry["parameters"] if e["component"] == component]
        if not entries:
            continue
        lines += ["## %s" % titles[component], "",
                  "| Parameter | Default | Image | Allowed | Change | Impact | Invalid value |",
                  "| --- | --- | --- | --- | --- | --- | --- |"]
        for e in sorted(entries, key=lambda item: item["name"]):
            flags = []
            if e["secret"]:
                flags.append("secret")
            if e["internal"]:
                flags.append("dev only")
            name = "`%s`" % e["name"] + (" (%s)" % ", ".join(flags) if flags else "")
            default = "*unset*" if e["default"] in (None, "") else "`%s`" % _cell(e["default"])
            image_default = e.get("image_default")
            if isinstance(image_default, dict):
                image = "; ".join("%s: `%s`" % (k, _cell(v)) for k, v in sorted(image_default.items()))
            else:
                image = {None: "", "": "*empty*"}.get(image_default, "`%s`" % _cell(image_default))
            lines.append("| %s | %s | %s | %s | %s | %s | %s |" % (
                name, default, image, _cell(e["allowed"]), e["change"],
                _cell(e["impact"]), _cell(e["fail_safe"])))
        lines.append("")
    return "\n".join(lines)


def main(argv):
    command = argv[0] if argv else "check"
    if command == "check":
        problems = check()
        for problem in problems:
            print("REGISTER: %s" % problem, file=sys.stderr)
        if not problems:
            print("register OK: %d parameters" % len(load()["parameters"]))
        return 1 if problems else 0
    if command == "render":
        with open(DOC_PATH, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(render())
        print("wrote %s" % os.path.relpath(DOC_PATH, ROOT))
        return 0
    if command == "validate" and len(argv) == 2:
        with open(argv[1], encoding="utf-8") as handle:
            problems = validate_env(parse_env_lines(handle.read()))
        for problem in problems:
            print("CONFIG: %s" % problem)
        if not problems:
            print("configuration OK")
        return 1 if problems else 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
