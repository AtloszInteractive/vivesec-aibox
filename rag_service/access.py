"""Document-level access filter for retrieval (E03).

The adapter owns the access model (adapter/acl.py) and sends two flattened
shapes here:

  document ACL  {"restricted": bool, "allow": [principal], "deny": [principal],
                 "classification": int}
                stored per document at sync time;
  access        {"principals": [principal], "clearance": int,
                 "membership_known": bool, "default_classification": int}
                sent with every query.

A document is visible when its classification is within the clearance, none
of its denies matches (with unknown membership every group/role deny counts
as a match), and -- when it is restricted -- one of its allows matches. The
SQL clause and the Python predicate below implement exactly that rule; the
filter is applied INSIDE the queries, so neither hits, counts nor metadata of
a hidden document leave the store.

A structured record (E29: the open text of an intelligence log or ODIN entry)
carries two more, optional keys:

  require                the record's own rule, an AND gate on top of the rest:
                         one of these principals must match as well -- a
                         group/role only when the membership is known; an
                         empty list admits nobody (fail closed);
  require_classification its own level, checked against the clearance too;
  follows                the folder whose ACL the record takes over: an ACL
                         update of that folder reaches the record and leaves
                         `require` as it is (store.update_acl).

An ACL update that does not name `require` / `follows` keeps the stored ones.
"""

MAX_PRINCIPALS = 512
MAX_LEVEL = 4
_GROUPISH = ("group:", "role:")


def _principals(raw, name):
    if not isinstance(raw, list) or len(raw) > MAX_PRINCIPALS:
        raise ValueError("%s must be a list of at most %d principals" % (name, MAX_PRINCIPALS))
    out = []
    for item in raw:
        if not isinstance(item, str) or not item.strip() or len(item) > 300:
            raise ValueError("%s must contain nonempty strings" % name)
        value = item.strip().casefold()
        if value not in out:
            out.append(value)
    return out


def _level(raw, name):
    if isinstance(raw, bool) or not isinstance(raw, int) or not 0 <= raw <= MAX_LEVEL:
        raise ValueError("%s must be an integer level 0..%d" % (name, MAX_LEVEL))
    return raw


def parse_access(raw):
    """Validated access filter, None when the caller sent none."""
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("access must be an object")
    membership = raw.get("membership_known")
    if not isinstance(membership, bool):
        raise ValueError("access.membership_known must be a boolean")
    return {"principals": _principals(raw.get("principals"), "access.principals"),
            "clearance": _level(raw.get("clearance"), "access.clearance"),
            "membership_known": membership,
            "default_classification": _level(raw.get("default_classification"),
                                             "access.default_classification")}


def parse_doc_acl(raw):
    """Validated document ACL, None when the caller sent none."""
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("acl must be an object")
    restricted = raw.get("restricted")
    if not isinstance(restricted, bool):
        raise ValueError("acl.restricted must be a boolean")
    out = {"restricted": restricted,
           "allow": _principals(raw.get("allow") or [], "acl.allow"),
           "deny": _principals(raw.get("deny") or [], "acl.deny"),
           "classification": _level(raw.get("classification"), "acl.classification")}
    if raw.get("require") is not None:
        out["require"] = _principals(raw["require"], "acl.require")
        out["require_classification"] = _level(raw.get("require_classification", 0),
                                               "acl.require_classification")
    elif raw.get("require_classification") is not None:
        raise ValueError("acl.require_classification needs acl.require")
    if raw.get("follows") is not None:
        follows = raw["follows"]
        if not isinstance(follows, str) or not follows.startswith("/") or len(follows) > 2000 \
                or ".." in follows.split("/"):
            raise ValueError("acl.follows must be an absolute folder path without '..'")
        out["follows"] = follows.rstrip("/") or "/"
    return out


OWN_KEYS = ("require", "require_classification", "follows")


def own_rule(doc_acl):
    """The keys of a document ACL an update of its folder leaves alone."""
    return {k: doc_acl[k] for k in OWN_KEYS if doc_acl and k in doc_acl}


def updated_acl(new, old, follower=False):
    """The ACL a document gets from an update `new` when it had `old`: what the
    update does not name of its own rule stays; a follower (a record taking
    over its folder's ACL) never takes the folder's own keys."""
    out = {k: v for k, v in new.items() if not (follower and k in OWN_KEYS)}
    kept = own_rule(old)
    if "require" not in out:
        out.update({k: kept[k] for k in ("require", "require_classification") if k in kept})
    if "follows" not in out and "follows" in kept:
        out["follows"] = kept["follows"]
    return out


def require_principals(access):
    """The caller's principals a `require` list may match: a group or role only
    from a known membership."""
    return [p for p in access["principals"]
            if access["membership_known"] or not p.startswith(_GROUPISH)]


def doc_allowed(doc_acl, access):
    """Python twin of `sql_clause` (JSON store)."""
    if access is None:
        return True
    doc_acl = doc_acl or {}
    level = doc_acl.get("classification")
    if level is None:
        level = access["default_classification"]
    if level > access["clearance"]:
        return False
    principals = set(access["principals"])
    for principal in doc_acl.get("deny") or ():
        if principal in principals:
            return False
        if not access["membership_known"] and principal.startswith(_GROUPISH):
            return False
    if doc_acl.get("restricted") and not principals.intersection(doc_acl.get("allow") or ()):
        return False
    if doc_acl.get("require") is not None:
        if doc_acl.get("require_classification", 0) > access["clearance"]:
            return False
        return bool(set(require_principals(access)).intersection(doc_acl["require"]))
    return True


def sql_clause(access, alias="d"):
    """(sql, params) restricting `documents AS alias`; ("1", []) without a
    filter. Expects a `doc_acl(doc_id, effect, principal)` table and the
    documents column `acl_require` (NULL: no gate, else the gate's level)."""
    if access is None:
        return "1", []
    principals = list(access["principals"]) or [""]
    marks = ",".join("?" * len(principals))
    required = require_principals(access) or [""]
    deny_match = "a.principal IN (%s)" % marks
    if not access["membership_known"]:
        deny_match = "(%s OR substr(a.principal,1,6)='group:' OR substr(a.principal,1,5)='role:')" \
                     % deny_match
    sql = ("COALESCE({d}.classification, ?) <= ? "
           "AND NOT EXISTS (SELECT 1 FROM doc_acl a WHERE a.doc_id={d}.doc_id "
           "AND a.effect='deny' AND {deny}) "
           "AND (COALESCE({d}.acl_restricted, 0)=0 OR EXISTS (SELECT 1 FROM doc_acl a "
           "WHERE a.doc_id={d}.doc_id AND a.effect='allow' AND a.principal IN ({marks}))) "
           "AND ({d}.acl_require IS NULL OR ({d}.acl_require <= ? AND EXISTS (SELECT 1 "
           "FROM doc_acl a WHERE a.doc_id={d}.doc_id AND a.effect='require' "
           "AND a.principal IN ({req}))))"
           ).format(d=alias, deny=deny_match, marks=marks,
                    req=",".join("?" * len(required)))
    params = [access["default_classification"], access["clearance"]] + principals + principals \
        + [access["clearance"]] + required
    return sql, params
