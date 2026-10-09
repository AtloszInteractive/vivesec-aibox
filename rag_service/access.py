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
    return {"restricted": restricted,
            "allow": _principals(raw.get("allow") or [], "acl.allow"),
            "deny": _principals(raw.get("deny") or [], "acl.deny"),
            "classification": _level(raw.get("classification"), "acl.classification")}


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
    if doc_acl.get("restricted"):
        return bool(principals.intersection(doc_acl.get("allow") or ()))
    return True


def sql_clause(access, alias="d"):
    """(sql, params) restricting `documents AS alias`; ("1", []) without a
    filter. Expects a `doc_acl(doc_id, effect, principal)` table."""
    if access is None:
        return "1", []
    principals = list(access["principals"]) or [""]
    marks = ",".join("?" * len(principals))
    deny_match = "a.principal IN (%s)" % marks
    if not access["membership_known"]:
        deny_match = "(%s OR substr(a.principal,1,6)='group:' OR substr(a.principal,1,5)='role:')" \
                     % deny_match
    sql = ("COALESCE({d}.classification, ?) <= ? "
           "AND NOT EXISTS (SELECT 1 FROM doc_acl a WHERE a.doc_id={d}.doc_id "
           "AND a.effect='deny' AND {deny}) "
           "AND (COALESCE({d}.acl_restricted, 0)=0 OR EXISTS (SELECT 1 FROM doc_acl a "
           "WHERE a.doc_id={d}.doc_id AND a.effect='allow' AND a.principal IN ({marks})))"
           ).format(d=alias, deny=deny_match, marks=marks)
    params = [access["default_classification"], access["clearance"]] + principals + principals
    return sql, params
