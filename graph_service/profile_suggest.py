"""Profile suggestions from what extraction already found (E26e).

When no relation type of the profile fits, the extractor keeps the link as
RELATED_TO with the model's own short label ("ismerőse", "has vehicle
plate"); an identifier no pattern knows stays without a subtype. Grouped over
the documents the caller may see, these show what the profile lacks.

  suggest()  groups them (links, documents, the kinds of entity at either
             end). A label the profile already knows (a synonym added since
             those documents were read) is only reported: reprocessing the
             documents read with an older profile settles it. The rest goes
             to the model in ONE call that sorts each group: a synonym of an
             existing relation type, a new type with its labels in the four
             languages, or nothing worth a type; likewise each identifier
             shape. The model only names: the ends of a new type come from
             the graph, and every id it gives is checked against the profile.
             Without the model each group is offered as a new type under its
             own label, to be named by the administrator.
  apply()    turns the items an administrator accepted (and edited) into the
             next profile version, validated like any profile. Nothing is
             saved here: the administrator saves it (/profile/set), and the
             documents are read with it when they are processed again.

Data minimisation: an item carries labels, counts and entity kinds, never a
quote, a name or an identifier value. The identifier examples the model is
shown stay on the box.
"""
from __future__ import annotations

import copy
import json
import re
import sys
from collections import Counter

import domain_profile
from domain_profile import ProfileError, relation_line
from extraction import fold
from model import CORE_TYPES

MAX_RELATIONS = 20000
MAX_IDENTIFIERS = 20000
MAX_GROUPS = 40
MAX_ITEMS = 200
MIN_DOCUMENTS = 2
MIN_VALUES = 3
# A bare number this short (a room, a page, a year) is no kind of identifier.
MIN_DIGITS_ONLY = 6
MAX_LABEL = 60
MAX_VARIANTS = 8
MAX_SYNONYMS = 60
MAX_REGEX = 300
# An end kind is proposed for a new type when it has at least this share of
# the group's links (a stray mis-typed entity does not widen the type).
MIN_END_SHARE = 0.1
# A synonym whose target type fits fewer of the group's links than this is
# flagged: extraction drops a typed link whose ends the type does not allow.
MIN_FIT = 0.5
SUGGEST_MAX_TOKENS = 6144
GENERIC = ("RELATED_TO", "CO_MENTIONED")
_REL_ID = re.compile(r"^[A-Z][A-Z0-9_]{1,40}$")
_ID = re.compile(r"^[a-z][a-z0-9_]{0,40}$")
_VALUE = re.compile(r"\+?[0-9A-Z](?:[0-9A-Z./\-]|(?<=\d) (?=\d))*[0-9A-Z]")


def label_key(text) -> str:
    """A label for grouping and comparing: no accents, case, punctuation."""
    plain = fold(str(text or "")).replace("_", " ")
    return " ".join(re.sub(r"[^\w ]+", " ", plain).split())


def _clean(text, limit=MAX_LABEL) -> str:
    return " ".join(str(text or "").split())[:limit]


# -- identifier shapes ------------------------------------------------------------

def identifier_value(label: str) -> str | None:
    """The identifier inside an entity label ("BUO-450 forgalmi rendszám" ->
    "BUO-450"): the longest token run with at least two digits."""
    found = [m.group(0) for m in _VALUE.finditer(label or "")
             if sum(c.isdigit() for c in m.group(0)) >= 2]
    return max(found, key=len) if found else None


def _runs(value: str) -> list[tuple[str, int]]:
    out: list[tuple[str, int]] = []
    for c in value:
        kind = "9" if c.isdigit() else "A" if c.isalpha() else c
        if out and out[-1][0] == kind and kind in "9A":
            out[-1] = (kind, out[-1][1] + 1)
        else:
            out.append((kind, 1))
    return out


def shape(value: str) -> str:
    """"NY-466636" -> "AA-999999" (9 a digit, A a letter)."""
    return "".join(kind * n for kind, n in _runs(value))


def shape_regex(value: str) -> str:
    parts = []
    for kind, n in _runs(value):
        if kind == "9":
            parts.append(r"\d{%d}" % n if n > 1 else r"\d")
        elif kind == "A":
            parts.append("[A-Z]{%d}" % n if n > 1 else "[A-Z]")
        elif kind == " ":
            parts.append(" ?")
        else:
            parts.append(re.escape(kind))
    return r"(?<![\w])" + "".join(parts) + r"(?![\w])"


# -- grouping ---------------------------------------------------------------------

def _known_labels(profile: dict) -> dict[str, str]:
    known: dict[str, str] = {}
    for r in profile["relation_types"]:
        if r["id"] in GENERIC:
            continue
        for text in [r["id"], *r["label"].values(), *r.get("synonyms", [])]:
            key = label_key(text)
            if key:
                known.setdefault(key, r["id"])
    return known


def _stale(view, doc_ids, tag: str) -> int:
    count = 0
    for doc_id in doc_ids:
        doc = view.document(doc_id)
        if doc is not None and (doc.profile or "").split("/partial")[0] != tag:
            count += 1
    return count


def relation_groups(view, profile: dict, limit: int = MAX_RELATIONS) -> tuple[list, int, bool]:
    """([group], links scanned, cut) of the labelled RELATED_TO links the view shows."""
    groups: dict[str, dict] = {}
    scanned, cut = 0, False
    for edge in view.edges(types=("RELATED_TO",)):
        if scanned >= limit:
            cut = True
            break
        scanned += 1
        raw = _clean(edge.get("raw_label"))
        key = label_key(raw)
        if not key:
            continue
        group = groups.setdefault(key, {"key": key, "labels": Counter(), "relations": 0,
                                        "documents": set(), "ends": Counter()})
        group["labels"][raw] += 1
        group["relations"] += 1
        group["documents"].update(ev["doc_id"] for ev in edge["evidence"])
        a, b = view.node(edge["a"]), view.node(edge["b"])
        group["ends"][(a["type"], b["type"])] += 1
    out = [g for g in groups.values() if len(g["documents"]) >= MIN_DOCUMENTS]
    out.sort(key=lambda g: (-g["relations"], g["key"]))
    return out, scanned, cut


def identifier_groups(view, profile: dict, limit: int = MAX_IDENTIFIERS) -> tuple[list, int, bool]:
    """([group], identifiers scanned, cut) of the identifiers without a subtype
    whose value no profile pattern matches, by shape."""
    patterns = [re.compile(p["regex"]) for p in profile["identifier_patterns"]]
    groups: dict[str, dict] = {}
    scanned, cut = 0, False
    for node in view.nodes(types=("identifier",), subtypes=("",)):
        if node.get("subtype"):
            continue
        if scanned >= limit:
            cut = True
            break
        scanned += 1
        value = identifier_value(node.get("label") or "")
        if not value or any(p.search(value) for p in patterns):
            continue
        key = shape(value)
        group = groups.setdefault(key, {"shape": key, "regex": shape_regex(value),
                                        "values": set(), "documents": set()})
        group["values"].add(value)
        group["documents"].update(node.get("documents") or ())
    out = [g for g in groups.values()
           if len(g["values"]) >= MIN_VALUES and len(g["documents"]) >= MIN_DOCUMENTS
           and not (g["shape"].isdigit() and len(g["shape"]) < MIN_DIGITS_ONLY)]
    out.sort(key=lambda g: (-len(g["values"]), g["shape"]))
    return out, scanned, cut


# -- ids and ends -------------------------------------------------------------------

def relation_id(label: str, taken) -> str:
    base = re.sub(r"[^A-Z0-9]+", "_", fold(label).upper()).strip("_")[:36] or "LINK"
    if not base[0].isalpha():
        base = "REL_" + base
    candidate, n = base, 2
    while candidate in taken or not _REL_ID.match(candidate):
        candidate = "%s_%d" % (base[:36], n)
        n += 1
    return candidate


def subtype_id(shape_text: str, taken) -> str:
    base = ("code_" + re.sub(r"[^a-z0-9]+", "_", shape_text.lower()).strip("_"))[:36]
    candidate, n = base, 2
    while candidate in taken:
        candidate = "%s_%d" % (base, n)
        n += 1
    return candidate


def _end_types(ends: Counter, side: int) -> list[str]:
    total = sum(ends.values()) or 1
    counts = Counter()
    for pair, n in ends.items():
        counts[pair[side]] += n
    kept = [t for t, n in counts.items() if n / total >= MIN_END_SHARE]
    kept = kept or [counts.most_common(1)[0][0]]
    return [t for t in CORE_TYPES if t in kept]


def _fit(spec: dict, ends: Counter) -> float:
    total = sum(ends.values()) or 1
    good = sum(n for (a, b), n in ends.items()
               if (a in spec["from"] and b in spec["to"]) or (b in spec["from"] and a in spec["to"]))
    return round(good / total, 2)


def _labels(raw, fallback: str) -> tuple[dict, bool]:
    """({en, hu, da, de}, from the model) -- the fallback text as English when
    the model gave no usable label."""
    if isinstance(raw, dict):
        out = {lang: _clean(raw.get(lang)) for lang in domain_profile.LANGS
               if isinstance(raw.get(lang), str) and _clean(raw.get(lang))}
        if out.get("en"):
            return out, True
    return {"en": _clean(fallback)}, False


# -- the model ----------------------------------------------------------------------

_LABEL_SCHEMA = {"type": "object", "properties": {lang: {"type": "string"}
                                                  for lang in domain_profile.LANGS},
                 "required": list(domain_profile.LANGS)}
SCHEMA = {"type": "object", "properties": {
    "labels": {"type": "array", "items": {"type": "object", "properties": {
        "n": {"type": "integer"}, "action": {"type": "string", "enum": ["synonym", "new", "ignore"]},
        "relation": {"type": "string"}, "id": {"type": "string"}, "label": _LABEL_SCHEMA},
        "required": ["n", "action"]}},
    "identifiers": {"type": "array", "items": {"type": "object", "properties": {
        "n": {"type": "integer"}, "action": {"type": "string", "enum": ["existing", "new", "ignore"]},
        "subtype": {"type": "string"}, "id": {"type": "string"}, "label": _LABEL_SCHEMA},
        "required": ["n", "action"]}}},
    "required": ["labels", "identifiers"]}


def _system_prompt(profile: dict) -> str:
    relations = "\n".join(relation_line(r) for r in profile["relation_types"]
                          if r["id"] not in GENERIC)
    subtypes = "\n".join("- %s: %s" % (s["id"], s["label"]["en"])
                         for s in profile["entity_subtypes"] if s["type"] == "identifier")
    return (
        "You keep the vocabulary of a knowledge graph built from an organisation's documents. "
        "Where no relation type fitted, the extractor kept a link with its own short label. "
        "Sort each LABEL:\n"
        "- synonym: it means the same kind of link as one of the existing relation types below "
        "(a family word such as mother, sibling or spouse is a synonym of a relative relation; "
        "a word for holding or using a number or plate is a synonym of a use relation) - give "
        "that type's id in `relation`;\n"
        "- new: no existing type fits, but the label names a real kind of link between two "
        "parties that will recur - give an English UPPER_SNAKE_CASE id in `id` (like "
        "ACQUAINTANCE_OF) and a short label in English, Hungarian, Danish and German, in the "
        "style of the existing labels;\n"
        "- ignore: not a kind of link (a field name, a property, a one-off description).\n"
        "Existing relation types:\n" + relations + "\n"
        "Sort each IDENTIFIER shape (9 is a digit, A a capital letter) by its examples:\n"
        "- existing: one of the identifier subtypes below - give its id in `subtype`;\n"
        "- new: a recurring kind of identifier - a lowercase snake_case English id in `id` "
        "and a short label in English, Hungarian, Danish and German;\n"
        "- ignore: not an identifier (a date, a time, a room or page number, part of a "
        "sentence).\n"
        "Identifier subtypes:\n" + (subtypes or "(none)") + "\n"
        "Answer for every n given. Return JSON only.")


def _ask(llm, profile: dict, labels: list[dict], identifiers: list[dict]) -> dict:
    user = json.dumps({
        "labels": [{"n": i, "label": g["label"], "variants": g["variants"][:5],
                    "between": ", ".join("%s -> %s" % (e["from"], e["to"]) for e in g["ends"][:3])}
                   for i, g in enumerate(labels)],
        "identifiers": [{"n": i, "shape": g["shape"], "examples": g["_examples"]}
                        for i, g in enumerate(identifiers)]}, ensure_ascii=False)
    return llm.json_call(_system_prompt(profile), user, SCHEMA, max_tokens=SUGGEST_MAX_TOKENS)


# -- suggest ------------------------------------------------------------------------

def _numbered(answer, key: str) -> dict[int, dict]:
    entries = answer.get(key) if isinstance(answer, dict) else None
    out: dict[int, dict] = {}
    for entry in entries if isinstance(entries, list) else []:
        if isinstance(entry, dict) and isinstance(entry.get("n"), int) \
                and not isinstance(entry.get("n"), bool):
            out.setdefault(entry["n"], entry)
    return out


def suggest(view, profile: dict, llm=None) -> dict:
    """The suggestions for this caller's view of the graph (see the module)."""
    known = _known_labels(profile)
    tag = domain_profile.profile_tag(profile)
    rel_groups, rel_scanned, rel_cut = relation_groups(view, profile)
    id_groups, id_scanned, id_cut = identifier_groups(view, profile)
    specs = {r["id"]: r for r in profile["relation_types"] if r["id"] not in GENERIC}
    identifier_subtypes = {s["id"] for s in profile["entity_subtypes"]
                           if s["type"] == "identifier"}

    already, labels = [], []
    for g in rel_groups:
        item = {"key": "relation:" + g["key"], "kind": "relation",
                "label": g["labels"].most_common(1)[0][0],
                "variants": [text for text, _ in g["labels"].most_common(MAX_VARIANTS)],
                "support": {"relations": g["relations"], "documents": len(g["documents"]),
                            "stale_documents": _stale(view, g["documents"], tag)},
                "ends": [{"from": a, "to": b, "count": n} for (a, b), n in g["ends"].most_common(5)],
                "_ends": g["ends"]}
        if g["key"] in known:
            item.update(action="known", relation=known[g["key"]])
            already.append(item)
        elif len(labels) < MAX_GROUPS:
            labels.append(item)
    identifiers = []
    for g in id_groups[:MAX_GROUPS]:
        identifiers.append({"key": "identifier:" + g["shape"], "kind": "identifier",
                            "shape": g["shape"], "regex": g["regex"],
                            "normalize": "upper" if "A" in g["shape"] else "none",
                            "support": {"values": len(g["values"]),
                                        "documents": len(g["documents"])},
                            "_examples": sorted(g["values"])[:5]})

    answer, model = {}, "off" if llm is None else "used"
    if llm is not None and (labels or identifiers):
        try:
            answer = _ask(llm, profile, labels, identifiers)
        except Exception as e:  # noqa: BLE001 -- any model failure leaves the rule defaults
            sys.stderr.write("[graph] profile suggestions without the model: %s\n" % e)
            model = "failed"
    elif llm is not None:
        model = "unused"
    by_n = _numbered(answer, "labels")
    taken = set(specs) | set(GENERIC)
    for n, item in enumerate(labels):
        entry = by_n.get(n, {})
        item["from"], item["to"] = _end_types(item["_ends"], 0), _end_types(item["_ends"], 1)
        action = entry.get("action")
        if action == "synonym" and entry.get("relation") in specs:
            spec = specs[entry["relation"]]
            item.update(action="synonym", relation=spec["id"], fit=_fit(spec, item["_ends"]))
            item["source"] = "model"
        elif action == "ignore":
            item.update(action="ignore", source="model")
        else:
            rid = entry.get("id") if isinstance(entry.get("id"), str) else ""
            rid = rid.strip().upper()
            if not _REL_ID.match(rid) or rid in taken:
                rid = relation_id(item["label"], taken)
            label, named = _labels(entry.get("label"), item["label"])
            item.update(action="new", id=rid, labels=label,
                        source="model" if action == "new" and named else "rule")
        taken.add(item.get("id") or "")
    ids_by_n = _numbered(answer, "identifiers")
    taken_subtypes = {s["id"] for s in profile["entity_subtypes"]}
    for n, item in enumerate(identifiers):
        entry = ids_by_n.get(n, {})
        action = entry.get("action")
        if action == "existing" and entry.get("subtype") in identifier_subtypes:
            item.update(action="existing", subtype=entry["subtype"], source="model")
        elif action == "ignore":
            item.update(action="ignore", source="model")
        else:
            sid = entry.get("id") if isinstance(entry.get("id"), str) else ""
            sid = sid.strip().lower()
            if not _ID.match(sid) or sid in taken_subtypes:
                sid = subtype_id(item["shape"], taken_subtypes)
            label, named = _labels(entry.get("label"), item["shape"])
            item.update(action="new", id=sid, labels=label,
                        source="model" if action == "new" and named else "rule")
        taken_subtypes.add(item.get("id") or "")
    for item in already + labels + identifiers:
        item.pop("_ends", None)
        item.pop("_examples", None)
    return {"items": labels + identifiers, "known": already, "model": model,
            "profile": {"id": profile["id"], "version": profile["version"]},
            "scanned": {"relations": rel_scanned, "identifiers": id_scanned,
                        "cut": rel_cut or id_cut}}


# -- apply --------------------------------------------------------------------------

def _texts(raw, limit: int) -> list[str]:
    if not isinstance(raw, list):
        raise ProfileError("variants must be a list of texts")
    out = []
    for text in raw[:limit]:
        if not isinstance(text, str):
            raise ProfileError("variants must be a list of texts")
        clean = _clean(text)
        if clean and clean.casefold() not in [o.casefold() for o in out]:
            out.append(clean)
    return out


def _label_of(raw, where: str) -> dict:
    if not isinstance(raw, dict) or not isinstance(raw.get("en"), str) or not raw["en"].strip():
        raise ProfileError("%s needs at least an English label" % where)
    return {lang: _clean(raw[lang]) for lang in domain_profile.LANGS
            if isinstance(raw.get(lang), str) and _clean(raw.get(lang))}


def apply(profile: dict, items) -> tuple[dict, list[dict]]:
    """(next profile version, changes) from the accepted items; ProfileError
    when an item is malformed or the result is not a valid profile."""
    if not isinstance(items, list) or not items or len(items) > MAX_ITEMS:
        raise ProfileError("items must be a list of 1-%d accepted suggestions" % MAX_ITEMS)
    out = copy.deepcopy(profile)
    out["version"] = profile["version"] + 1
    rels = {r["id"]: r for r in out["relation_types"]}
    subtypes = {s["id"]: s for s in out["entity_subtypes"]}
    pattern_ids = {p["id"] for p in out["identifier_patterns"]}
    changes = []
    for number, item in enumerate(items, 1):
        where = "item %d" % number
        if not isinstance(item, dict):
            raise ProfileError("%s must be an object" % where)
        kind, action = item.get("kind"), item.get("action")
        if kind == "relation" and action == "synonym":
            target = rels.get(item.get("relation"))
            if target is None or target["id"] in GENERIC:
                raise ProfileError("%s: unknown relation type %r" % (where, item.get("relation")))
            have = {label_key(s) for s in target["synonyms"]}
            added = [t for t in _texts(item.get("variants") or [], MAX_VARIANTS)
                     if label_key(t) not in have]
            if len(target["synonyms"]) + len(added) > MAX_SYNONYMS:
                raise ProfileError("%s: %s would have more than %d synonyms"
                                   % (where, target["id"], MAX_SYNONYMS))
            target["synonyms"] = target["synonyms"] + added
            changes.append({"change": "synonyms", "relation": target["id"], "added": added})
        elif kind == "relation" and action == "new":
            rid = item.get("id")
            if not isinstance(rid, str) or not _REL_ID.match(rid) or rid in rels:
                raise ProfileError("%s: the id must be a new UPPER_CASE identifier" % where)
            new = {"id": rid, "label": _label_of(item.get("labels"), where),
                   "from": item.get("from"), "to": item.get("to"),
                   "symmetric": item.get("symmetric") is True, "presence": False,
                   "holding": False,
                   "synonyms": _texts(item.get("variants") or [], MAX_VARIANTS)}
            out["relation_types"].append(new)
            rels[rid] = new
            changes.append({"change": "relation", "relation": rid})
        elif kind == "identifier" and action in ("existing", "new"):
            regex = item.get("regex")
            if not isinstance(regex, str) or not regex or len(regex) > MAX_REGEX:
                raise ProfileError("%s: a pattern of at most %d characters is needed"
                                   % (where, MAX_REGEX))
            if action == "existing":
                sid = item.get("subtype")
                if (subtypes.get(sid) or {}).get("type") != "identifier":
                    raise ProfileError("%s: unknown identifier subtype %r" % (where, sid))
            else:
                sid = item.get("id")
                if not isinstance(sid, str) or not _ID.match(sid) or sid in subtypes:
                    raise ProfileError("%s: the id must be a new lowercase identifier" % where)
                subtypes[sid] = {"id": sid, "type": "identifier",
                                 "label": _label_of(item.get("labels"), where)}
                out["entity_subtypes"].append(subtypes[sid])
            pid, n = sid + "_pattern", 2
            while pid in pattern_ids:
                pid, n = "%s_pattern_%d" % (sid, n), n + 1
            pattern_ids.add(pid)
            out["identifier_patterns"].append({
                "id": pid, "subtype": sid, "label": dict(subtypes[sid]["label"]), "regex": regex,
                "normalize": item.get("normalize") or "none"})
            changes.append({"change": "pattern", "subtype": sid, "new_subtype": action == "new"})
        elif action in ("ignore", "known"):
            continue
        else:
            raise ProfileError("%s: unknown suggestion %r/%r" % (where, kind, action))
    if not changes:
        raise ProfileError("no accepted change")
    return domain_profile.validate(out), changes
