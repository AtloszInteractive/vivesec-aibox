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


def _flag(obj, key, where) -> bool:
    value = obj.get(key, False)
    if not isinstance(value, bool):
        raise ProfileError("%s.%s must be true or false" % (where, key))
    return value


SCOPED_TYPES = ("place", "organization")
# Raised when extraction itself changes what it produces from the same text (not
# the profile): documents extracted before then count as stale and can be
# reprocessed. 2: places scoped by their container, common nouns kept local;
# 3: also common nouns with digits (an untyped "102. számú zárka");
# 4: "Label: value" forms read by a learnt mapping, minified model output;
# 5: an entity's subtype kept per document (a reader sees only what their
#    documents say), so older documents must be read again to show one.
EXTRACTOR_REVISION = 5


def profile_tag(profile: dict) -> str:
    """"bv@3/e3": the profile and extractor revision a document is extracted with."""
    return "%s@%s/e%d" % (profile["id"], profile["version"], EXTRACTOR_REVISION)


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
                "relation_types", "resolution", "cooccurrence", "table_mappings",
                "structured_sources"), "profile")
    if not isinstance(raw.get("id"), str) or not _ID.match(raw["id"]):
        raise ProfileError("profile.id must be a lowercase identifier")
    if not isinstance(raw.get("version"), int) or raw["version"] < 1:
        raise ProfileError("profile.version must be a positive integer")
    out = {"id": raw["id"], "version": raw["version"], "name": _label(raw.get("name"), "profile"),
           "description": str(raw.get("description", ""))}

    subtypes = {}
    for i, s in enumerate(raw.get("entity_subtypes", [])):
        where = "entity_subtypes[%d]" % i
        _keys(s, ("id", "type", "label", "within", "structured_only"), where)
        if not isinstance(s.get("id"), str) or not _ID.match(s["id"]) or s["id"] in subtypes:
            raise ProfileError("%s.id must be a unique lowercase identifier" % where)
        if s.get("type") not in CORE_TYPES:
            raise ProfileError("%s.type must be one of %s" % (where, CORE_TYPES))
        subtypes[s["id"]] = {"id": s["id"], "type": s["type"], "label": _label(s.get("label"), where)}
        if "within" in s:
            subtypes[s["id"]]["within"] = s["within"]
        if _flag(s, "structured_only", where):
            subtypes[s["id"]]["structured_only"] = True
    # within: a place known only inside a larger one (a cell inside an institution).
    # Its identity then includes that container, so cell 102 of two prisons are
    # two places; without a container in the document it stays document-local.
    for i, s in enumerate(subtypes.values()):
        if "within" not in s:
            continue
        where = "entity_subtypes[%d].within" % i
        within = s["within"]
        if s["type"] not in SCOPED_TYPES or not isinstance(within, list) or not within \
                or any(not isinstance(x, str) or x == s["id"] or x not in subtypes
                       or subtypes[x]["type"] not in SCOPED_TYPES for x in within):
            raise ProfileError("%s must list other place or organization subtypes (and the "
                               "subtype itself must be a place or organization)" % where)
        s["within"] = list(within)
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
                  "from_subtypes", "to_subtypes", "structured_only"), where)
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
        if _flag(r, "structured_only", where):
            relations[r["id"]]["structured_only"] = True
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
        _keys(m, ("header", "columns", "relations", "form"), where)
        if not isinstance(m.get("form", False), bool):
            raise ProfileError("%s.form must be true or false" % where)
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
        if m.get("form"):
            pinned[name]["form"] = True
    if pinned:
        out["table_mappings"] = pinned
    sources = _structured_sources(raw.get("structured_sources"), out)
    if sources:
        out["structured_sources"] = sources
    return out


# -- structured sources (periodic exports, structured_import.py) --------------------

ATTRIBUTE_NAMES = ("birth_date", "birth_place", "mother_name", "id_number", "registry_number")
SOURCE_MODES = ("delta", "full")
_PRINCIPAL = re.compile(r"^(user|group|role):[^\s]{1,200}$")


def _column(raw, where, required=True):
    if raw is None and not required:
        return None
    if not isinstance(raw, str) or not raw.strip() or len(raw) > 120:
        raise ProfileError("%s must name a column" % where)
    return raw.strip()


def _principals(raw, where):
    if not isinstance(raw, list) or any(not isinstance(p, str) or not _PRINCIPAL.match(p.strip())
                                        for p in raw):
        raise ProfileError("%s must be a list of principals (user:/group:/role:...)" % where)
    return [p.strip().casefold() for p in raw]


def _structured_sources(raw, profile: dict) -> list[dict]:
    """Periodic exports (CSV/XLSX) read by rule, never by the model: which column
    is the record id, which columns make which entity and relation, the code
    lists that turn codes into text, and the record's own access rule. Strict
    like the rest: an unknown key, column role, relation or subtype rejects the
    whole profile."""
    if raw is None:
        return []
    if not isinstance(raw, list) or len(raw) > 50:
        raise ProfileError("structured_sources must be a list of at most 50 sources")
    subtypes = {s["id"]: s for s in profile["entity_subtypes"]}
    rels = {r["id"]: r for r in profile["relation_types"]}
    out, seen = [], set()
    for i, s in enumerate(raw):
        where = "structured_sources[%d]" % i
        _keys(s, ("id", "label", "title", "record_id", "mode", "deleted", "sheet", "codelists",
                  "entities", "relations", "acl", "full_max_delete"), where)
        if not isinstance(s.get("id"), str) or not _ID.match(s["id"]) or s["id"] in seen:
            raise ProfileError("%s.id must be a unique lowercase identifier" % where)
        seen.add(s["id"])
        src = {"id": s["id"], "label": _label(s.get("label"), where)}
        title = s.get("title", "")
        if not isinstance(title, str) or len(title) > 120:
            raise ProfileError("%s.title must be a short text" % where)
        src["title"] = title.strip() or src["label"]["en"]
        ids = s.get("record_id")
        ids = [ids] if isinstance(ids, str) else ids
        if not isinstance(ids, list) or not 1 <= len(ids) <= 5:
            raise ProfileError("%s.record_id must name 1..5 columns" % where)
        src["record_id"] = [_column(c, where + ".record_id") for c in ids]
        mode = s.get("mode", "delta")
        if mode not in SOURCE_MODES:
            raise ProfileError("%s.mode must be one of %s" % (where, SOURCE_MODES))
        src["mode"] = mode
        share = s.get("full_max_delete", 0.5)
        if isinstance(share, bool) or not isinstance(share, (int, float)) or not 0 <= share <= 1:
            raise ProfileError("%s.full_max_delete must be between 0 and 1" % where)
        src["full_max_delete"] = float(share)
        if s.get("deleted") is not None:
            d = s["deleted"]
            _keys(d, ("column", "values"), where + ".deleted")
            values = d.get("values")
            if not isinstance(values, list) or not values \
                    or any(not isinstance(v, str) or not v.strip() for v in values):
                raise ProfileError("%s.deleted.values must list the marks of a deleted record"
                                   % where)
            src["deleted"] = {"column": _column(d.get("column"), where + ".deleted.column"),
                              "values": [v.strip() for v in values]}
        if s.get("sheet") is not None:
            src["sheet"] = _column(s["sheet"], where + ".sheet")
        codelists = s.get("codelists") or {}
        if not isinstance(codelists, dict) or any(
                not _ID.match(str(k)) or not isinstance(v, dict) or len(v) > 5000
                or any(not isinstance(c, str) or not isinstance(t, str) or not t.strip()
                       for c, t in v.items()) for k, v in codelists.items()):
            raise ProfileError("%s.codelists must map list ids to {code: text}" % where)
        src["codelists"] = {k: {c.strip(): t.strip() for c, t in v.items()}
                            for k, v in codelists.items()}

        def codelist(name, at):
            if name is None:
                return None
            if name not in src["codelists"]:
                raise ProfileError("%s: unknown code list %r" % (at, name))
            return name

        ents = {}
        raw_ents = s.get("entities")
        if not isinstance(raw_ents, list) or not 1 <= len(raw_ents) <= 20:
            raise ProfileError("%s.entities must list 1..20 entities" % where)
        for j, e in enumerate(raw_ents):
            at = "%s.entities[%d]" % (where, j)
            _keys(e, ("ref", "type", "subtype", "name", "codelist", "attributes", "person_key",
                      "key_only", "within"), at)
            if not isinstance(e.get("ref"), str) or not _ID.match(e["ref"]) or e["ref"] in ents:
                raise ProfileError("%s.ref must be a unique lowercase identifier" % at)
            if e.get("type") not in CORE_TYPES or e["type"] == "event":
                raise ProfileError("%s.type must be an entity type other than event" % at)
            sub = e.get("subtype") or ""
            if sub and subtypes.get(sub, {}).get("type") != e["type"]:
                raise ProfileError("%s.subtype must be a %s subtype of the profile"
                                   % (at, e["type"]))
            ent = {"ref": e["ref"], "type": e["type"], "subtype": sub,
                   "name": _column(e.get("name"), at + ".name", required=False),
                   "codelist": codelist(e.get("codelist"), at + ".codelist"),
                   "attributes": {}, "person_key": _flag(e, "person_key", at),
                   "key_only": _flag(e, "key_only", at)}
            attrs = e.get("attributes") or {}
            if not isinstance(attrs, dict) or any(k not in ATTRIBUTE_NAMES for k in attrs):
                raise ProfileError("%s.attributes must map %s to columns" % (at, ATTRIBUTE_NAMES))
            if attrs and e["type"] != "person":
                raise ProfileError("%s.attributes: only a person has attributes" % at)
            ent["attributes"] = {k: _column(v, "%s.attributes.%s" % (at, k))
                                 for k, v in attrs.items()}
            if e["type"] != "person" and (ent["person_key"] or ent["key_only"]):
                raise ProfileError("%s: person_key and key_only are for persons" % at)
            if ent["person_key"] and not all(k in ent["attributes"] for k in
                                             ("birth_date", "birth_place", "mother_name")) \
                    or ent["person_key"] and not ent["name"]:
                raise ProfileError("%s.person_key needs the name, birth_place, birth_date and "
                                   "mother_name columns" % at)
            if not ent["name"] and not (e["type"] == "person"
                                        and "registry_number" in ent["attributes"]):
                raise ProfileError("%s.name is required (a person may be named by its "
                                   "registry_number instead)" % at)
            if e.get("within") is not None:
                if e["type"] != "place":
                    raise ProfileError("%s.within: only a place lies within another" % at)
                ent["within"] = e["within"]
            ents[e["ref"]] = ent
        for ref, ent in ents.items():
            # A container chain (cell -> unit -> facility -> institution) without loops.
            seen_refs, cur = {ref}, ent
            while cur.get("within"):
                nxt = ents.get(cur["within"])
                if nxt is None or nxt["type"] != "place" or cur["within"] in seen_refs:
                    raise ProfileError("%s.entities: %r lies within an unknown place or in a "
                                       "loop" % (where, ref))
                seen_refs.add(cur["within"])
                cur = nxt
        src["entities"] = list(ents.values())
        src_rels = []
        raw_rels = s.get("relations") or []
        if not isinstance(raw_rels, list) or len(raw_rels) > 20:
            raise ProfileError("%s.relations must be a list of at most 20 relations" % where)
        for j, r in enumerate(raw_rels):
            at = "%s.relations[%d]" % (where, j)
            _keys(r, ("type", "from", "to", "start", "end", "label", "label_codelist"), at)
            spec = rels.get(r.get("type"))
            a, b = ents.get(r.get("from")), ents.get(r.get("to"))
            if spec is None or spec["id"] in BUILTIN_RELATIONS or a is None or b is None \
                    or r["from"] == r["to"]:
                raise ProfileError("%s needs a profile relation type and two entity refs" % at)
            if a["type"] not in spec["from"] or b["type"] not in spec["to"]:
                raise ProfileError("%s: %s cannot go from %s to %s" % (at, spec["id"], a["type"],
                                                                       b["type"]))
            labels = r.get("label")
            labels = [labels] if isinstance(labels, str) or labels is None else labels
            if not isinstance(labels, list) or len(labels) > 4:
                raise ProfileError("%s.label must name 1..4 columns" % at)
            src_rels.append({"type": spec["id"], "from": r["from"], "to": r["to"],
                             "start": _column(r.get("start"), at + ".start", required=False),
                             "end": _column(r.get("end"), at + ".end", required=False),
                             "label": [_column(c, at + ".label") for c in labels
                                       if c is not None],
                             "label_codelist": codelist(r.get("label_codelist"),
                                                        at + ".label_codelist")})
        if any(e.get("within") for e in ents.values()) and "PART_OF" not in rels:
            raise ProfileError("%s: a place within another needs the PART_OF relation" % where)
        src["relations"] = src_rels
        if s.get("acl") is not None:
            acl = s["acl"]
            at = where + ".acl"
            _keys(acl, ("column", "groups", "pattern", "always", "classification"), at)
            groups = acl.get("groups") or {}
            if not isinstance(groups, dict):
                raise ProfileError("%s.groups must map column values to principals" % at)
            pattern = acl.get("pattern")
            if pattern is not None and (not isinstance(pattern, str) or "{value}" not in pattern
                                        or not _PRINCIPAL.match(pattern.replace("{value}", "x"))):
                raise ProfileError("%s.pattern must be a principal with {value} in it" % at)
            level = acl.get("classification", 0)
            if isinstance(level, bool) or not isinstance(level, int) or not 0 <= level <= 4:
                raise ProfileError("%s.classification must be 0..4" % at)
            src["acl"] = {"column": _column(acl.get("column"), at + ".column", required=False),
                          "groups": {str(k).strip(): _principals(v, "%s.groups.%s" % (at, k))
                                     for k, v in groups.items()},
                          "pattern": pattern.strip().casefold() if pattern else None,
                          "always": _principals(acl.get("always") or [], at + ".always"),
                          "classification": level}
            if not src["acl"]["column"] and (src["acl"]["groups"] or src["acl"]["pattern"]):
                raise ProfileError("%s: groups and pattern need the column they read" % at)
        out.append(src)
    return out


def for_extraction(profile: dict) -> dict:
    """The profile as the extraction model and the table mapper see it: without
    the subtypes and relations only structured imports produce."""
    if not any(x.get("structured_only") for x in
               profile["entity_subtypes"] + profile["relation_types"]):
        return profile
    out = dict(profile)
    out["entity_subtypes"] = [s for s in profile["entity_subtypes"] if not s.get("structured_only")]
    out["relation_types"] = [r for r in profile["relation_types"] if not r.get("structured_only")]
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
