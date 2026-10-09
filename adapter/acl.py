"""File, folder and room level access control (E03).

The drive scope (scope.py) decides which corpora a request may search. This
module decides, inside those corpora, which documents the caller may see. It is
the ONE implementation of that rule: the RAG service only evaluates the
flattened result (`Effective.as_rag`) in its SQL, and every adapter path that
names a file -- filename search, folder listing, download, statistics -- asks
the same `Policy`.

Model
  * Principals: `user:<id>`, `group:<id>`, `role:<value>` and `everyone`,
    compared case-insensitively.
  * A node (room = drive root, folder or file) may carry an ACL:
    `{"allow": [...], "deny": [...], "inherit": true, "classification": "..."}`.
    It comes from the ViVeSecBox sync metadata (`acl` field, stored in the
    mirror) and/or from the administrator rules file (`ADAPTER_ACL_RULES`).
  * Deny first: a deny on ANY level of the path (room -> folders -> file)
    hides the document. Allow lists inherit downwards; the nearest level that
    defines one decides. `inherit: false` stops the parent's allow list (a
    node with `inherit: false` and no allow list of its own is visible to
    nobody); denies always inherit.
  * A path without any allow list stays visible to everyone who may reach the
    drive -- the drive-level behaviour the box had before E03.
  * Classification: public < internal < confidential < strictly_confidential
    < personal. A document's level is the highest level on its path (the
    default level when none is set); the caller sees up to their clearance.

Fail-closed rules
  * A malformed ACL denies its whole subtree; an unknown classification is
    treated as the highest level.
  * A configured rules file that cannot be read or parsed denies everything.
  * When the caller's group membership is unknown (directory off or failing),
    a group/role allow cannot match, and a document with ANY group/role deny is
    hidden -- the caller might be in that group.
"""
import hashlib
import json
import os
import sys
import threading

import corpus

MODE_ENFORCE = "enforce"
MODE_OFF = "off"
MODES = (MODE_ENFORCE, MODE_OFF)

LEVELS = ("public", "internal", "confidential", "strictly_confidential", "personal")
TOP_LEVEL = len(LEVELS) - 1
_LEVEL_ALIASES = {
    "nyilvanos": 0, "nyilvános": 0,
    "belso": 1, "belső": 1,
    "bizalmas": 2,
    "szigoruan_bizalmas": 3, "szigorúan_bizalmas": 3, "strictly_confidential": 3,
    "secret": 3,
    "szemelyes": 4, "személyes": 4, "szemelyes_adat": 4, "személyes_adat": 4,
    "personal_data": 4,
}

EVERYONE = "everyone"
PRINCIPAL_TYPES = ("user", "group", "role")
MAX_PRINCIPALS = 512
MAX_PRINCIPAL_LENGTH = 256


def parse_level(value):
    """Level index for a name or an index, None when unknown."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if 0 <= value <= TOP_LEVEL else None
    if not isinstance(value, str):
        return None
    key = value.strip().casefold().replace("-", "_").replace(" ", "_")
    if key in LEVELS:
        return LEVELS.index(key)
    return _LEVEL_ALIASES.get(key)


def level_name(index):
    if isinstance(index, int) and 0 <= index <= TOP_LEVEL:
        return LEVELS[index]
    return None


def normalize_principal(value):
    """`type:id` (casefolded) or `everyone`; None for anything unusable."""
    if isinstance(value, dict):
        kind, ident = value.get("type"), value.get("id")
        if isinstance(kind, str) and kind.strip().casefold() == EVERYONE:
            return EVERYONE
        if not isinstance(kind, str) or not isinstance(ident, (str, int)) \
                or isinstance(ident, bool):
            return None
        text = "%s:%s" % (kind, ident)
    elif isinstance(value, str):
        text = value
    else:
        return None
    text = text.strip()
    if text.casefold() in (EVERYONE, "*"):
        return EVERYONE
    kind, sep, ident = text.partition(":")
    kind, ident = kind.strip().casefold(), ident.strip()
    if not sep or kind not in PRINCIPAL_TYPES or not ident:
        return None
    if len(ident) > MAX_PRINCIPAL_LENGTH or any(ord(ch) < 32 for ch in ident):
        return None
    return kind + ":" + ident.casefold()


def _principal_list(raw):
    """Normalized principals, or None when the list is malformed."""
    if not isinstance(raw, (list, tuple)) or len(raw) > MAX_PRINCIPALS:
        return None
    out = []
    for item in raw:
        principal = normalize_principal(item)
        if principal is None:
            return None
        if principal not in out:
            out.append(principal)
    return tuple(out)


class NodeAcl:
    """The ACL set on ONE node, before inheritance."""

    __slots__ = ("allow", "deny", "inherit", "classification", "invalid", "source")

    def __init__(self, allow=None, deny=(), inherit=True, classification=None,
                 invalid=False, source="sync"):
        self.allow = None if allow is None else tuple(allow)
        self.deny = tuple(deny)
        self.inherit = bool(inherit)
        self.classification = classification
        self.invalid = bool(invalid)
        self.source = source

    def as_dict(self):
        if self.invalid:
            return {"invalid": True}
        out = {"deny": list(self.deny), "inherit": self.inherit}
        if self.allow is not None:
            out["allow"] = list(self.allow)
        if self.classification is not None:
            out["classification"] = LEVELS[self.classification]
        return out


def parse_node(raw, source="sync"):
    """Parse one node ACL. None means "no ACL on this node"; anything
    malformed becomes an invalid node, which denies its subtree."""
    if raw is None:
        return None
    if not isinstance(raw, dict) or raw.get("invalid"):
        return NodeAcl(invalid=True, source=source)
    allow = None
    if raw.get("allow") is not None:
        allow = _principal_list(raw.get("allow"))
        if allow is None:
            return NodeAcl(invalid=True, source=source)
    deny = ()
    if raw.get("deny") is not None:
        deny = _principal_list(raw.get("deny"))
        if deny is None:
            return NodeAcl(invalid=True, source=source)
    inherit = raw.get("inherit", True)
    if not isinstance(inherit, bool):
        return NodeAcl(invalid=True, source=source)
    classification = None
    if raw.get("classification") not in (None, ""):
        classification = parse_level(raw.get("classification"))
        if classification is None:
            classification = TOP_LEVEL
    return NodeAcl(allow, deny, inherit, classification, source=source)


def canonical(raw):
    """Storage form of a sync ACL (what the mirror keeps), or None."""
    node = parse_node(raw)
    return None if node is None else node.as_dict()


class Effective:
    """The flattened ACL of one path: what the RAG stores per document."""

    __slots__ = ("restricted", "allow", "deny", "classification", "sources", "invalid")

    def __init__(self, restricted, allow, deny, classification, sources=(), invalid=False):
        self.restricted = bool(restricted)
        self.allow = tuple(sorted(set(allow)))
        self.deny = tuple(sorted(set(deny)))
        self.classification = classification
        self.sources = list(sources)
        self.invalid = bool(invalid)

    @classmethod
    def deny_all(cls, reason, sources=()):
        return cls(True, (), (), TOP_LEVEL, list(sources) + [{"reason": reason}], invalid=True)

    def as_rag(self):
        return {"restricted": self.restricted, "allow": list(self.allow),
                "deny": list(self.deny), "classification": self.classification}

    def as_dict(self):
        return {"restricted": self.restricted, "allow": list(self.allow),
                "deny": list(self.deny), "classification": level_name(self.classification),
                "invalid": self.invalid, "sources": self.sources}


def flatten(levels, default_classification):
    """`levels`: [(path, [NodeAcl, ...]), ...] from the room down to the node.

    Several ACLs on the same node (rules file + sync metadata) combine
    strictly: allow lists intersect, denies add up, the higher classification
    wins and inheritance survives only if every source keeps it."""
    merged = []
    sources = []
    for path, nodes in levels:
        nodes = [node for node in nodes if node is not None]
        if not nodes:
            continue
        for node in nodes:
            sources.append(dict(node.as_dict(), path=path, source=node.source))
            if node.invalid:
                return Effective.deny_all("invalid ACL at %s" % path, sources)
        allow = None
        for node in nodes:
            if node.allow is None:
                continue
            allow = set(node.allow) if allow is None else allow & set(node.allow)
        deny = set()
        for node in nodes:
            deny.update(node.deny)
        levels_set = [node.classification for node in nodes if node.classification is not None]
        merged.append({"allow": None if allow is None else tuple(allow), "deny": deny,
                       "inherit": all(node.inherit for node in nodes),
                       "classification": max(levels_set) if levels_set else None})
    deny = set()
    for level in merged:
        deny.update(level["deny"])
    restricted, allow = False, ()
    for level in reversed(merged):
        if level["allow"] is not None:
            restricted, allow = True, level["allow"]
            break
        if not level["inherit"]:
            restricted, allow = True, ()
            break
    classes = [level["classification"] for level in merged if level["classification"] is not None]
    classification = max(classes) if classes else default_classification
    return Effective(restricted, allow, deny, classification, sources)


class Access:
    """What one caller is: principals, clearance, and whether the group and
    role memberships are actually known."""

    def __init__(self, principals, clearance, membership_known, enforced=True,
                 default_classification=1):
        found = [EVERYONE]
        for principal in principals or ():
            if principal and principal not in found:
                found.append(principal)
        self.principals = tuple(found)
        self.clearance = clearance
        self.membership_known = bool(membership_known)
        self.enforced = bool(enforced)
        self.default_classification = default_classification

    def as_rag(self):
        """The filter the RAG applies; None means "no ACL filter"."""
        if not self.enforced:
            return None
        return {"principals": sorted(self.principals), "clearance": self.clearance,
                "membership_known": self.membership_known,
                "default_classification": self.default_classification}

    def to_dict(self):
        return {"principals": list(self.principals), "clearance": self.clearance,
                "membership_known": self.membership_known, "enforced": self.enforced,
                "default_classification": self.default_classification}

    @classmethod
    def from_dict(cls, data):
        """Rebuild from a job record. Anything malformed yields an access
        that sees only unrestricted, lowest-level documents."""
        if not isinstance(data, dict):
            return None
        raw = data.get("principals")
        raw = raw if isinstance(raw, list) else []
        principals = [p for p in (normalize_principal(item) for item in raw) if p]
        clearance = parse_level(data.get("clearance"))
        default = parse_level(data.get("default_classification"))
        return cls(principals, 0 if clearance is None else clearance,
                   data.get("membership_known") is True,
                   enforced=data.get("enforced") is not False,
                   default_classification=TOP_LEVEL if default is None else default)

    def fingerprint(self):
        blob = json.dumps(self.to_dict(), sort_keys=True).encode("utf-8")
        return hashlib.sha1(blob).hexdigest()[:16]


def decide(effective, access):
    """(allowed, reason). The reason is for logs and the admin view only."""
    if access is None:
        return False, "no access context"
    if not access.enforced:
        return True, "acl off"
    if effective is None:
        return False, "no ACL could be computed"
    if effective.invalid:
        return False, "invalid ACL"
    if effective.classification > access.clearance:
        return False, "classification %s above clearance %s" % (
            level_name(effective.classification), level_name(access.clearance))
    principals = set(access.principals)
    for principal in effective.deny:
        if principal in principals:
            return False, "denied: %s" % principal
        if not access.membership_known and principal.startswith(("group:", "role:")):
            return False, "denied: membership unknown, %s may apply" % principal
    if effective.restricted:
        matched = sorted(principals.intersection(effective.allow))
        if not matched:
            return False, "not in allow list"
        return True, "allowed: %s" % matched[0]
    return True, "allowed: no allow list (drive-level access)"


def _ancestors(path):
    """The room (drive root) and every folder down to `path`, or None when the
    path is not under a drive or is not canonical. `.`, `..` and empty
    segments are refused: `/a/./secret` names the same file as `/a/secret` on
    the box but would match none of its ACLs here."""
    p = corpus.norm(path or "")
    if not p or not p.startswith("/") or any(part in ("", ".", "..") for part in p[1:].split("/")):
        return None
    root = corpus.drive_root_of_path(p)
    if root is None:
        return None
    chain = [root]
    rest = p[len(root):].strip("/")
    current = root
    for part in rest.split("/") if rest else ():
        current = current + "/" + part
        chain.append(current)
    return chain


class RulesError(Exception):
    pass


def _parse_rules(raw, warn):
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, dict):
        raise RulesError("rules file is not a JSON object")
    paths = {}
    for path, node in (data.get("paths") or {}).items():
        key = corpus.norm(str(path))
        if _ancestors(key) is None:
            warn("ignoring ACL rule outside the drive prefix: %r" % path)
            continue
        paths[key] = parse_node(node, source="rules")
    clearance = {}
    for principal, level in (data.get("clearance") or {}).items():
        index = parse_level(level)
        if index is None:
            warn("ignoring clearance %r for %r: unknown level" % (level, principal))
            continue
        if str(principal).strip().casefold() == "default":
            clearance["default"] = index
            continue
        key = normalize_principal(principal)
        if key is None:
            warn("ignoring clearance for malformed principal %r" % principal)
            continue
        clearance[key] = index
    default_classification = None
    if data.get("default_classification") not in (None, ""):
        default_classification = parse_level(data.get("default_classification"))
        if default_classification is None:
            warn("unknown default_classification %r -> %s"
                 % (data.get("default_classification"), LEVELS[TOP_LEVEL]))
            default_classification = TOP_LEVEL
    return {"paths": paths, "clearance": clearance,
            "default_classification": default_classification,
            "fingerprint": hashlib.sha1(raw).hexdigest()[:16]}


class Policy:
    def __init__(self, mode=MODE_ENFORCE, rules_path=None, default_classification=1,
                 default_clearance=1, warn=None):
        self.mode = mode
        self.rules_path = rules_path or None
        self._default_classification = default_classification
        self._default_clearance = default_clearance
        self._warn = warn or (lambda message: sys.stderr.write("[adapter] acl: %s\n" % message))
        self._lock = threading.Lock()
        self._mtime = None
        self._rules = {"paths": {}, "clearance": {}, "default_classification": None,
                       "fingerprint": "none"}
        self._rules_error = None

    @classmethod
    def from_env(cls, env=None, warn=None):
        env = env if env is not None else os.environ
        warn = warn or (lambda message: sys.stderr.write("[adapter] acl: %s\n" % message))
        raw_mode = (env.get("ADAPTER_ACL") or "").strip().lower()
        mode = MODE_ENFORCE
        if raw_mode in MODES:
            mode = raw_mode
        elif raw_mode:
            warn("unknown ADAPTER_ACL %r -> %s" % (raw_mode, MODE_ENFORCE))

        def level(name, default, unknown):
            raw = (env.get(name) or "").strip()
            if not raw:
                return default
            index = parse_level(raw)
            if index is None:
                warn("unknown %s=%r -> %s" % (name, raw, LEVELS[unknown]))
                return unknown
            return index

        return cls(mode=mode,
                   rules_path=(env.get("ADAPTER_ACL_RULES") or "").strip() or None,
                   default_classification=level("ADAPTER_ACL_DEFAULT_CLASSIFICATION", 1, TOP_LEVEL),
                   default_clearance=level("ADAPTER_ACL_DEFAULT_CLEARANCE", 1, 0),
                   warn=warn)

    @property
    def enforced(self):
        return self.mode != MODE_OFF

    # -- rules file -------------------------------------------------------------
    def _current_rules(self):
        """The parsed rules, or None when a configured file is unusable."""
        if not self.rules_path:
            return self._rules
        with self._lock:
            try:
                mtime = os.path.getmtime(self.rules_path)
            except OSError as e:
                if self._rules_error is None or self._mtime is not None:
                    self._warn("rules file unreadable (%s): every path is denied" % e.strerror)
                self._mtime, self._rules_error = None, "unreadable"
                return None
            if mtime != self._mtime:
                self._mtime = mtime
                try:
                    with open(self.rules_path, "rb") as handle:
                        self._rules = _parse_rules(handle.read(), self._warn)
                    self._rules_error = None
                except (OSError, ValueError, RulesError) as e:
                    self._warn("rules file invalid (%s): every path is denied" % e)
                    self._rules_error = "invalid"
            return None if self._rules_error else self._rules

    @property
    def default_classification(self):
        rules = self._current_rules()
        if rules is None:
            return TOP_LEVEL
        if rules.get("default_classification") is not None:
            return rules["default_classification"]
        return self._default_classification

    def fingerprint(self):
        """Changes whenever the flattened ACL of some path may change for a
        reason other than sync metadata (rules file, defaults, mode)."""
        rules = self._current_rules()
        part = "error:%s" % self._rules_error if rules is None else rules["fingerprint"]
        return "%s|%s|%s" % (self.mode, part, self.default_classification)

    def settings(self):
        rules = self._current_rules()
        return {"mode": self.mode, "rules": bool(self.rules_path),
                "rules_error": self._rules_error,
                "rule_paths": 0 if rules is None else len(rules["paths"]),
                "default_classification": level_name(self.default_classification),
                "default_clearance": level_name(self._default_clearance)}

    # -- documents --------------------------------------------------------------
    def effective(self, path, sync_lookup=None, cache=None):
        """The flattened ACL of `path`. `sync_lookup(path)` returns the stored
        sync ACL of one node; `cache` (a dict) memoizes nodes across calls."""
        chain = _ancestors(path)
        if chain is None:
            return Effective.deny_all("path outside the drive prefix")
        rules = self._current_rules()
        if rules is None:
            return Effective.deny_all("ACL rules file unusable")
        levels = []
        for node_path in chain:
            nodes = None if cache is None else cache.get(node_path)
            if nodes is None:
                nodes = [rules["paths"].get(node_path)]
                if sync_lookup is not None:
                    nodes.append(parse_node(sync_lookup(node_path)))
                if cache is not None:
                    cache[node_path] = nodes
            levels.append((node_path, nodes))
        return flatten(levels, self.default_classification)

    def allows(self, path, access, sync_lookup=None, cache=None):
        if access is not None and not access.enforced:
            return True
        return decide(self.effective(path, sync_lookup, cache), access)[0]

    def checker(self, access, sync_lookup=None):
        """`path -> bool` for many paths of one request, sharing a node cache."""
        cache = {}
        return lambda path: self.allows(path, access, sync_lookup, cache)

    # -- callers ----------------------------------------------------------------
    def access_for(self, user=None, upn=None, directory_info=None):
        """The access context of one caller. Group and role principals come
        only from a directory lookup without error."""
        principals = []
        for ident in (user, upn):
            principal = normalize_principal("user:%s" % ident) if ident else None
            if principal:
                principals.append(principal)
        known = (directory_info is not None and directory_info.error is None
                 and directory_info.source != "off")
        if known:
            for group in directory_info.groups:
                principal = normalize_principal("group:%s" % group)
                if principal:
                    principals.append(principal)
            for role in directory_info.roles:
                principal = normalize_principal("role:%s" % role)
                if principal:
                    principals.append(principal)
        rules = self._current_rules()
        clearance = self._default_clearance
        if rules is None:
            clearance = 0
        else:
            mapping = rules["clearance"]
            clearance = mapping.get("default", clearance)
            for principal in principals:
                if principal in mapping:
                    clearance = max(clearance, mapping[principal])
        return Access(principals, clearance, known, enforced=self.enforced,
                      default_classification=self.default_classification)

    def explain(self, path, access, sync_lookup=None):
        effective = self.effective(path, sync_lookup)
        allowed, reason = decide(effective, access)
        return {"path": corpus.norm(path or ""), "allowed": allowed, "reason": reason,
                "effective": effective.as_dict()}
