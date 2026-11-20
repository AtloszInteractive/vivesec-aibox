"""Learning structured sources from a sample file (E28b).

A box running standalone cannot know in advance which exports it will get or
how their columns are named. Three things help the administrator:

  inspect        which source of the profile a file fits: per source the
                 columns found (by name or by an alias), the missing ones and
                 the file's columns no source reads;
  learn          a draft of a new source from a sample file: the box reads the
                 header and a few rows and asks the local model what each
                 column is (record id, person name and role, birth data,
                 mother's name, ID and registry numbers, phone, e-mail,
                 address, dates, relations, deleted mark); without the model,
                 or when it fails, name rules decide. The administrator corrects
                 the draft and `apply_draft` turns it into a new source of the
                 next profile version, validated like any profile;
  apply_aliases  another name of a column of an existing source (an export
                 renamed it): recorded once, matched from then on.

Privacy: the sample rows go only to the local model (GRAPH_LLM_URL, the box's
GPU broker). Answers carry the header, counts and per-column shape hints
(date, number ...), never a cell value; nothing of the file is logged.
"""
from __future__ import annotations

import copy
import os
import re
import sys

import domain_profile
from domain_profile import BUILTIN_RELATIONS, LANGS, ProfileError
from extraction import fold
from person_key import iso_date
import structured_import
from structured_import import StructuredImportError
import table_formats

ROLES = ("record_id", "person", "birth_date", "birth_place", "mother_name", "id_number",
         "registry_number", "phone", "email", "address", "start_date", "end_date",
         "relation_label", "deleted", "text", "ignore")
ATTRIBUTE_ROLES = ("birth_date", "birth_place", "mother_name", "id_number", "registry_number")
# Entities that belong to a person: linked to it by a (holding) relation.
OWNED_ROLES = {"phone": "identifier", "email": "identifier", "address": "place"}
ENTITY_ROLES = ("person",) + tuple(OWNED_ROLES)
SAMPLE_ROWS = 6
KIND_ROWS = 50
MAX_CELL = 80
MAX_COLUMNS = 100
MAX_RELATIONS = 20
LEARN_MAX_TOKENS = 4096
DELETED_VALUES = ["igen", "i", "1", "true", "x", "yes", "y", "ja", "törölt", "deleted"]

# Name rules, first match wins (the more specific ones first), on the folded
# header with every non-alphanumeric run as one space. Hungarian, English,
# German and Danish wording.
RULES = (
    ("deleted", r"\b(torolve|torolt|torles|deleted|is deleted|geloscht|slettet)\b"),
    ("mother_name", r"\b(anyja|anyja neve|mother|mothers|mutter|mor|mors|moder)\b"),
    ("birth_date", r"\b(szuletesi (ido|idopont|datum|nap)|szul (ido|datum)|szuletett|birth ?date|"
                   r"date of birth|dob|born|geburtsdatum|geburtstag|fodselsdato|fodselsdag)\b"),
    ("birth_place", r"\b(szuletesi hely|szul hely|birth ?place|place of birth|geburtsort|"
                    r"fodested|fodselssted|fodeby)\b"),
    ("registry_number", r"\b(nyilvantartasi|nyilv|torzsszam|registry|register|reg no|regnr)\b"),
    ("id_number", r"\b(szemelyi (szam|azonosito|igazolvany\w*)|szemelyiszam|szig\w*|taj\w*|"
                  r"adoazonosito\w*|id number|id no|personal id|national id|passport\w*|"
                  r"utlevel\w*|personnummer|cpr\w*|ausweis\w*)\b"),
    ("phone", r"\b(telefon\w*|tel|mobil\w*|phone|handy|tlf)\b"),
    ("email", r"\b(e mail\w*|email\w*|mail)\b"),
    ("address", r"\b(lakcim\w*|cim|cime|lakhely\w*|tartozkodasi hely|address\w*|adresse|"
                r"anschrift|addr)\b"),
    ("end_date", r"\b(vege|veg|lejarat\w*|end|ende|bis|til|slut\w*|expir\w*|valid until|ig)\b"),
    ("start_date", r"\b(kezdete|kezdet|datum|date|dato|tol|start\w*|von|fra|kelt|idopont\w*|"
                   r"ideje|napja|nap|beginn)\b"),
    ("relation_label", r"\b(kapcsolat jellege|jellege|jelleg|kapcsolat|rokonsag\w*|rokoni|"
                       r"relationship|relation|beziehung|verhaltnis|forhold)\b"),
    ("record_id", r"\b(rekordazonosito|rekord ?id|record ?id|row ?id|azonosito|id|kulcs|key|"
                  r"uuid|guid|sorszam)\b"),
    ("person", r"\b(nev|neve|name|navn|nachname|vorname|csaladnev|utonev|full name)\b"),
    ("text", r"\b(megjegyzes\w*|leiras\w*|informacio\w*|szoveg\w*|tartalom|comment\w*|"
             r"remark\w*|note\w*|description|text|bemerkung\w*|beschreibung|beskrivelse|"
             r"bemaerkning\w*|notat)\b"),
)
_RULES = tuple((role, re.compile(rx)) for role, rx in RULES)
_NAME_WORDS = {"nev", "neve", "name", "navn", "szama", "szam", "number"}
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")
_PHONE = re.compile(r"^(\+|00|06)[\d\s/().-]{6,}$")
_NUMBER = re.compile(r"^-?\d+(?:[.,]\d+)?$")
_SLUG = re.compile(r"[^a-z0-9]+")


def _norm(header: str) -> str:
    return _SLUG.sub(" ", fold(header or "")).strip()


# -- reading the sample --------------------------------------------------------------

def _tables(raw: bytes, filename: str) -> list:
    ext = os.path.splitext(filename or "")[1].lower()
    try:
        tables = table_formats.tables_of(raw, ext, structured_import.MAX_ROWS)
    except table_formats.FormatError as e:
        raise StructuredImportError(str(e))
    tables = [(name, rows) for name, rows in tables if rows]
    if not tables:
        raise StructuredImportError("the file is empty")
    return tables


def _main_table(tables: list, sheet: str | None):
    """The table to learn from: the one named, else the one with most rows."""
    if sheet:
        for name, rows in tables:
            if fold(name) == fold(sheet):
                return name, rows
        raise StructuredImportError("the file has no sheet or table %r (it has: %s)"
                                    % (sheet, ", ".join(n for n, _ in tables) or "-"))
    return max(tables, key=lambda t: len(t[1]))


def _header(rows) -> list[str]:
    header = [str(h or "").strip() for h in rows[0][1]]
    folded = [fold(h) for h in header if h]
    if len(set(folded)) != len(folded):
        raise StructuredImportError("the header repeats a column name")
    if not folded:
        raise StructuredImportError("the file has no header row")
    if len(header) > MAX_COLUMNS:
        raise StructuredImportError("the table has more than %d columns" % MAX_COLUMNS)
    return header


def _kind(values: list[str]) -> str:
    """What a column's sample values look like (a hint for the administrator,
    never the values themselves)."""
    values = [v for v in values if v]
    if not values:
        return "empty"
    if all(_EMAIL.match(v) for v in values):
        return "email"
    if all(_PHONE.match(v) and 8 <= sum(c.isdigit() for c in v) <= 15 for v in values):
        return "phone"
    if all(iso_date(v) for v in values):
        return "date"
    if all(_NUMBER.match(v) for v in values):
        return "number"
    return "text"


def _cells(rows, k: int, limit: int) -> list[str]:
    return [(cells[k] if k < len(cells) else "").strip() for _, cells in rows[1:limit + 1]]


# -- which source a file fits -------------------------------------------------------

def required_columns(source: dict) -> list[str]:
    """The columns an import of the source cannot do without (plan_import):
    the record id, the name of each person and the column of its own rule."""
    out = list(source["record_id"])
    out += [e["name"] for e in source["entities"] if e["type"] == "person" and e["name"]]
    if (source.get("acl") or {}).get("column"):
        out.append(source["acl"]["column"])
    return list(dict.fromkeys(out))


def _fit(source: dict, tables: list) -> dict:
    columns = structured_import.source_columns(source)
    try:
        _, rows = structured_import._pick(tables, source.get("sheet"),
                                          structured_import.expected_columns(source))
        header = [str(h or "").strip() for h in rows[0][1]]
    except StructuredImportError:
        header = []
    found = structured_import.header_index(source, header)
    required = required_columns(source)
    missing = [c for c in required if fold(c) not in found]
    optional = sorted(c for c in columns if fold(c) not in found and c not in required)
    used = set(found.values())
    return {"id": source["id"], "label": source["label"], "title": source["title"],
            "fit": "none" if missing else "partial" if optional else "full",
            "matched": len(found), "total": len(columns),
            "missing_required": missing, "missing_optional": optional,
            "unused": [h for k, h in enumerate(header) if h and k not in used],
            "aliased": sorted(c for c in columns if fold(c) in found
                              and fold(c) not in {fold(h) for h in header})}


def inspect(profile: dict, raw: bytes, filename: str, sheet: str | None = None) -> dict:
    """How the file fits each structured source of the profile, and the header
    of the table a new source would be learnt from."""
    tables = _tables(raw, filename)
    name, rows = _main_table(tables, sheet)
    fits = [_fit(s, tables) for s in profile.get("structured_sources") or []]
    usable = [f for f in fits if f["fit"] != "none" and f["matched"] * 2 >= f["total"]]
    best = max(usable, key=lambda f: (f["fit"] == "full", f["matched"] / max(1, f["total"])),
               default=None)
    return {"tables": [n for n, _ in tables], "sheet": name, "header": _header(rows),
            "rows": len(rows) - 1, "sources": fits, "best": best["id"] if best else None}


# -- the draft ---------------------------------------------------------------------

def _person_subtypes(profile: dict) -> dict[str, dict]:
    return {s["id"]: s for s in profile["entity_subtypes"] if s["type"] == "person"}


def _relation_specs(profile: dict) -> dict[str, dict]:
    """The relation types a source may state: not the built-in ones, and not a
    protected one (that needs a protected part, set up in the profile JSON)."""
    return {r["id"]: r for r in profile["relation_types"]
            if r["id"] not in BUILTIN_RELATIONS and not r.get("protected")}


def _subtype_by_name(header: str, subtypes: dict) -> str:
    """The person subtype whose label (any language) the header names."""
    tokens = _norm(header).split()
    best, length = "", 0
    for sid, s in subtypes.items():
        for text in s["label"].values():
            words = [w for w in _norm(text).split() if len(w) >= 3]
            if words and all(any(t.startswith(w) for t in tokens) for w in words) \
                    and len(" ".join(words)) > length:
                best, length = sid, len(" ".join(words))
    return best


def _owner(k: int, header: list[str], persons: list[int]) -> int | None:
    """The person column a property column belongs to: the one sharing a word
    with it ("Fogvatartott nyilvántartási száma" -> "Fogvatartott neve"), else
    the nearest to its left, else the first."""
    if not persons:
        return None
    if len(persons) == 1:
        return persons[0]
    words = {w for w in _norm(header[k]).split() if len(w) >= 3} - _NAME_WORDS
    scored = [(len(words & ({w for w in _norm(header[p]).split()} - _NAME_WORDS)), p)
              for p in persons]
    top = max(scored)
    if top[0]:
        return top[1]
    left = [p for p in persons if p < k]
    return left[-1] if left else persons[0]


def _holding_for(profile: dict, role: str) -> str:
    """The relation a person's phone, e-mail or address is linked by."""
    target = OWNED_ROLES[role]
    specs = _relation_specs(profile)
    fits = [r for r in specs.values() if "person" in r["from"] and target in r["to"]]
    prefer = "RESIDES_AT" if role == "address" else "USES"
    if prefer in specs and specs[prefer] in fits:
        return prefer
    holding = [r for r in fits if r.get("holding")]
    return (holding or fits or [{"id": ""}])[0]["id"]


def _pair_relation(profile: dict, sub_a: str, sub_b: str) -> tuple[str, bool] | None:
    """A relation the two persons' subtypes name (contact -> inmate: CONTACT_OF);
    (type, swapped) or None."""
    for r in _relation_specs(profile).values():
        first, second = r.get("from_subtypes") or [], r.get("to_subtypes") or []
        if not first or not second or "person" not in r["from"] or "person" not in r["to"]:
            continue
        if sub_a in first and sub_b in second:
            return r["id"], False
        if sub_b in first and sub_a in second:
            return r["id"], True
    return None


def rule_roles(header: list[str], kinds: list[str]) -> list[str]:
    """Each column's role by its name (and, failing that, by the shape of its
    values: e-mail addresses, phone numbers, dates)."""
    roles = []
    for k, h in enumerate(header):
        text = _norm(h)
        role = next((r for r, rx in _RULES if rx.search(text)), "ignore") if text else "ignore"
        if role == "ignore" and kinds[k] in ("email", "phone"):
            role = kinds[k]
        elif role == "ignore" and kinds[k] == "date":
            role = "start_date"
        roles.append(role)
    for single in ("record_id", "deleted"):
        seen = False
        for k, role in enumerate(roles):
            if role == single:
                if seen:
                    roles[k] = "ignore"
                seen = True
    return roles


def _learn_prompt(profile: dict) -> str:
    subtypes = "; ".join("%s (%s)" % (s["id"], s["label"]["en"])
                         for s in _person_subtypes(profile).values()) or "none"
    rels = "\n".join(domain_profile.relation_line(r) for r in _relation_specs(profile).values()
                     if "person" in r["from"])
    return (
        "You map the columns of a register export (one row = one record) for a rule-based "
        "import. You see the header and a few sample rows. For EVERY column give one entry "
        "with `column` = the header text exactly and one role:\n"
        "- record_id: the column that identifies the row (a record id); several only when "
        "together they are the key\n"
        "- person: the cell is a person's name; give `subtype` from this list when the header "
        "or the cells show the person's role: " + subtypes + "\n"
        "- birth_date, birth_place, mother_name (the mother's name is a property of a person, "
        "NOT a separate person), id_number (a personal ID number), registry_number (the "
        "person's number in an institution's own register): properties of the person whose "
        "name is in column `of`\n"
        "- phone, email, address: a phone number, e-mail address or postal address of the "
        "person in column `of`\n"
        "- start_date / end_date: when the row's relation started (or happened) / ended\n"
        "- relation_label: text or a code saying what the relation of the row is (e.g. "
        "kinship)\n"
        "- deleted: marks a deleted record\n"
        "- text: free text worth keeping (remarks, the information itself)\n"
        "- ignore: anything else (counts, row serial numbers, codes of no use)\n"
        "Then list the relations EVERY row states between two person columns, or between a "
        "person column and a phone, email or address column (`from`/`to` are column headers; "
        "`from` is the one who does what the relation says), using only these types:\n"
        + (rels or "(none)") + "\nReturn JSON only."
    )


def _learn_schema(profile: dict) -> dict:
    column = {"type": "object", "properties": {
        "column": {"type": "string"},
        "role": {"type": "string", "enum": list(ROLES)},
        "subtype": {"type": "string"},
        "of": {"type": "string"}}, "required": ["column", "role"]}
    relation = {"type": "object", "properties": {
        "type": {"type": "string", "enum": sorted(_relation_specs(profile)) or ["NONE"]},
        "from": {"type": "string"}, "to": {"type": "string"}},
        "required": ["type", "from", "to"]}
    return {"type": "object", "properties": {
        "columns": {"type": "array", "items": column},
        "relations": {"type": "array", "items": relation}},
        "required": ["columns", "relations"]}


def _sample_text(header: list[str], rows) -> str:
    lines = []
    for _, cells in rows[1:SAMPLE_ROWS + 1]:
        parts = ["%s: %s" % (h, str(cells[k]).strip()[:MAX_CELL])
                 for k, h in enumerate(header) if h and k < len(cells) and str(cells[k]).strip()]
        lines.append("; ".join(parts))
    return "Header: %s\nSample rows:\n%s" % (" | ".join(header), "\n".join(lines))


def _fits(spec: dict, a: str, b: str) -> bool:
    return a in spec["from"] and b in spec["to"]


def learn(profile: dict, raw: bytes, filename: str, sheet: str | None = None, llm=None) -> dict:
    """A draft of a new structured source from a sample file (see the module)."""
    tables = _tables(raw, filename)
    name, rows = _main_table(tables, sheet)
    header = _header(rows)
    kinds = [_kind(_cells(rows, k, KIND_ROWS)) for k in range(len(header))]
    roles = rule_roles(header, kinds)
    source = ["rule"] * len(header)
    subtypes = _person_subtypes(profile)
    sub = [""] * len(header)
    of: dict[int, int] = {}
    specs = _relation_specs(profile)
    col = {fold(h): k for k, h in enumerate(header) if h}
    model_rels = []

    answer, model = {}, "off" if llm is None else "used"
    if llm is not None:
        try:
            answer = llm.json_call(_learn_prompt(profile), _sample_text(header, rows),
                                   _learn_schema(profile), max_tokens=LEARN_MAX_TOKENS)
            if not isinstance(answer, dict):
                raise ValueError("not an object")
        except Exception as e:  # noqa: BLE001 -- any model failure leaves the rules
            # Only the kind of failure: the prompt and the answer carry sample cells.
            sys.stderr.write("[graph] source learning without the model: %s\n"
                             % type(e).__name__)
            answer, model = {}, "failed"
    pending_of = {}
    model_ids = False
    for c in answer.get("columns") or []:
        if not isinstance(c, dict):
            continue
        k = col.get(fold(str(c.get("column") or "")))
        if k is None or c.get("role") not in ROLES:
            continue
        roles[k], source[k] = c["role"], "model"
        model_ids = model_ids or c["role"] == "record_id"
        if c["role"] == "person" and c.get("subtype") in subtypes:
            sub[k] = c["subtype"]
        if isinstance(c.get("of"), str):
            pending_of[k] = c["of"]
    if answer and not model_ids:
        # The model named no record id: the name rules' one, if any.
        for k, role in enumerate(rule_roles(header, kinds)):
            if role == "record_id":
                roles[k], source[k] = "record_id", "rule"
    persons = [k for k, r in enumerate(roles) if r == "person"]
    for k in persons:
        if not sub[k]:
            sub[k] = _subtype_by_name(header[k], subtypes)
    for k, role in enumerate(roles):
        if role in ATTRIBUTE_ROLES or role in OWNED_ROLES:
            owner = col.get(fold(pending_of.get(k, "")))
            of[k] = owner if owner in persons else _owner(k, header, persons)
    holding = {k: _holding_for(profile, roles[k]) for k in range(len(header))
               if roles[k] in OWNED_ROLES}
    for r in answer.get("relations") or []:
        if not isinstance(r, dict) or r.get("type") not in specs:
            continue
        a, b = col.get(fold(str(r.get("from") or ""))), col.get(fold(str(r.get("to") or "")))
        if a is None or b is None or a == b:
            continue
        spec = specs[r["type"]]
        if roles[a] in OWNED_ROLES and roles[b] == "person":
            a, b = b, a
        if roles[a] == "person" and roles[b] in OWNED_ROLES:
            if _fits(spec, "person", OWNED_ROLES[roles[b]]):
                of[b], holding[b] = a, spec["id"]
        elif roles[a] == "person" and roles[b] == "person" and _fits(spec, "person", "person"):
            if (spec["id"], a, b) not in model_rels:
                model_rels.append((spec["id"], a, b))
    pairs = model_rels
    if not pairs:
        for i, a in enumerate(persons):
            for b in persons[i + 1:]:
                found = _pair_relation(profile, sub[a], sub[b])
                if found:
                    pairs.append((found[0], b, a) if found[1] else (found[0], a, b))
    first = {role: next((header[k] for k, r in enumerate(roles) if r == role), "")
             for role in ("start_date", "end_date", "relation_label")}
    relations = []
    for n, (rid, a, b) in enumerate(pairs[:MAX_RELATIONS]):
        main = n == 0
        relations.append({"type": rid, "from": header[a], "to": header[b],
                          "start": first["start_date"] if main else "",
                          "end": first["end_date"] if main else "",
                          "label": first["relation_label"] if main else ""})
    columns = []
    for k, h in enumerate(header):
        if not h:
            continue  # an empty header cell (a trailing separator) is no column
        entry = {"column": h, "role": roles[k], "subtype": sub[k] if roles[k] == "person" else "",
                 "of": header[of[k]] if of.get(k) is not None else "", "kind": kinds[k],
                 "source": source[k]}
        if roles[k] in OWNED_ROLES:
            entry["relation"] = holding.get(k, "")
        columns.append(entry)
    stem = os.path.splitext(os.path.basename(filename or ""))[0]
    return {"header": header, "sheet": name, "tables": [n for n, _ in tables],
            "rows": len(rows) - 1, "columns": columns, "relations": relations,
            "name": " ".join(stem.replace("_", " ").split())[:60], "model": model,
            "profile": {"id": profile["id"], "version": profile["version"]}}


# -- the accepted draft -> a new source ---------------------------------------------

def _text(value, where: str, limit: int = 120) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > limit:
        raise ProfileError("%s must be a text of at most %d characters" % (where, limit))
    return value.strip()


def _source_id(name: str, taken: set) -> str:
    base = _SLUG.sub("_", fold(name)).strip("_")[:30].strip("_") or "source"
    if not base[0].isalpha():
        base = "src_" + base
    sid, n = base, 2
    while sid in taken:
        sid, n = "%s_%d" % (base, n), n + 1
    return sid


def _identifier_subtype(profile: dict, role: str, place: bool = False) -> str:
    wanted = "place" if place else "identifier"
    subs = {s["id"]: s for s in profile["entity_subtypes"] if s["type"] == wanted}
    return role if role in subs else ""


def build_source(profile: dict, draft: dict, name: str) -> dict:
    """The profile's raw structured source from an accepted draft; ProfileError
    when the draft does not make one."""
    if not isinstance(draft, dict):
        raise ProfileError("the draft must be an object")
    name = _text(name, "the source's name", 60)
    raw_cols = draft.get("columns")
    if not isinstance(raw_cols, list) or not raw_cols or len(raw_cols) > MAX_COLUMNS:
        raise ProfileError("the draft needs its columns (at most %d)" % MAX_COLUMNS)
    cols, seen = [], set()
    for i, c in enumerate(raw_cols):
        if not isinstance(c, dict) or c.get("role") not in ROLES:
            raise ProfileError("column %d needs one of the roles %s" % (i + 1, ROLES))
        column = _text(c.get("column"), "column %d" % (i + 1))
        if fold(column) in seen:
            raise ProfileError("the column %r is listed twice" % column)
        seen.add(fold(column))
        cols.append(dict(c, column=column))
    by_name = {fold(c["column"]): c for c in cols}
    subtypes = _person_subtypes(profile)
    specs = _relation_specs(profile)

    ids = [c["column"] for c in cols if c["role"] == "record_id"]
    if not 1 <= len(ids) <= 5:
        raise ProfileError("choose 1..5 record id columns")
    refs: dict[str, str] = {}  # folded column -> entity ref
    entities: list[dict] = []
    taken = set()

    def ref_for(base):
        ref, n = base, 2
        while ref in taken:
            ref, n = "%s_%d" % (base, n), n + 1
        taken.add(ref)
        return ref

    persons = [c for c in cols if c["role"] == "person"]
    for c in persons:
        sub = c.get("subtype") or ""
        if sub and sub not in subtypes:
            raise ProfileError("%r: unknown person role %r" % (c["column"], sub))
        ent = {"ref": ref_for(sub or "person"), "type": "person", "name": c["column"],
               "attributes": {}}
        if sub:
            ent["subtype"] = sub
        refs[fold(c["column"])] = ent["ref"]
        entities.append(ent)
    by_ref = {e["ref"]: e for e in entities}

    def owner_of(c):
        of = c.get("of") or ""
        if of:
            owner = by_name.get(fold(of))
            if owner is None or owner["role"] != "person":
                raise ProfileError("%r belongs to %r, which is not a person column"
                                   % (c["column"], of))
            return refs[fold(owner["column"])]
        if len(persons) == 1:
            return refs[fold(persons[0]["column"])]
        if len(persons) > 1:
            raise ProfileError("%r: choose the person it belongs to" % c["column"])
        return None

    nameless = None
    for c in cols:
        if c["role"] not in ATTRIBUTE_ROLES:
            continue
        ref = owner_of(c)
        if ref is None:
            # A person named only by its registry number (no name column).
            if nameless is None:
                nameless = {"ref": ref_for("person"), "type": "person", "attributes": {}}
                entities.append(nameless)
                by_ref[nameless["ref"]] = nameless
            ref = nameless["ref"]
        attrs = by_ref[ref]["attributes"]
        if c["role"] in attrs:
            raise ProfileError("%r: the person already has a %s column" % (c["column"], c["role"]))
        attrs[c["role"]] = c["column"]
    for ent in entities:
        if ent.get("name") and all(a in ent["attributes"]
                                   for a in ("birth_date", "birth_place", "mother_name")):
            ent["person_key"] = True
        if not ent["attributes"]:
            del ent["attributes"]
    relations = []
    for c in cols:
        if c["role"] not in OWNED_ROLES:
            continue
        target = OWNED_ROLES[c["role"]]
        sub = _identifier_subtype(profile, c["role"], place=target == "place")
        ent = {"ref": ref_for(c["role"]), "type": target, "name": c["column"]}
        if sub:
            ent["subtype"] = sub
        entities.append(ent)
        refs[fold(c["column"])] = ent["ref"]
        rel = c.get("relation") or ""
        if rel:
            owner = owner_of(c)
            spec = specs.get(rel)
            if owner is None or spec is None or not _fits(spec, "person", target):
                raise ProfileError("%r: %s cannot link a person to it" % (c["column"], rel))
            relations.append({"type": rel, "from": owner, "to": ent["ref"]})

    roles = {fold(c["column"]): c["role"] for c in cols}
    assigned = set()

    def side_column(value, role, where):
        if not value:
            return None
        if roles.get(fold(value)) != role:
            raise ProfileError("%s: %r is not a %s column" % (where, value, role))
        assigned.add(fold(value))
        return by_name[fold(value)]["column"]

    raw_rels = draft.get("relations") or []
    if not isinstance(raw_rels, list) or len(raw_rels) > MAX_RELATIONS:
        raise ProfileError("at most %d relations" % MAX_RELATIONS)
    for i, r in enumerate(raw_rels):
        where = "relation %d" % (i + 1)
        if not isinstance(r, dict):
            raise ProfileError("%s must be an object" % where)
        spec = specs.get(r.get("type"))
        a, b = refs.get(fold(str(r.get("from") or ""))), refs.get(fold(str(r.get("to") or "")))
        if spec is None or a is None or b is None or a == b:
            raise ProfileError("%s needs a relation type and two different entity columns"
                               % where)
        ta, tb = _type_of(entities, a), _type_of(entities, b)
        if not _fits(spec, ta, tb):
            if not _fits(spec, tb, ta):
                raise ProfileError("%s: %s cannot go from %s to %s" % (where, spec["id"], ta, tb))
            a, b = b, a
        rel = {"type": spec["id"], "from": a, "to": b}
        start = side_column(r.get("start"), "start_date", where)
        end = side_column(r.get("end"), "end_date", where)
        label = side_column(r.get("label"), "relation_label", where)
        if start:
            rel["start"] = start
        if end:
            rel["end"] = end
        if label:
            rel["label"] = label
        relations.append(rel)
    deleted = [c["column"] for c in cols if c["role"] == "deleted"]
    if len(deleted) > 1:
        raise ProfileError("choose at most one deleted mark column")
    # Dates and labels no relation reads stay readable in the record's text.
    texts = [c["column"] for c in cols if c["role"] == "text"
             or c["role"] in ("start_date", "end_date", "relation_label")
             and fold(c["column"]) not in assigned]
    if not entities:
        raise ProfileError("choose at least one person, phone, e-mail or address column")
    out = {"id": _source_id(name, {s["id"] for s in profile.get("structured_sources") or []}),
           "label": {lang: name for lang in LANGS}, "title": name, "record_id": ids,
           "mode": draft.get("mode") if draft.get("mode") in domain_profile.SOURCE_MODES
           else "delta", "entities": entities, "relations": relations}
    if deleted:
        out["deleted"] = {"column": deleted[0], "values": list(DELETED_VALUES)}
    if texts:
        out["text_columns"] = texts
    if draft.get("rag") is True and any(c["role"] == "text" for c in cols):
        out["rag"] = True
    sheet = draft.get("sheet")
    if isinstance(sheet, str) and sheet.strip():
        out["sheet"] = sheet.strip()
    return out


def _type_of(entities: list[dict], ref: str) -> str:
    return next(e["type"] for e in entities if e["ref"] == ref)


def apply_draft(profile: dict, draft: dict, name: str) -> tuple[dict, dict]:
    """(next profile version with the new source, the validated source); not
    saved: the administrator saves it (profile/set validates it again)."""
    out = copy.deepcopy(profile)
    out["version"] = profile["version"] + 1
    source = build_source(profile, draft, name)
    out["structured_sources"] = list(out.get("structured_sources") or []) + [source]
    out = domain_profile.validate(out)
    return out, out["structured_sources"][-1]


def apply_aliases(profile: dict, source_id: str, aliases) -> tuple[dict, list[dict]]:
    """(next profile version, changes) with other names of a source's columns:
    {column of the source: the name it has in the file}."""
    out = copy.deepcopy(profile)
    out["version"] = profile["version"] + 1
    source = next((s for s in out.get("structured_sources") or [] if s["id"] == source_id), None)
    if source is None:
        raise ProfileError("the profile has no structured source %r" % source_id)
    if not isinstance(aliases, dict) or not aliases or len(aliases) > 50:
        raise ProfileError("aliases must map 1..50 columns of the source to their new names")
    columns = {fold(c): c for c in structured_import.source_columns(source)}
    current = source.setdefault("column_aliases", {})
    changes = []
    for column, alias in aliases.items():
        canonical = columns.get(fold(str(column)))
        if canonical is None:
            raise ProfileError("%r is not a column of the source %s" % (column, source_id))
        alias = _text(alias, "the new name of %r" % column)
        names = current.setdefault(canonical, [])
        if fold(alias) not in {fold(n) for n in names}:
            names.append(alias)
            changes.append({"column": canonical, "alias": alias})
    if not changes:
        raise ProfileError("no new column name")
    return domain_profile.validate(out), changes
