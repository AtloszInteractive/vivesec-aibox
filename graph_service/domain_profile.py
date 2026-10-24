"""Customer profile: the configurable layer on top of the fixed core (E26).

A profile is a JSON document that an administrator edits (later through the
admin UI). Industry templates live in profiles/. Validation is strict and
fails closed: an unknown key, type or malformed pattern rejects the whole
profile instead of being silently ignored.
"""
from __future__ import annotations

import copy
import json
import os
import re

from model import CORE_TYPES

LANGS = ("en", "hu", "da", "de")
NORMALIZERS = ("none", "digits", "upper", "lower", "nospace_upper", "phone_hu")
CO_WINDOWS = ("sentence", "paragraph", "document")
TABLE_ROLES = ("entity", "attribute", "date", "end_date", "relation_label", "ignore")
# Always present, never overridable: the generic fallback and the co-mention edge.
BUILTIN_RELATIONS = {
    "RELATED_TO": {"id": "RELATED_TO", "label": {"en": "Related to", "hu": "Kapcsolatban áll",
                                                 "da": "Relateret til", "de": "Verbunden mit"},
                   "from": list(CORE_TYPES), "to": list(CORE_TYPES), "symmetric": True,
                   "presence": False, "holding": False, "synonyms": []},
    "CO_MENTIONED": {"id": "CO_MENTIONED", "label": {"en": "Mentioned together",
                                                     "hu": "Együtt említve",
                                                     "da": "Nævnt sammen",
                                                     "de": "Gemeinsam erwähnt"},
                     "from": list(CORE_TYPES), "to": list(CORE_TYPES), "symmetric": True,
                     "presence": False, "holding": False, "synonyms": []},
}
_ID = re.compile(r"^[a-z][a-z0-9_]{0,40}$")
_REL_ID = re.compile(r"^[A-Z][A-Z0-9_]{1,40}$")
PROFILES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "profiles")


class ProfileError(ValueError):
    pass


def _keys(obj, allowed, where):
    if not isinstance(obj, dict):
        raise ProfileError("%s must be an object" % where)
    unknown = set(obj) - set(allowed)
    if unknown:
        raise ProfileError("%s: unknown keys %s" % (where, sorted(unknown)))


def _label(raw, where):
    if not isinstance(raw, dict) or not isinstance(raw.get("en"), str) or not raw["en"].strip():
        raise ProfileError("%s.label needs at least an English text" % where)
    for lang, text in raw.items():
        if lang not in LANGS or not isinstance(text, str):
            raise ProfileError("%s.label: unsupported language %r" % (where, lang))
    return dict(raw)


def _types(raw, where):
    if not isinstance(raw, list) or not raw or any(t not in CORE_TYPES for t in raw):
        raise ProfileError("%s must be a nonempty list of %s" % (where, CORE_TYPES))
    return list(raw)


def reversed_by_subtypes(spec: dict, sub_a: str, sub_b: str) -> bool:
    """True when a relation read a -> b only fits its profile the other way round
    (an inmate "visited" a visitor). An unknown subtype fits either end."""
    first, second = spec.get("from_subtypes") or [], spec.get("to_subtypes") or []
    if not first and not second:
        return False

    def fits(x, y):
        return (not first or not x or x in first) and (not second or not y or y in second)
    return not fits(sub_a, sub_b) and fits(sub_b, sub_a)


def relation_line(r: dict) -> str:
    """One relation type for an extraction prompt, with the subtype that acts first."""
    def side(types, subs):
        text = "/".join(types)
        return "%s (%s)" % (text, "/".join(subs)) if subs else text
    return "- %s: %s -> %s. %s%s" % (
        r["id"], side(r["from"], r.get("from_subtypes")), side(r["to"], r.get("to_subtypes")),
        r["label"]["en"],
        (" Typical wording: " + ", ".join(r["synonyms"][:8])) if r["synonyms"] else "")


def validate(raw: dict) -> dict:
    _keys(raw, ("id", "version", "name", "description", "entity_subtypes", "identifier_patterns",
                "relation_types", "resolution", "cooccurrence", "table_mappings"), "profile")
    if not isinstance(raw.get("id"), str) or not _ID.match(raw["id"]):
        raise ProfileError("profile.id must be a lowercase identifier")
    if not isinstance(raw.get("version"), int) or raw["version"] < 1:
        raise ProfileError("profile.version must be a positive integer")
    out = {"id": raw["id"], "version": raw["version"], "name": _label(raw.get("name"), "profile"),
           "description": str(raw.get("description", ""))}

    subtypes = {}
    for i, s in enumerate(raw.get("entity_subtypes", [])):
        where = "entity_subtypes[%d]" % i
        _keys(s, ("id", "type", "label"), where)
        if not isinstance(s.get("id"), str) or not _ID.match(s["id"]) or s["id"] in subtypes:
            raise ProfileError("%s.id must be a unique lowercase identifier" % where)
        if s.get("type") not in CORE_TYPES:
            raise ProfileError("%s.type must be one of %s" % (where, CORE_TYPES))
        subtypes[s["id"]] = {"id": s["id"], "type": s["type"], "label": _label(s.get("label"), where)}
    out["entity_subtypes"] = list(subtypes.values())

    patterns = {}
    for i, p in enumerate(raw.get("identifier_patterns", [])):
        where = "identifier_patterns[%d]" % i
        _keys(p, ("id", "subtype", "label", "regex", "normalize"), where)
        if not isinstance(p.get("id"), str) or not _ID.match(p["id"]) or p["id"] in patterns:
            raise ProfileError("%s.id must be a unique lowercase identifier" % where)
        sub = subtypes.get(p.get("subtype"))
        if not sub or sub["type"] != "identifier":
            raise ProfileError("%s.subtype must name an identifier subtype" % where)
        try:
            re.compile(p.get("regex", ""))
        except (re.error, TypeError) as e:
            raise ProfileError("%s.regex: %s" % (where, e))
        if not p.get("regex"):
            raise ProfileError("%s.regex is required" % where)
        norm = p.get("normalize", "none")
        if norm not in NORMALIZERS:
            raise ProfileError("%s.normalize must be one of %s" % (where, NORMALIZERS))
        patterns[p["id"]] = {"id": p["id"], "subtype": p["subtype"],
                             "label": _label(p.get("label"), where), "regex": p["regex"],
                             "normalize": norm}
    out["identifier_patterns"] = list(patterns.values())

    relations = copy.deepcopy(BUILTIN_RELATIONS)
    for i, r in enumerate(raw.get("relation_types", [])):
        where = "relation_types[%d]" % i
        _keys(r, ("id", "label", "from", "to", "symmetric", "presence", "holding", "synonyms",
                  "from_subtypes", "to_subtypes"), where)
        # A saved profile echoes the built-ins back; accept them only unchanged.
        if r.get("id") in BUILTIN_RELATIONS and r == BUILTIN_RELATIONS[r["id"]] \
                and i == list(BUILTIN_RELATIONS).index(r["id"]):
            continue
        if not isinstance(r.get("id"), str) or not _REL_ID.match(r["id"]) or r["id"] in relations:
            raise ProfileError("%s.id must be a unique UPPER_CASE identifier" % where)
        synonyms = r.get("synonyms", [])
        if not isinstance(synonyms, list) or any(not isinstance(x, str) for x in synonyms):
            raise ProfileError("%s.synonyms must be a list of strings" % where)
        relations[r["id"]] = {"id": r["id"], "label": _label(r.get("label"), where),
                              "from": _types(r.get("from"), where + ".from"),
                              "to": _types(r.get("to"), where + ".to"),
                              "symmetric": bool(r.get("symmetric", False)),
                              "presence": bool(r.get("presence", False)),
                              "holding": bool(r.get("holding", False)), "synonyms": synonyms}
        for side in ("from", "to"):
            # Which subtype does what: a visit goes from the visitor to the inmate.
            key = side + "_subtypes"
            ids = r.get(key)
            if ids is None:
                continue
            if not isinstance(ids, list) or not ids or any(
                    not isinstance(x, str) or subtypes.get(x, {}).get("type")
                    not in relations[r["id"]][side] for x in ids):
                raise ProfileError("%s.%s must list subtypes of its %s types"
                                   % (where, key, side))
            relations[r["id"]][key] = list(ids)
    out["relation_types"] = list(relations.values())

    resolution = {}
    for type_, rule in (raw.get("resolution") or {}).items():
        where = "resolution.%s" % type_
        if type_ not in CORE_TYPES:
            raise ProfileError("%s: unknown entity type" % where)
        _keys(rule, ("strong_keys", "match_fields", "auto_merge", "review"), where)
        auto, review = rule.get("auto_merge", 0.95), rule.get("review", 0.7)
        if not all(isinstance(x, (int, float)) for x in (auto, review)) or not 0 < review <= auto <= 1:
            raise ProfileError("%s: need 0 < review <= auto_merge <= 1" % where)
        resolution[type_] = {"strong_keys": list(rule.get("strong_keys", [])),
                             "match_fields": list(rule.get("match_fields", [])),
                             "auto_merge": float(auto), "review": float(review)}
    out["resolution"] = resolution

    co = raw.get("cooccurrence") or {}
    _keys(co, ("window", "max_entities"), "cooccurrence")
    window, cap = co.get("window", "sentence"), co.get("max_entities", 25)
    if window not in CO_WINDOWS or not isinstance(cap, int) or not 2 <= cap <= 500:
        raise ProfileError("cooccurrence needs window in %s and 2 <= max_entities <= 500"
                           % (CO_WINDOWS,))
    out["cooccurrence"] = {"window": window, "max_entities": cap}

    # Pinned table layouts (table_mapping.py): header -> column roles + row relations.
    mappings = raw.get("table_mappings") or {}
    if not isinstance(mappings, dict):
        raise ProfileError("table_mappings must be an object")
    rel_ids = {r["id"] for r in out["relation_types"]}
    pinned = {}
    for name, m in mappings.items():
        where = "table_mappings.%s" % name
        if not isinstance(m, dict):
            raise ProfileError("%s must be an object" % where)
        _keys(m, ("header", "columns", "relations"), where)
        header = m.get("header")
        if not isinstance(header, list) or not header or not all(
                isinstance(h, str) and h.strip() for h in header):
            raise ProfileError("%s.header must be a list of column names" % where)
        columns = []
        for c in m.get("columns") or []:
            _keys(c, ("column", "role", "entity_type", "subtype", "attribute", "of"),
                  where + ".columns")
            if c.get("column") not in header or c.get("role") not in TABLE_ROLES:
                raise ProfileError("%s.columns: unknown column or role" % where)
            if c["role"] == "entity" and c.get("entity_type") not in CORE_TYPES:
                raise ProfileError("%s.columns: entity needs an entity_type" % where)
            columns.append(dict(c))
        relations = []
        for r in m.get("relations") or []:
            _keys(r, ("type", "from", "to"), where + ".relations")
            if r.get("type") not in rel_ids or r.get("from") not in header \
                    or r.get("to") not in header:
                raise ProfileError("%s.relations: unknown type or column" % where)
            relations.append(dict(r))
        pinned[name] = {"header": list(header), "columns": columns, "relations": relations}
    if pinned:
        out["table_mappings"] = pinned
    return out


def load(path_or_id: str) -> dict:
    """A profile file path (*.json) or the id of a bundled template."""
    path = path_or_id if path_or_id.endswith(".json") \
        else os.path.join(PROFILES_DIR, path_or_id + ".json")
    with open(path, encoding="utf-8") as f:
        return validate(json.load(f))


def templates() -> list[dict]:
    out = []
    for name in sorted(os.listdir(PROFILES_DIR)):
        if name.endswith(".json"):
            p = load(os.path.join(PROFILES_DIR, name))
            out.append({"id": p["id"], "version": p["version"], "name": p["name"],
                        "description": p["description"]})
    return out


def normalize_value(value: str, how: str) -> str:
    if how == "digits":
        return "".join(c for c in value if c.isdigit())
    if how == "upper":
        return value.upper()
    if how == "lower":
        return value.lower()
    if how == "nospace_upper":
        return "".join(value.split()).upper()
    if how == "phone_hu":
        digits = "".join(c for c in value if c.isdigit())
        if digits.startswith("06"):
            digits = "36" + digits[2:]
        return "+" + digits
    return value
