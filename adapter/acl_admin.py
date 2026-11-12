"""The administrator rules file of the standalone admin page (E27 S7).

The adapter already reads `ADAPTER_ACL_RULES` (acl.py) and re-reads it when it
changes. In standalone mode the administration page edits it through the
adapter, so the file is validated by the adapter's own rules before anything
is written:

* every path is a canonical store path (drivestore: NFC, no `.`/`..`, no
  control characters); two spellings of one path are refused, because the
  ACL keys match exactly and an NFD key would never apply;
* every principal and level must be valid. Where the reader would silently
  drop or "fail closed" a bad entry (acl._parse_rules), the editor refuses
  the whole save instead, so what the administrator saw is what applies;
* the write is atomic, owner-only (0600), and refused when the file changed
  since the page read it (`version`, the SHA-256 of the content).

Pure stdlib.
"""
import hashlib
import json
import os

import acl
import drivestore

MAX_RULES_BYTES = 1024 * 1024
MAX_RULE_PATHS = 2000
NODE_KEYS = ("allow", "deny", "inherit", "classification")


class RulesInvalid(Exception):
    """The submitted rules cannot be saved; the message says why."""


class RulesConflict(Exception):
    """The rules file changed since the page read it."""


def version_of(raw):
    return "none" if raw is None else hashlib.sha256(raw).hexdigest()


def read(path):
    """(raw bytes or None when missing, version). OSError when unreadable."""
    try:
        with open(path, "rb") as handle:
            raw = handle.read(MAX_RULES_BYTES + 1)
    except FileNotFoundError:
        return None, version_of(None)
    return raw, version_of(raw)


def decode(raw):
    """The file content as JSON for the page; (data, error)."""
    if raw is None:
        return {"paths": {}}, None
    if len(raw) > MAX_RULES_BYTES:
        return None, "the rules file is larger than %d bytes" % MAX_RULES_BYTES
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as e:
        return None, "not valid JSON: %s" % e
    if not isinstance(data, dict):
        return None, "not a JSON object"
    return data, None


def _principals(raw, where):
    if not isinstance(raw, list) or len(raw) > acl.MAX_PRINCIPALS:
        raise RulesInvalid("%s: a list of at most %d principals is expected" % (where, acl.MAX_PRINCIPALS))
    out = []
    for item in raw:
        principal = acl.normalize_principal(item) if isinstance(item, str) else None
        if principal is None:
            raise RulesInvalid("%s: %r is not a principal (user:, group:, role: or everyone)"
                               % (where, item))
        if principal not in out:
            out.append(principal)
    return out


def _level(raw, where):
    index = acl.parse_level(raw)
    if index is None:
        raise RulesInvalid("%s: unknown classification %r" % (where, raw))
    return acl.LEVELS[index]


def _node(raw, where):
    """The normalised node, or None when it sets nothing."""
    if not isinstance(raw, dict) or set(raw) - set(NODE_KEYS):
        raise RulesInvalid("%s: an object with %s is expected" % (where, ", ".join(NODE_KEYS)))
    node = {}
    if raw.get("allow") is not None:
        node["allow"] = _principals(raw["allow"], where + " allow")
    deny = _principals(raw.get("deny") or [], where + " deny")
    if deny:
        node["deny"] = deny
    inherit = raw.get("inherit", True)
    if not isinstance(inherit, bool):
        raise RulesInvalid("%s: inherit must be true or false" % where)
    if not inherit:
        node["inherit"] = False
    if raw.get("classification") not in (None, ""):
        node["classification"] = _level(raw["classification"], where)
    return node or None


def validate(data, prefix):
    """The normalised rules file content for `data`, or RulesInvalid."""
    if not isinstance(data, dict) or set(data) - {"paths", "clearance", "default_classification"}:
        raise RulesInvalid("an object with paths, clearance and default_classification is expected")
    raw_paths = data.get("paths") or {}
    if not isinstance(raw_paths, dict) or len(raw_paths) > MAX_RULE_PATHS:
        raise RulesInvalid("paths must be an object of at most %d rules" % MAX_RULE_PATHS)
    paths, seen = {}, set()
    for raw_path, raw_node in raw_paths.items():
        try:
            path = drivestore.canonical_path(raw_path, prefix)
        except drivestore.StoreError as e:
            raise RulesInvalid("%r: %s" % (raw_path, e))
        if path in seen:
            raise RulesInvalid("%r is listed twice" % path)
        seen.add(path)
        node = _node(raw_node, path)
        if node is not None:
            paths[path] = node
    clearance = {}
    raw_clearance = data.get("clearance") or {}
    if not isinstance(raw_clearance, dict) or len(raw_clearance) > acl.MAX_PRINCIPALS:
        raise RulesInvalid("clearance must be an object of principals and levels")
    for raw_key, level in raw_clearance.items():
        if isinstance(raw_key, str) and raw_key.strip().casefold() == "default":
            key = "default"
        else:
            key = acl.normalize_principal(raw_key) if isinstance(raw_key, str) else None
            if key is None or key == acl.EVERYONE:
                raise RulesInvalid("clearance: %r is not a user:, group: or role: principal" % raw_key)
        if key in clearance:
            raise RulesInvalid("clearance: %r is listed twice" % raw_key)
        clearance[key] = _level(level, "clearance " + key)
    out = {"paths": {path: paths[path] for path in sorted(paths)}}
    if clearance:
        out["clearance"] = clearance
    if data.get("default_classification") not in (None, ""):
        out["default_classification"] = _level(data["default_classification"], "default_classification")
    return out


def write(path, rules, base):
    """Write validated `rules` when the file is still at version `base`.
    Returns the new version. The caller serialises writers."""
    _raw, current = read(path)
    if not isinstance(base, str) or base != current:
        raise RulesConflict()
    data = (json.dumps(rules, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    directory = os.path.dirname(os.path.abspath(path))
    temp = os.path.join(directory, ".%s.tmp" % os.path.basename(path))
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    except BaseException:
        try:
            os.unlink(temp)
        except OSError:
            pass
        raise
    return version_of(data)


def without_tree(rules, root):
    """`rules` without any path at or under `root` (a deleted room)."""
    base = root.rstrip("/")
    kept = {path: node for path, node in (rules.get("paths") or {}).items()
            if path != base and not path.startswith(base + "/")}
    return dict(rules, paths=kept)
