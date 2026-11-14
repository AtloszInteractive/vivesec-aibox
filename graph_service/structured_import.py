"""Periodic structured exports -> graph records, by configured mapping (E28).

The BV hands over the result set of an export run for a period (FŐNIX base
data, placements, the approved-contact list ...) as CSV or XLSX. A source in
the domain profile (`structured_sources`, domain_profile.py) says which column
is the record id, which columns make which entity and relation, the code lists
that turn codes into text, and the record's own access rule. The rows are read
by rule; the model is never asked, so there is no GPU time and no extraction
error.

Every record becomes its own graph document:
  doc_id       "rec:" + hash(corpus, folder, source, record id): the idempotency
               key -- a later export of the same record into the same folder
               replaces it; another folder's records are never touched;
  source_path  <folder>/~records/<source>/<hash>: under the folder the admin
               imported into, so the folder's access rule (`acl`, kept current
               by the adapter's ACL push) and a delete of the folder reach it;
  record_acl   the record's own rule (e.g. its institution's group), applied in
               addition to the folder's (visibility.Scope.allows);
  profile      "record:<source>@<as_of>": an older export never overwrites or
               deletes what a newer one wrote (a newer export of unchanged rows
               refreshes the date); a removed record stays as an empty document
               tagged ".../removed", so an older export cannot bring it back.
Its text is the row as "Column: value" pairs (codes with their meaning), the
verbatim evidence of every relation it states.

Modes (per source, BV decision pending, A8): "delta" (default) upserts the rows
and removes only rows marked deleted; "full" also removes the source's records
of that folder the export no longer lists -- refused when that would remove
more than `full_max_delete` of them, unless forced (a truncated file must not
wipe the register).

Persons: a registry number links as everywhere; a source entity with
`person_key` gets the salted key of its four natural identifiers
(person_key.py); `key_only` (contacts) links only through such a key, never by
name (resolution.py).

Protected part (E29, the informant of an intelligence log entry): a source's
`protected` block names entities (and extra columns) that only the profile's
`protected_principals` may see. Each record then becomes two documents: the
open one (everything else, with the record's own rule) and its protected twin
under <folder>/~records/<source>/~protected/, holding the protected entities
and every relation touching one, with a rule only a member passes
(visibility.Scope). The profile guarantees the open text never reads a
protected column. A protected person is never linked automatically nor
remembered by the resolver: it gives at most a review pair a member sees.
An event entity (the log entry) is named by its number, one per folder and
source, so the rows of one entry share it.
"""
from __future__ import annotations

import csv
import hashlib
import hmac
import io
import os
import re

from domain_profile import normalize_value
from extraction import LocalEntity, fold
from ingest import parse_document_payload
from model import KEY_ONLY_ATTR, PERSON_KEY_ATTR, PROTECTED_DIR, RECORD_KIND
from person_key import iso_date, person_key

RECORDS_DIR = "~records"
TAG_PREFIX = "record:"
MAX_ROWS = 200000
MAX_PROBLEMS = 50
_AS_OF = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_SAFE_VALUE = re.compile(r"^[A-Za-z0-9_.-]{1,60}$")


class StructuredImportError(ValueError):
    pass


# -- reading the file ---------------------------------------------------------------

def _cell(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if hasattr(value, "year") and hasattr(value, "month"):
        return iso_date(value)
    return " ".join(str(value).split())


def _decode(raw: bytes) -> str:
    for enc in ("utf-8-sig", "cp1250"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    raise StructuredImportError("the CSV is neither UTF-8 nor Windows-1250 text")


def read_table(raw: bytes, filename: str, sheet: str | None = None
               ) -> tuple[list[str], list[tuple[int, list[str]]]]:
    """(header, [(row number, cells)]) of a CSV/TSV or XLSX export; row numbers
    as the user sees them (the header is row 1 of a CSV)."""
    ext = os.path.splitext(filename or "")[1].lower()
    rows: list[tuple[int, list[str]]] = []
    if ext in (".xlsx", ".xlsm"):
        try:
            import openpyxl
        except ImportError:
            raise StructuredImportError("XLSX import needs openpyxl")
        try:
            wb = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
        except Exception as e:  # noqa: BLE001 - any parser failure is a bad file
            raise StructuredImportError("not a readable XLSX file: %s" % type(e).__name__)
        try:
            if sheet:
                names = {fold(n): n for n in wb.sheetnames}
                if fold(sheet) not in names:
                    raise StructuredImportError("the workbook has no sheet %r" % sheet)
                ws = wb[names[fold(sheet)]]
            else:
                ws = wb.worksheets[0]
            for n, values in enumerate(ws.iter_rows(values_only=True), 1):
                cells = [_cell(v) for v in values]
                if any(cells):
                    rows.append((n, cells))
                if len(rows) > MAX_ROWS + 1:
                    raise StructuredImportError("more than %d rows" % MAX_ROWS)
        finally:
            wb.close()
    elif ext in (".csv", ".tsv", ".txt"):
        text = _decode(raw)
        first = next((line for line in text.splitlines() if line.strip()), "")
        sep = max((";", "\t", ","), key=first.count) if ext != ".tsv" else "\t"
        if first.count(sep) < 1:
            raise StructuredImportError("the CSV header has no ';', ',' or tab separated columns")
        for n, cells in enumerate(csv.reader(io.StringIO(text), delimiter=sep), 1):
            cells = [_cell(c) for c in cells]
            if any(cells):
                rows.append((n, cells))
            if len(rows) > MAX_ROWS + 1:
                raise StructuredImportError("more than %d rows" % MAX_ROWS)
    else:
        raise StructuredImportError("only CSV, TSV and XLSX exports can be imported")
    if not rows:
        raise StructuredImportError("the file is empty")
    header = rows[0][1]
    folded = [fold(h) for h in header if h]
    if len(set(folded)) != len(folded):
        raise StructuredImportError("the header repeats a column name")
    return header, rows[1:]


# -- one record ----------------------------------------------------------------------

class Record:
    def __init__(self, record_id: str, row: int):
        self.record_id = record_id
        self.row = row
        self.deleted = False
        self.text = ""
        self.offsets: dict[int, tuple[int, int, str]] = {}  # column -> span in text
        # The protected part (E29): its own text, offsets, entities and relations.
        self.ptext = ""
        self.poffsets: dict[int, tuple[int, int, str]] = {}
        self.entities: dict[str, dict] = {}  # ref -> {type, subtype, name, attrs, id?, ...}
        self.relations: list[dict] = []
        self.acl_value = ""

    def has_protected(self) -> bool:
        return any(item.get("protected") for item in self.entities.values()) \
            or any(r.get("protected") for r in self.relations)


def doc_id_of(source_id: str, record_id: str, corpus_id: str = "", folder: str = "") -> str:
    """The record's idempotency key: source + record id, within one folder of one
    corpus (an import into another folder never touches these records)."""
    key = "%s\0%s\0%s\0%s" % (corpus_id, folder.rstrip("/"), source_id, record_id)
    return "rec:" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:24]


def path_of(folder: str, source_id: str, record_id: str) -> str:
    return "%s/%s/%s/%s" % (folder.rstrip("/"), RECORDS_DIR, source_id,
                            hashlib.sha1(record_id.encode("utf-8")).hexdigest()[:16])


def protected_doc_id(doc_id: str) -> str:
    """The protected part of the record `doc_id` (E29), its own document."""
    return "rec:" + hashlib.sha1((doc_id + "\0protected").encode("utf-8")).hexdigest()[:24]


def protected_path(path: str) -> str:
    """<folder>/~records/<source>/~protected/<hash> of the record at `path`."""
    head, tail = path.rsplit("/", 1)
    return "%s/%s/%s" % (head, PROTECTED_DIR, tail)


def is_protected_path(path: str) -> bool:
    return "/%s/" % RECORDS_DIR in path and "/%s/" % PROTECTED_DIR in path


def records_root(folder: str, source_id: str = "") -> str:
    root = "%s/%s" % (folder.rstrip("/"), RECORDS_DIR)
    return root + "/" + source_id if source_id else root


def tag_of(source_id: str, as_of: str) -> str:
    return "%s%s@%s" % (TAG_PREFIX, source_id, as_of)


# A removed record stays as an empty document with this tag suffix, so an older
# export that still lists it cannot bring it back.
REMOVED_SUFFIX = "/removed"


def as_of_of(tag: str) -> str:
    tag = tag[:-len(REMOVED_SUFFIX)] if tag.endswith(REMOVED_SUFFIX) else tag
    return tag.rsplit("@", 1)[1] if tag.startswith(TAG_PREFIX) and "@" in tag else ""


class Plan:
    """The validated reading of one export against its source: records by id and
    what did not fit, before anything is written."""

    def __init__(self):
        self.records: dict[str, Record] = {}
        self.rows = 0
        self.problems: list[dict] = []
        self.problem_count = 0
        self.duplicates = 0
        self.missing_columns: list[str] = []
        self.unknown_codes: dict[str, set[str]] = {}
        self.person_keys = 0

    def problem(self, row: int, reason: str) -> None:
        self.problem_count += 1
        if len(self.problems) < MAX_PROBLEMS:
            self.problems.append({"row": row, "reason": reason})


def _identifier_value(value: str, subtype: str, profile: dict) -> str:
    for p in profile["identifier_patterns"]:
        if p["subtype"] == subtype:
            return normalize_value(value, p["normalize"])
    return fold(value)


def _render(header, cell, decoded, cols) -> tuple[str, dict]:
    """A record's text, "Column: value" pairs of `cols` (a code with its meaning),
    and the span of each value in it (of the meaning, for a code)."""
    parts, offsets, pos = [], {}, 0
    for k in cols:
        value, h = cell(k), header[k]
        if not value:
            continue
        shown = value if not decoded.get(k) else "%s (%s)" % (value, decoded[k])
        part = "%s: %s" % (h, shown)
        start = pos + len(h) + 2
        offsets[k] = (start, start + len(shown), shown)
        if decoded.get(k):
            d0 = start + len(value) + 2
            offsets[k] = (d0, d0 + len(decoded[k]), decoded[k])
        parts.append(part)
        pos += len(part) + 2
    return "; ".join(parts), offsets


def plan_import(profile: dict, source: dict, header: list[str], rows, salt: bytes | None) -> Plan:
    plan = Plan()
    col = {fold(h): k for k, h in enumerate(header) if h}
    used = set()

    def need(name, required):
        if name is None:
            return None
        k = col.get(fold(name))
        if k is None:
            if required:
                raise StructuredImportError("the file has no %r column (source %s)"
                                            % (name, source["id"]))
            if name not in plan.missing_columns:
                plan.missing_columns.append(name)
            return None
        used.add(k)
        return k

    id_cols = [need(c, True) for c in source["record_id"]]
    deleted = source.get("deleted")
    del_col = need(deleted["column"], False) if deleted else None
    del_values = {fold(v) for v in deleted["values"]} if deleted else set()
    acl = source.get("acl") or {}
    acl_col = need(acl.get("column"), True) if acl.get("column") else None
    ents = []
    for e in source["entities"]:
        # A person must be there; a phone or address column may be missing (reported).
        ents.append(dict(e, name_col=need(e["name"], e["type"] == "person"),
                         attr_cols={a: need(c, False) for a, c in e["attributes"].items()}))
    rels = []
    for r in source["relations"]:
        rels.append(dict(r, start_col=need(r["start"], False), end_col=need(r["end"], False),
                         label_cols=[need(c, False) for c in r["label"]]))
    for c in source.get("text_columns") or []:
        need(c, False)
    # E29: the protected part's columns. `hidden` only its text shows; `shown` its
    # text shows as well: the open ends of its relations and their dates.
    protected = source.get("protected") or {}
    prot_refs = set(protected.get("entities") or [])
    hidden = {need(c, False) for c in protected.get("columns") or []}
    for e in ents:
        if e["ref"] in prot_refs:
            hidden |= {e["name_col"], *e["attr_cols"].values()}
    shown = set()
    for r in rels:
        r["protected"] = r["from"] in prot_refs or r["to"] in prot_refs
        if r["protected"]:
            hidden |= set(r["label_cols"])
            shown |= {r["start_col"], r["end_col"]}
            for ref in (r["from"], r["to"]):
                if ref not in prot_refs:
                    shown.add(next(e["name_col"] for e in ents if e["ref"] == ref))
    hidden.discard(None)
    shown = (shown - {None}) | hidden
    codelists = source["codelists"]

    def decode(value, name, row):
        if not name or not value:
            return value, ""
        text = codelists[name].get(value)
        if text is None:
            plan.unknown_codes.setdefault(name, set()).add(value[:40])
            return value, ""
        return value, text

    by_ref = {e["ref"]: e for e in ents}
    for n, cells in rows:
        plan.rows += 1

        def cell(k):
            return cells[k].strip() if k is not None and k < len(cells) else ""
        rid = "|".join(cell(k) for k in id_cols)
        if not all(cell(k) for k in id_cols):
            plan.problem(n, "no record id")
            continue
        rec = Record(rid, n)
        rec.deleted = del_col is not None and fold(cell(del_col)) in del_values
        rec.acl_value = cell(acl_col)
        # The record's text: every used column but the record id and the delete mark,
        # a code with its meaning; offsets of each value for the mentions.
        decoded: dict[int, str] = {}
        for e in ents:
            if e["name_col"] is not None and e.get("codelist"):
                raw, text = decode(cell(e["name_col"]), e["codelist"], n)
                decoded[e["name_col"]] = text
        for r in rels:
            for k in r["label_cols"]:
                if k is not None and r.get("label_codelist"):
                    decoded[k] = decode(cell(k), r["label_codelist"], n)[1]
        open_cols = [k for k in range(len(header))
                     if k in used and k not in id_cols and k != del_col and k not in hidden]
        rec.text, rec.offsets = _render(header, cell, decoded, open_cols)
        if prot_refs or hidden:
            rec.ptext, rec.poffsets = _render(header, cell, decoded, [
                k for k in range(len(header)) if k in shown and k not in id_cols and k != del_col])
        if rec.deleted:
            plan.records.setdefault(rid, rec)
            if plan.records[rid] is not rec:
                plan.duplicates += 1
                plan.records[rid] = rec
            continue
        for e in ents:
            name = decoded.get(e["name_col"]) or cell(e["name_col"])
            attrs = {}
            for a, k in e["attr_cols"].items():
                value = cell(k)
                if value:
                    attrs[a] = iso_date(value) or value if a == "birth_date" else value
            if not name and not attrs.get("registry_number"):
                continue
            item = {"type": e["type"], "subtype": e["subtype"], "name": name, "attrs": attrs,
                    "span_col": e["name_col"] if name else e["attr_cols"].get("registry_number"),
                    "key_only": e["key_only"], "protected": e["ref"] in prot_refs}
            if e["type"] == "identifier":
                item["value"] = _identifier_value(name, e["subtype"], profile)
                if not item["value"] or item["value"] == "+":
                    plan.problem(n, "%s: no usable value" % e["ref"])
                    continue
            if e["type"] == "person" and e["person_key"]:
                key = person_key(salt, name, attrs.get("birth_place", ""),
                                 attrs.get("birth_date", ""), attrs.get("mother_name", ""))
                if key:
                    item["person_key"] = key
                    plan.person_keys += 1
            rec.entities[e["ref"]] = item
        for ref, item in rec.entities.items():
            parent = by_ref[ref].get("within")
            chain = []
            while parent:
                if parent not in rec.entities:
                    chain = None  # a cell without its unit is no place to share
                    break
                chain.append(rec.entities[parent]["name"])
                parent = by_ref[parent].get("within")
            if chain is None:
                plan.problem(n, "%s without the place it lies in" % ref)
                item["skip"] = True
            elif chain:
                item["within"] = " / ".join(reversed(chain))
        rec.entities = {ref: item for ref, item in rec.entities.items() if not item.get("skip")}
        for r in rels:
            if r["from"] not in rec.entities or r["to"] not in rec.entities:
                continue
            start, end = iso_date(cell(r["start_col"])), iso_date(cell(r["end_col"]))
            if cell(r["start_col"]) and not start or cell(r["end_col"]) and not end:
                plan.problem(n, "%s: a date that is not a date" % r["type"])
            labels = [decoded.get(k) or cell(k) for k in r["label_cols"]]
            rec.relations.append({"type": r["type"], "from": r["from"], "to": r["to"],
                                  "start": start or None, "end": end or None,
                                  "label": " / ".join(x for x in labels if x)[:300],
                                  "protected": r["protected"]})
        for ref, item in rec.entities.items():
            parent = by_ref[ref].get("within")
            if parent:
                rec.relations.append({"type": "PART_OF", "from": ref, "to": parent,
                                      "start": None, "end": None, "label": "",
                                      "protected": item["protected"]
                                      or rec.entities[parent]["protected"]})
        if not any(not item["protected"] for item in rec.entities.values()):
            plan.problem(n, "nothing to record (no name in any mapped column)" if not rec.entities
                         else "only protected data (no name in any open column)")
            continue
        if rid in plan.records:
            plan.duplicates += 1
        plan.records[rid] = rec
    return plan


def record_acl(source: dict, value: str) -> dict | None:
    """The record's own rule: the groups its column value names, plus `always`;
    nobody when neither gives a principal (an unknown institution code)."""
    acl = source.get("acl")
    if not acl:
        return None
    allow = list(acl["always"])
    value = (value or "").strip()
    if acl.get("column") and value:
        allow += acl["groups"].get(value, [])
        if acl.get("pattern") and _SAFE_VALUE.match(value):
            allow.append(acl["pattern"].replace("{value}", value.casefold()))
    return {"restricted": True, "allow": sorted(set(allow)), "deny": [],
            "classification": acl["classification"]}


def protected_acl(profile: dict, source: dict) -> dict:
    """The rule of a record's protected part (E29): the profile's protected
    principals only, marked so the view checks the membership the profile names
    at query time (visibility.Scope)."""
    level = (source.get("acl") or {}).get("classification", 0)
    return {"restricted": True, "allow": list(profile.get("protected_principals") or []),
            "deny": [], "classification": level, "protected": True}


# -- writing --------------------------------------------------------------------------

def _place_id(item: dict, rec: Record, ref: str, by_ref: dict, ids: dict) -> str:
    own = "place:%s" % fold(item["name"])
    parent = by_ref[ref].get("within")
    return "%s@%s" % (own, ids[parent]) if parent else own


def build_payloads(profile: dict, source: dict, rec: Record, folder: str, corpus_id: str,
                   base_acl, resolver, sticky_of, salt: bytes | None = None
                   ) -> tuple[dict, dict | None]:
    """The ingest payloads of one record, its people resolved (the caller holds the
    write lock: the worker thread): the open part and the protected part (E29;
    None when the source has none or the row names nobody in it). Both name the
    same ids for what they share (the log entry an informant reported).

    A protected person is never resolved like the others: its id comes from the
    protected record alone (or a reviewer's merge), it is never remembered, and
    it gives at most a review pair only a protected member sees."""
    by_ref = {e["ref"]: e for e in source["entities"]}
    doc_id = doc_id_of(source["id"], rec.record_id, corpus_id, folder)
    path = path_of(folder, source["id"], rec.record_id)
    pdoc_id, ppath = protected_doc_id(doc_id), protected_path(path)
    holding = {r["id"] for r in profile["relation_types"] if r.get("holding")}
    events = hashlib.sha1(("%s\0%s\0%s" % (corpus_id, folder.rstrip("/"), source["id"])
                           ).encode("utf-8")).hexdigest()[:12]
    ids: dict[str, str] = {}
    order = sorted(rec.entities, key=lambda ref: _depth(ref, by_ref))
    for ref in order:
        item = rec.entities[ref]
        if item["type"] == "identifier":
            ids[ref] = "id:%s:%s" % (item["subtype"] or "other", item["value"])
        elif item["type"] == "place":
            ids[ref] = _place_id(item, rec, ref, by_ref, ids)
        elif item["type"] == "event":
            # One log entry number in one folder of one source is one event.
            ids[ref] = "event:rec%s:%s" % (events, fold(item["name"]))
        elif item["type"] != "person":
            ids[ref] = "%s:%s" % (item["type"], fold(item["name"]))
    resolver.sticky = sticky_of(doc_id, corpus_id, path)
    try:
        for ref in order:
            item = rec.entities[ref]
            if item["type"] != "person" or item["protected"]:
                continue
            contacts = {ids[r["to"]] for r in rec.relations
                        if r["from"] == ref and r["type"] in holding and r["to"] in ids}
            attrs = {k: (v, 1) for k, v in item["attrs"].items()}
            if item.get("person_key"):
                attrs[PERSON_KEY_ATTR] = (item["person_key"], 1)
            if item["key_only"]:
                attrs[KEY_ONLY_ATTR] = ("1", 1)
            ids[ref] = _resolve_person(resolver, doc_id, ref, item, attrs, contacts, salt)
    finally:
        resolver.sticky = {}
    for ref in order:
        item = rec.entities[ref]
        if item["type"] == "person" and item["protected"]:
            # What it is related to here: the same name on the same log entry is
            # one informant (one row per subject), elsewhere it is not.
            anchors = sorted({ids[r["to"] if r["from"] == ref else r["from"]]
                              for r in rec.relations if ref in (r["from"], r["to"])
                              and (r["to"] if r["from"] == ref else r["from"]) in ids})
            ids[ref] = _protected_person(resolver, pdoc_id, ref, item, salt, anchors)
    content = _payload(profile, source["title"], rec, doc_id, path, corpus_id, base_acl, ids,
                       [ref for ref in order if not rec.entities[ref]["protected"]],
                       [r for r in rec.relations if not r["protected"]], rec.text, rec.offsets)
    if not rec.has_protected():
        return content, None
    rels = [r for r in rec.relations if r["protected"]]
    refs = {ref for ref in order if rec.entities[ref]["protected"]} \
        | {x for r in rels for x in (r["from"], r["to"])}
    title = (source.get("protected") or {}).get("title") or source["title"]
    protected = _payload(profile, title, rec, pdoc_id, ppath, corpus_id, base_acl, ids,
                         [ref for ref in order if ref in refs], rels, rec.ptext, rec.poffsets,
                         open_refs={ref for ref in refs if not rec.entities[ref]["protected"]})
    return content, protected


def _payload(profile, title, rec, doc_id, path, corpus_id, base_acl, ids, refs, rels, text,
             offsets, open_refs=frozenset()) -> dict:
    """One document of a record: its entities (with what it states of them; of
    an open entity a protected part repeats only the mention), mentions and
    relations with the text as their evidence."""
    entities, mentions = {}, []
    for ref in refs:
        item = rec.entities[ref]
        gid = ids[ref]
        ent = entities.setdefault(gid, {"id": gid, "type": item["type"],
                                        "subtype": item["subtype"],
                                        "label": item["name"] or item["attrs"].get(
                                            "registry_number", ""), "attributes": []})
        if ref not in open_refs:
            for name, value in sorted(item["attrs"].items()):
                ent["attributes"].append({"name": name, "value": value, "page": 1})
            if item.get("person_key"):
                ent["attributes"].append({"name": PERSON_KEY_ATTR, "value": item["person_key"],
                                          "page": 1})
            if item["key_only"]:
                ent["attributes"].append({"name": KEY_ONLY_ATTR, "value": "1", "page": 1})
            if item.get("within"):
                ent["attributes"].append({"name": "within", "value": item["within"],
                                          "page": 1})
        span = offsets.get(item.get("span_col"))
        if span:
            mentions.append({"id": "%s:m%d" % (doc_id, len(mentions)), "entity_id": gid,
                             "text": span[2], "page": 1, "start": span[0], "end": span[1],
                             "method": "structured"})
    spec = {r["id"]: r for r in profile["relation_types"]}
    relations = {}
    for r in rels:
        a, b = ids[r["from"]], ids[r["to"]]
        if a == b:
            continue
        if spec[r["type"]]["symmetric"] and b < a:
            a, b = b, a
        key = (r["type"], a, b, r["start"], r["end"])
        if key in relations:
            continue
        rid = "%s:r%s" % (doc_id, hashlib.sha1(repr(key).encode("utf-8")).hexdigest()[:12])
        relations[key] = {"id": rid, "type": r["type"], "a": a, "b": b, "start": r["start"],
                          "end": r["end"], "raw_label": r["label"],
                          "evidence": [{"quote": text, "page": 1, "method": "structured",
                                        "confidence": 1.0}]}
    document = {"doc_id": doc_id, "corpus_id": corpus_id, "source_path": path,
                "title": title, "kind": RECORD_KIND, "pages": 1}
    if base_acl is not None:
        document["acl"] = base_acl
    return {"document": document, "entities": list(entities.values()), "mentions": mentions,
            "relations": list(relations.values())}


def _depth(ref: str, by_ref: dict) -> int:
    n, cur = 0, by_ref[ref].get("within")
    while cur:
        n += 1
        cur = by_ref[cur].get("within")
    return n


def _resolve_person(resolver, doc_id, ref, item, attrs, contacts, salt=None) -> str:
    """A record's person: with a name through the resolver; named only by a
    registry number, the one person already holding it (or a new one). A new
    person's id comes from its person key or (salted, since ids are shown) its
    registry number, so every import of it gives the same id."""
    reg = item["attrs"].get("registry_number")
    if item.get("person_key"):
        new_id = "person:" + hashlib.sha1(("key\0" + item["person_key"]).encode()).hexdigest()[:16]
    elif reg and salt:
        new_id = "person:" + hmac.new(salt, ("reg\0" + fold(reg)).encode("utf-8"),
                                      hashlib.sha256).hexdigest()[:16]
    else:
        new_id = ""
    if not item["name"]:
        with resolver.lock:
            holders = resolver.by_key.get("attr:registry_number:%s" % fold(reg), set())
            pid = next(iter(holders)) if len(holders) == 1 else (
                new_id or "person:" + hashlib.sha1(("%s\0%s" % (doc_id, ref)).encode()
                                                   ).hexdigest()[:16])
            pid = resolver.survivor(pid)
            resolver.remember_person(pid, [], {k: v for k, (v, _) in attrs.items()},
                                     doc_id=doc_id)
        return pid
    ent = LocalEntity(ref, "person", item["subtype"], [item["name"]], attrs, method="structured")
    return resolver.resolve(doc_id, ent, frozenset(), frozenset(contacts), item["key_only"],
                            new_id=new_id)


def _protected_person(resolver, doc_id, ref, item, salt=None, anchors=()) -> str:
    """A person of a protected record part (E29). Its id comes from the record
    alone -- its registry number (salted, in a namespace of its own) or person
    key, so one informant of many entries is one person to a protected member;
    without them its name on the same entities (`anchors`: the log entry), so
    the rows of one entry name one informant -- or from a reviewer's merge of
    it; it is never matched or remembered, only suggested for review
    (resolution.Resolver.suggest)."""
    reg = item["attrs"].get("registry_number")
    if reg and salt:
        new_id = "person:" + hmac.new(salt, ("protected-reg\0" + fold(reg)).encode("utf-8"),
                                      hashlib.sha256).hexdigest()[:16]
    elif item.get("person_key"):
        new_id = "person:" + hashlib.sha1(("protected-key\0" + item["person_key"]).encode()
                                          ).hexdigest()[:16]
    elif item["name"] and anchors:
        new_id = "person:" + hashlib.sha1("\0".join(["protected-name", fold(item["name"]),
                                                     *anchors]).encode("utf-8")).hexdigest()[:16]
    else:
        new_id = "person:" + hashlib.sha1(("%s\0%s" % (doc_id, ref)).encode()).hexdigest()[:16]
    pid = resolver.survivor(new_id)
    if pid == new_id:
        attrs = {k: (v, 1) for k, v in item["attrs"].items()}
        if item.get("person_key"):
            attrs[PERSON_KEY_ATTR] = (item["person_key"], 1)
        names = [item["name"]] if item["name"] else []
        resolver.suggest(doc_id, LocalEntity(ref, "person", item["subtype"], names, attrs,
                                             method="structured"), pid)
    return pid


def parse_record_payload(payload: dict, profile: dict, source: dict, as_of: str, rec: Record,
                         protected: bool = False):
    doc, entities, mentions, relations = parse_document_payload(payload, profile,
                                                                allow_hidden=True)
    doc.kind = RECORD_KIND
    doc.profile = tag_of(source["id"], as_of)
    text = rec.ptext if protected else rec.text
    doc.content_sha256 = hashlib.sha256(text.encode("utf-8")).hexdigest()
    doc.record_acl = protected_acl(profile, source) if protected \
        else record_acl(source, rec.acl_value)
    return doc, entities, mentions, relations
