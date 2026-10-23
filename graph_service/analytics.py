"""Analytic queries over the caller's visible subgraph (E26): patterns, counts, sequences.

Domain-agnostic: kinds and relations come from the request (the adapter maps
them from the active domain profile). A side filter is
{"types": [...], "subtypes": [...], "ids": [...]}; an empty filter matches any
entity. Relations are matched in either direction unless a query says
otherwise, because "A visited B" and "B was visited by A" are the same fact
for an analyst. Like queries.py, everything runs on the view, so results,
counts and samples never reflect hidden documents.
"""
from __future__ import annotations

import datetime

MAX_STEPS = 3
MAX_PATHS = 200
MAX_ROWS = 100
MAX_PAIRS = 200
SAMPLES = 3
GROUPS = ("focus", "other", "month", "year", "relation", "other_kind")


def _matches(node: dict | None, spec: dict | None) -> bool:
    if node is None:
        return False
    spec = spec or {}
    if spec.get("ids") and node["id"] not in spec["ids"]:
        return False
    if spec.get("types") and node["type"] not in spec["types"]:
        return False
    if spec.get("subtypes") and node.get("subtype") not in spec["subtypes"]:
        return False
    return True


def _in_period(edge: dict, date_from: str | None, date_to: str | None) -> bool:
    if not date_from and not date_to:
        return True
    start = edge.get("start")
    if not start:
        return False
    end = edge.get("end") or start
    return not (date_from and end < date_from) and not (date_to and start > date_to)


def _relation_ok(edge: dict, relations) -> bool:
    return not relations or edge["type"] in relations


def _orientations(view, edge: dict, focus: dict | None, other: dict | None) -> list:
    """Every (focus id, other id) reading of the edge that fits the filters: both
    when either end may be the focus (a person-to-person visit seen from each side)."""
    out = []
    for f, o in ((edge["a"], edge["b"]), (edge["b"], edge["a"])):
        if (f, o) not in out and _matches(view.node(f), focus) \
                and _matches(view.node(o), other):
            out.append((f, o))
    return out


def _candidate_edges(view, focus: dict | None):
    """Edges worth scanning: those of the named entities, else the whole visible graph."""
    if focus and focus.get("ids"):
        seen = set()
        for eid in focus["ids"]:
            for edge in view.edges_of(eid):
                if edge["id"] not in seen:
                    seen.add(edge["id"])
                    yield edge
    else:
        yield from view.edges()


def _date(value: str | None) -> datetime.date | None:
    if not value:
        return None
    parts = value[:10].split("-")
    try:
        return datetime.date(int(parts[0]), int(parts[1]) if len(parts) > 1 else 1,
                             int(parts[2]) if len(parts) > 2 else 1)
    except (ValueError, IndexError):
        return None


def match(view, start: dict | None, steps: list[dict], date_from: str | None = None,
          date_to: str | None = None, limit: int = 50) -> list[dict]:
    """Chains start -[relation]- target -[relation]- target ...; each step is
    {"relations": [...], "target": spec}. Returns paths of nodes and edges."""
    steps = steps[:MAX_STEPS]
    if not steps:
        return []
    limit = max(1, min(limit, MAX_PATHS))
    first = steps[0]
    paths = []
    for edge in _candidate_edges(view, start):
        if not _relation_ok(edge, first.get("relations")) or not _in_period(edge, date_from, date_to):
            continue
        for f, o in _orientations(view, edge, start, first.get("target")):
            paths.append(([f, o], [edge]))
    for step in steps[1:]:
        grown = []
        for nodes, edges in paths:
            for edge in view.edges_of(nodes[-1]):
                if edge["id"] in {e["id"] for e in edges}:
                    continue
                if not _relation_ok(edge, step.get("relations")) \
                        or not _in_period(edge, date_from, date_to):
                    continue
                nxt = edge["b"] if edge["a"] == nodes[-1] else edge["a"]
                if nxt in nodes or not _matches(view.node(nxt), step.get("target")):
                    continue
                grown.append((nodes + [nxt], edges + [edge]))
                if len(grown) >= MAX_PATHS * 5:
                    break
        paths = grown
    paths.sort(key=lambda p: (p[1][0].get("start") or "", [view.node(n)["label"] for n in p[0]]))
    return [{"nodes": [view.node(n) for n in nodes], "edges": edges}
            for nodes, edges in paths[:limit]]


def aggregate(view, relations=None, focus: dict | None = None, other: dict | None = None,
              group_by: str = "focus", date_from: str | None = None, date_to: str | None = None,
              limit: int = 20) -> list[dict]:
    """Counts the matching relations per group, largest first, with sample edges."""
    if group_by not in GROUPS:
        raise ValueError("group_by must be one of %s" % ", ".join(GROUPS))
    rows: dict[str, dict] = {}
    for edge in _candidate_edges(view, focus):
        if not _relation_ok(edge, relations) or not _in_period(edge, date_from, date_to):
            continue
        pairs = _orientations(view, edge, focus, other)
        # Per entity, a link counts for each end that fits; per period or kind, once.
        for f, o in pairs if group_by in ("focus", "other") else pairs[:1]:
            _count(view, rows, edge, f, o, group_by)
    ordered = sorted(rows.values(), key=lambda r: (-r["count"], str(r["label"])))
    if group_by in ("month", "year"):
        ordered = sorted(rows.values(), key=lambda r: r["key"])
    out = []
    for row in ordered[:max(1, min(limit, MAX_ROWS))]:
        row["distinct"] = len(row.pop("others"))
        row["nodes"] = [view.node(x) for x in sorted({y for e in row["samples"]
                                                      for y in (e["a"], e["b"])})]
        out.append(row)
    return out


def _count(view, rows: dict, edge: dict, f: str, o: str, group_by: str) -> None:
    if group_by in ("month", "year"):
        if not edge.get("start"):
            return
        key = edge["start"][:7] if group_by == "month" else edge["start"][:4]
        label, node = key, None
    elif group_by == "relation":
        key = label = edge["type"]
        node = None
    elif group_by == "other_kind":
        n = view.node(o)
        key = label = n.get("subtype") or n["type"]
        node = None
    else:
        node = view.node(f if group_by == "focus" else o)
        key, label = node["id"], node["label"]
    row = rows.setdefault(key, {"key": key, "label": label, "node": node, "count": 0,
                                "others": set(), "samples": []})
    row["count"] += 1
    row["others"].add(o if group_by != "other" else f)
    if len(row["samples"]) < SAMPLES:
        row["samples"].append(edge)


def sequence(view, first: dict, then: dict, within_days: int = 30, focus: dict | None = None,
             date_from: str | None = None, date_to: str | None = None,
             limit: int = 50) -> list[dict]:
    """Pairs of dated relations of one focus entity where `then` follows `first`
    within `within_days`. `first`/`then` are {"relations": [...], "other": spec}."""
    pairs = []
    for e1 in _candidate_edges(view, focus):
        if not _relation_ok(e1, first.get("relations")) or not _in_period(e1, date_from, date_to):
            continue
        d1 = _date(e1.get("start"))
        if d1 is None:
            continue
        for pivot, first_other in _orientations(view, e1, focus, first.get("other")):
            _follow(view, pairs, e1, d1, pivot, first_other, then, within_days)
        if len(pairs) >= MAX_PAIRS * 5:
            break
    pairs.sort(key=lambda p: (p["first"]["start"], p["days"], p["focus"]["label"]))
    return pairs[:max(1, min(limit, MAX_PAIRS))]


def _follow(view, pairs: list, e1: dict, d1, pivot: str, first_other: str, then: dict,
            within_days: int) -> None:
    for e2 in view.edges_of(pivot):
        if e2["id"] == e1["id"] or not _relation_ok(e2, then.get("relations")):
            continue
        d2 = _date(e2.get("start"))
        if d2 is None:
            continue
        gap = (d2 - d1).days
        if not 0 <= gap <= within_days:
            continue
        o2 = e2["b"] if e2["a"] == pivot else e2["a"]
        if not _matches(view.node(o2), then.get("other")):
            continue
        pairs.append({"focus": view.node(pivot), "first": e1, "then": e2, "days": gap,
                      "nodes": [view.node(first_other), view.node(o2)]})
