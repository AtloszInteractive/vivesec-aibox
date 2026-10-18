"""Tables -> graph by column mapping: one model call per table layout (E26).

Logs and register extracts (visits, calls, placements, subscribers ...) repeat
the same layout across thousands of files. Instead of asking the model about
every row, the model sees the header and a few sample rows ONCE and says what
each column is and which columns relate; the rows are then turned into
entities and relations by rule. The mapping is cached by header, so a layout
costs one call per run, and it is kept as JSON an administrator can inspect
(and later pin in the profile).

Recognised table renderings (as the RAG extractor produces them):
  delimited  CSV/TSV text: a header line and rows split by ';', ',' or tab;
  sheet      xlsx: `Columns: A | B` then rows `A: x; B: y` (empty cells absent);
  markdown   `| A | B |` / `| --- |` pipe tables (DOCX, PDF conversions).
"""
from __future__ import annotations

import csv
import io
import re
import threading
from dataclasses import dataclass, field

from domain_profile import normalize_value
from extraction import (ATTRIBUTE_KEYS, LocalEntity, LocalRelation, _dates_in, fold, squash)
from model import CORE_TYPES

SAMPLE_ROWS = 6
SHEET_LINE = "Sheet: "
COLUMNS_LINE = "Columns: "


@dataclass
class Row:
    start: int  # offset of the row line in the page text
    text: str
    cells: dict[int, str]


@dataclass
class Table:
    page: int
    start: int  # first character of the table (header) in the page text
    end: int
    header: list[str]
    rows: list[Row] = field(default_factory=list)

    @property
    def signature(self) -> str:
        return " | ".join(fold(h) for h in self.header)


# -- finding tables in page text --------------------------------------------------

def _lines(text: str) -> list[tuple[int, str]]:
    out, pos = [], 0
    for line in text.split("\n"):
        out.append((pos, line))
        pos += len(line) + 1
    return out


def _split(line: str, sep: str) -> list[str]:
    return [c.strip() for c in next(csv.reader(io.StringIO(line), delimiter=sep))]


def _delimited(page: int, lines: list[tuple[int, str]], i: int) -> tuple[Table | None, int]:
    head = lines[i][1]
    for sep in (";", "\t", ","):
        n = head.count(sep)
        if n < 2:
            continue
        j = i + 1
        while j < len(lines) and lines[j][1].count(sep) >= max(1, n - 1) and lines[j][1].strip():
            j += 1
        if j - i - 1 < 2:
            continue
        header = _split(head, sep)
        if (not all(header) or len(set(map(fold, header))) != len(header)
                or max(map(len, header)) > 60):
            continue
        table = Table(page, lines[i][0], lines[j - 1][0] + len(lines[j - 1][1]), header)
        for pos, line in lines[i + 1:j]:
            cells = {k: v for k, v in enumerate(_split(line, sep)) if k < len(header) and v}
            table.rows.append(Row(pos, line, cells))
        return table, j
    return None, i + 1


def _sheet(page: int, lines: list[tuple[int, str]], i: int) -> tuple[Table | None, int]:
    header = [h.strip() for h in lines[i][1][len(COLUMNS_LINE):].split(" | ")]
    if not all(header):
        return None, i + 1
    labels = sorted(((h, k) for k, h in enumerate(header)), key=lambda x: -len(x[0]))
    table = Table(page, lines[i][0], lines[i][0] + len(lines[i][1]), header)
    j = i + 1
    while j < len(lines) and lines[j][1].strip() and not lines[j][1].startswith(SHEET_LINE):
        pos, line = lines[j]
        cells, rest = {}, line
        # "A: x; B: y" - split at known column labels so a value may contain "; ".
        marks = []
        for label, k in labels:
            for m in re.finditer(r"(?:^|; )" + re.escape(label) + ": ", line):
                if not any(s <= m.start() < e for s, e, _ in marks):
                    marks.append((m.start(), m.end(), k))
        marks.sort()
        for n, (s, e, k) in enumerate(marks):
            stop = marks[n + 1][0] if n + 1 < len(marks) else len(rest)
            value = line[e:stop].strip()
            if value:
                cells[k] = value
        if cells:
            table.rows.append(Row(pos, line, cells))
            table.end = pos + len(line)
        j += 1
    return (table if len(table.rows) >= 1 else None), j


def _markdown(page: int, lines: list[tuple[int, str]], i: int) -> tuple[Table | None, int]:
    if i + 1 >= len(lines) or not re.match(r"^\s*\|?\s*:?-{3,}", lines[i + 1][1]):
        return None, i + 1
    header = [c.strip() for c in lines[i][1].strip().strip("|").split("|")]
    if not all(header):
        return None, i + 1
    table = Table(page, lines[i][0], lines[i + 1][0] + len(lines[i + 1][1]), header)
    j = i + 2
    while j < len(lines) and lines[j][1].lstrip().startswith("|"):
        pos, line = lines[j]
        cells = {k: v.strip() for k, v in enumerate(line.strip().strip("|").split("|"))
                 if k < len(header) and v.strip()}
        table.rows.append(Row(pos, line, cells))
        table.end = pos + len(line)
        j += 1
    return (table if table.rows else None), j


def find_tables(page: int, text: str) -> list[Table]:
    lines = _lines(text)
    out, i = [], 0
    while i < len(lines):
        line = lines[i][1]
        if line.startswith(COLUMNS_LINE):
            table, i = _sheet(page, lines, i)
        elif line.lstrip().startswith("|"):
            table, i = _markdown(page, lines, i)
        elif line.count(";") >= 2 or line.count("\t") >= 2 or line.count(",") >= 2:
            table, i = _delimited(page, lines, i)
        else:
            table, i = None, i + 1
        if table and table.rows:
            out.append(table)
    return out


def blank_tables(text: str, tables: list[Table]) -> str:
    """The page text with table areas blanked (offsets kept) for the prose model."""
    chars = list(text)
    for t in tables:
        for k in range(t.start, min(t.end, len(chars))):
            if chars[k] != "\n":
                chars[k] = " "
    return "".join(chars)


# -- the mapping -------------------------------------------------------------------

def _mapping_schema(profile: dict) -> dict:
    rel_ids = [r["id"] for r in profile["relation_types"] if r["id"] != "CO_MENTIONED"]
    column = {"type": "object", "properties": {
        "column": {"type": "string"},
        "role": {"type": "string", "enum": ["entity", "attribute", "date", "end_date",
                                            "relation_label", "ignore"]},
        "entity_type": {"type": "string", "enum": list(CORE_TYPES)},
        "subtype": {"type": "string"},
        "attribute": {"type": "string", "enum": list(ATTRIBUTE_KEYS)},
        "of": {"type": "string"}},
        "required": ["column", "role"]}
    relation = {"type": "object", "properties": {
        "type": {"type": "string", "enum": rel_ids}, "from": {"type": "string"},
        "to": {"type": "string"}}, "required": ["type", "from", "to"]}
    return {"type": "object", "properties": {
        "columns": {"type": "array", "items": column},
        "relations": {"type": "array", "items": relation}}, "required": ["columns", "relations"]}


def _mapping_prompt(profile: dict) -> str:
    subtypes = "; ".join("%s (%s)" % (s["id"], s["type"]) for s in profile["entity_subtypes"])
    rels = "\n".join("- %s: %s -> %s. %s" % (r["id"], "/".join(r["from"]), "/".join(r["to"]),
                                             r["label"]["en"])
                     for r in profile["relation_types"] if r["id"] != "CO_MENTIONED")
    return (
        "You map the columns of a table to a knowledge graph. You see the header and sample "
        "rows; the same mapping is applied to every row.\n"
        "For EVERY column give one entry with `column` = the header text exactly and a role:\n"
        "- entity: the cell names a person, organization, place, identifier (phone, ID card, "
        "plate, account, case number) or object; give entity_type and, if one fits, subtype "
        "from: " + subtypes + "\n"
        "- attribute: a property of the entity in column `of` (attribute: one of "
        + ", ".join(ATTRIBUTE_KEYS) + ")\n"
        "- date / end_date: when the row's relations happened / ended\n"
        "- relation_label: free text describing the relation of the row (e.g. kinship)\n"
        "- ignore: anything else (durations, counts, remarks, serial numbers)\n"
        "Then list the relations each row states between entity columns (`from`/`to` are "
        "column headers), using only these types:\n" + rels + "\n"
        "Use RELATED_TO only when none fits. Return JSON only."
    )


@dataclass
class Mapping:
    entities: dict[int, tuple[str, str]]  # column -> (type, subtype)
    attributes: dict[int, tuple[int, str]]  # column -> (entity column, attribute)
    date: int | None
    end_date: int | None
    label: int | None
    relations: list[tuple[str, int, int]]  # (type, from column, to column)

    def to_json(self, header: list[str]) -> dict:
        """The validated mapping in the same form parse_mapping reads (pinnable)."""
        columns = []
        for k, h in enumerate(header):
            if k in self.entities:
                columns.append({"column": h, "role": "entity", "entity_type": self.entities[k][0],
                                "subtype": self.entities[k][1]})
            elif k in self.attributes:
                columns.append({"column": h, "role": "attribute",
                                "attribute": self.attributes[k][1],
                                "of": header[self.attributes[k][0]]})
            elif k == self.date:
                columns.append({"column": h, "role": "date"})
            elif k == self.end_date:
                columns.append({"column": h, "role": "end_date"})
            elif k == self.label:
                columns.append({"column": h, "role": "relation_label"})
            else:
                columns.append({"column": h, "role": "ignore"})
        return {"header": header, "columns": columns,
                "relations": [{"type": t, "from": header[a], "to": header[b]}
                              for t, a, b in self.relations]}


def parse_mapping(raw: dict, header: list[str], profile: dict) -> Mapping:
    """Validate a model (or administrator) mapping against the header and the profile."""
    col = {fold(h): k for k, h in enumerate(header)}
    subtypes = {s["id"]: s["type"] for s in profile["entity_subtypes"]}
    specs = {r["id"]: r for r in profile["relation_types"] if r["id"] != "CO_MENTIONED"}
    m = Mapping({}, {}, None, None, None, [])
    pending_attrs = []
    for c in raw.get("columns") or []:
        if not isinstance(c, dict):
            continue
        k = col.get(fold(str(c.get("column") or "")))
        if k is None:
            continue
        role = c.get("role")
        if role == "entity" and c.get("entity_type") in CORE_TYPES and c["entity_type"] != "event":
            sub = c.get("subtype") or ""
            m.entities[k] = (c["entity_type"], sub if subtypes.get(sub) == c["entity_type"] else "")
        elif role == "attribute" and c.get("attribute") in ATTRIBUTE_KEYS:
            pending_attrs.append((k, str(c.get("of") or ""), c["attribute"]))
        elif role in ("date", "end_date") and getattr(m, role) is None:
            setattr(m, role, k)
        elif role == "relation_label" and m.label is None:
            m.label = k
    for k, of, attr in pending_attrs:
        owner = col.get(fold(of))
        if owner is None:
            people = [c for c, (t, _) in m.entities.items() if t == "person"]
            owner = people[0] if len(people) == 1 else None
        if owner in m.entities and m.entities[owner][0] == "person":
            m.attributes[k] = (owner, attr)
    for r in raw.get("relations") or []:
        if not isinstance(r, dict) or r.get("type") not in specs:
            continue
        a, b = col.get(fold(str(r.get("from") or ""))), col.get(fold(str(r.get("to") or "")))
        if a not in m.entities or b not in m.entities or a == b:
            continue
        spec = specs[r["type"]]
        ta, tb = m.entities[a][0], m.entities[b][0]
        if ta not in spec["from"] or tb not in spec["to"]:
            if tb in spec["from"] and ta in spec["to"]:
                a, b = b, a
            else:
                continue
        if (r["type"], a, b) not in m.relations:
            m.relations.append((r["type"], a, b))
    return m


class TableMapper:
    """Header signature -> Mapping, asked from the model once per layout."""

    def __init__(self, profile: dict, llm, pinned: dict | None = None):
        self.profile = profile
        self.llm = llm
        self.lock = threading.Lock()
        self.cache: dict[str, Mapping | None] = {}
        self.headers: dict[str, list[str]] = {}
        for raw in (pinned or {}).values():
            sig = " | ".join(fold(h) for h in raw["header"])
            self.cache[sig] = parse_mapping(raw, raw["header"], profile)
            self.headers[sig] = raw["header"]

    def mapping_for(self, table: Table, title: str = "") -> Mapping | None:
        sig = table.signature
        with self.lock:
            if sig in self.cache:
                return self.cache[sig]
        sample = "\n".join(r.text for r in table.rows[:SAMPLE_ROWS])
        user = "Document: %s\nHeader: %s\nSample rows:\n%s" % (
            title, " | ".join(table.header), sample)
        raw = self.llm.json_call(_mapping_prompt(self.profile), user,
                                 _mapping_schema(self.profile), max_tokens=2048)
        mapping = parse_mapping(raw, table.header, self.profile)
        if not mapping.entities:
            mapping = None
        with self.lock:
            self.cache[sig] = mapping
            self.headers[sig] = table.header
        return mapping

    def export(self) -> dict:
        return {sig: m.to_json(self.headers[sig]) for sig, m in self.cache.items() if m}


def _identifier_value(cell: str, subtype: str, profile: dict) -> str:
    for p in profile["identifier_patterns"]:
        if p["subtype"] == subtype:
            return normalize_value(cell, p["normalize"])
    return fold(cell)


def apply_mapping(table: Table, mapping: Mapping, profile: dict, prefix: str
                  ) -> tuple[list[LocalEntity], list[LocalRelation]]:
    """Entities and relations of every row; refs are `<prefix>.<row>.<column>`."""
    ents, rels = [], []
    for n, row in enumerate(table.rows):
        refs = {}
        for k, (type_, sub) in mapping.entities.items():
            cell = row.cells.get(k)
            if not cell or (type_ == "person" and not any(w[:1].isupper() for w in cell.split())):
                continue
            e = LocalEntity("%s.%d.%d" % (prefix, n, k), type_, sub, [cell], method="structured")
            if type_ == "identifier":
                e.value = _identifier_value(cell, sub, profile)
            refs[k] = e
        for k, (owner, attr) in mapping.attributes.items():
            cell = row.cells.get(k)
            if cell and owner in refs:
                if attr == "birth_date":
                    cell = next(iter(sorted(_dates_in(cell))), "")
                if cell:
                    refs[owner].attrs[attr] = (cell, table.page)
        ents += refs.values()
        start = end = None
        if mapping.date is not None and row.cells.get(mapping.date):
            start = next(iter(sorted(_dates_in(row.cells[mapping.date]))), None)
        if mapping.end_date is not None and row.cells.get(mapping.end_date):
            end = next(iter(sorted(_dates_in(row.cells[mapping.end_date]))), None)
        label = row.cells.get(mapping.label, "") if mapping.label is not None else ""
        for type_, a, b in mapping.relations:
            if a in refs and b in refs:
                rels.append(LocalRelation(type_, refs[a].ref, refs[b].ref, squash(row.text),
                                          table.page, "structured", start, end, label[:120],
                                          confidence=0.9))
    return ents, rels
