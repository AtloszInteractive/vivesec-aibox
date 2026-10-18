"""Query templates over the caller's visible subgraph (E26).

Every function takes a view (graph_store.MemoryView or the Neo4j view) and
only ever sees what the view exposes, so counts, paths, communities and
centrality are computed on the visible part of the graph alone and never
hint at hidden nodes or edges.
"""
from __future__ import annotations

import re
import unicodedata
from collections import Counter, deque

from model import IDENTIFIER, ORGANIZATION, PERSON

MAX_HOPS = 8
MAX_RESULTS = 500
# A name's last word may carry a case ending in the question ("Kovács Jánoshoz").
MAX_SUFFIX = 5
MIN_NAME_CHARS = 4


def fold(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", text.casefold())
                   if unicodedata.category(c) != "Mn")


def _other(edge: dict, eid: str) -> str:
    return edge["b"] if edge["a"] == eid else edge["a"]


def find_entities(view, text: str, types=None, limit: int = 20) -> list[dict]:
    """Accent- and case-insensitive name lookup; every token must match."""
    tokens = fold(text).split()
    if not tokens:
        return []
    hits = []
    for n in view.nodes(types):
        names = [fold(x) for x in [n["label"], *n["aliases"]]]
        best = 0
        for name in names:
            words = name.split()
            if all(any(w.startswith(t) for w in words) for t in tokens):
                best = max(best, 2 if name == " ".join(tokens) else 1)
        if best:
            hits.append((-best, -n["mentions"], n["label"], n))
    hits.sort(key=lambda h: h[:3])
    return [h[3] for h in hits[:max(1, min(limit, MAX_RESULTS))]]


def mentioned_entities(view, text: str, patterns=(), types=None, limit: int = 10) -> list[dict]:
    """Visible entities a free-text question names, in order of appearance.

    Names match word by word (accent- and case-insensitive); the last word may
    carry a short ending, the others must match exactly. Identifiers are found
    with the profile's patterns and looked up by normalized value.
    `patterns`: [(subtype, compiled regex, normalizer)].
    """
    tokens = [(m.start(), m.group(0)) for m in re.finditer(r"\w+", fold(text))]
    words_at = [w for _, w in tokens]
    prefixes = {w[:k] for w in words_at for k in range(3, len(w) + 1)}
    found: dict[str, tuple[int, int, dict]] = {}
    for n in view.nodes(types):
        for name in {n["label"], *n["aliases"]}:
            words = fold(name).split()
            if not words or len(" ".join(words)) < MIN_NAME_CHARS or words[-1] not in prefixes:
                continue
            size = len(words)
            for i in range(len(words_at) - size + 1):
                window = words_at[i:i + size]
                last = window[-1]
                if window[:-1] == words[:-1] and last.startswith(words[-1]) \
                        and len(last) - len(words[-1]) <= MAX_SUFFIX:
                    pos = tokens[i][0]
                    if n["id"] not in found or (-size, pos) < found[n["id"]][:2]:
                        found[n["id"]] = (-size, pos, n)
                    break
    for subtype, rx, normalizer in patterns:
        for m in rx.finditer(text):
            node = view.node("id:%s:%s" % (subtype, normalizer(m.group(0))))
            if node is not None and node["id"] not in found:
                found[node["id"]] = (-9, m.start(), node)
    ordered = sorted(found.values(), key=lambda x: (x[1], x[0]))
    return [n for _, _, n in ordered[:max(1, min(limit, MAX_RESULTS))]]


def shortest_path(view, a: str, b: str, max_hops: int = 6) -> dict | None:
    """Bidirectional BFS; returns nodes and the edges (with evidence) between them."""
    if view.node(a) is None or view.node(b) is None:
        return None
    if a == b:
        return {"nodes": [view.node(a)], "edges": [], "hops": 0}
    max_hops = max(1, min(max_hops, MAX_HOPS))
    prev = {a: None}
    nxt = {b: None}
    front_a, front_b = [a], [b]
    meet = None
    for _ in range(max_hops):
        grow_a = len(front_a) <= len(front_b)
        front, seen, other = (front_a, prev, nxt) if grow_a else (front_b, nxt, prev)
        new_front = []
        if hasattr(view, "prefetch_edges"):
            view.prefetch_edges(front)
        for cur in front:
            for edge in view.edges_of(cur):
                o = _other(edge, cur)
                if o in seen:
                    continue
                seen[o] = (cur, edge)
                if o in other:
                    meet = o
                    break
                new_front.append(o)
            if meet:
                break
        if meet:
            break
        if grow_a:
            front_a = new_front
        else:
            front_b = new_front
        if not new_front:
            return None
    if meet is None:
        return None
    nodes, edges, cur = [meet], [], meet
    while prev[cur] is not None:
        cur, edge = prev[cur]
        nodes.append(cur)
        edges.append(edge)
    nodes.reverse()
    edges.reverse()
    cur = meet
    while nxt[cur] is not None:
        cur, edge = nxt[cur]
        nodes.append(cur)
        edges.append(edge)
    if len(edges) > max_hops:
        return None
    return {"nodes": [view.node(x) for x in nodes], "edges": edges, "hops": len(edges)}


def common_neighbors(view, a: str, b: str) -> list[dict]:
    na = {_other(e, a): e for e in view.edges_of(a)}
    out = []
    for e in view.edges_of(b):
        o = _other(e, b)
        if o in na and o not in (a, b):
            out.append({"node": view.node(o), "edge_a": na[o], "edge_b": e})
    return out[:MAX_RESULTS]


def _interval(e: dict) -> tuple[str, str, bool]:
    """(start, end, is_point); an open end runs to the far future, end is exclusive."""
    start, end = e["start"], e.get("end")
    if end is None:
        return start, "9999-12-31", False
    return start, end, end == start


def _overlap(e1: dict, e2: dict) -> tuple[str, str] | None:
    if not e1.get("start") or not e2.get("start"):
        return None
    s1, t1, p1 = _interval(e1)
    s2, t2, p2 = _interval(e2)
    if p1 and p2:
        return (s1, s1) if s1 == s2 else None
    if p1:
        return (s1, s1) if s2 <= s1 < t2 else None
    if p2:
        return (s2, s2) if s1 <= s2 < t1 else None
    start, end = max(s1, s2), min(t1, t2)
    return (start, end) if start < end else None


def copresence(view, eid: str, presence_types, date_from: str | None = None,
               date_to: str | None = None) -> list[dict]:
    """Who was at the same place/event as `eid` during an overlapping period."""
    presence_types = set(presence_types)
    out = []
    for mine in view.edges_of(eid):
        if mine["type"] not in presence_types or mine["a"] != eid:
            continue
        place = mine["b"]
        for theirs in view.edges_of(place):
            other = theirs["a"]
            if other == eid or theirs["type"] not in presence_types or theirs["b"] != place:
                continue
            span = _overlap(mine, theirs)
            if not span:
                continue
            if date_from and span[1] < date_from or date_to and span[0] > date_to:
                continue
            out.append({"other": view.node(other), "place": view.node(place),
                        "from": span[0], "to": None if span[1] == "9999-12-31" else span[1],
                        "evidence": mine["evidence"] + theirs["evidence"]})
    out.sort(key=lambda x: (x["from"], x["other"]["label"]))
    return out[:MAX_RESULTS]


def shared_identifiers(view, types=(IDENTIFIER,), holder_types=(PERSON, ORGANIZATION),
                       min_holders: int = 2, limit: int = 100, subtypes=None,
                       relation_types=None) -> list[dict]:
    """Identifiers (or places) held by several people/organisations; `relation_types`
    names the holding relations (uses, owns, resides at), not e.g. a call to a number."""
    out = []
    for n in view.nodes(set(types)):
        if subtypes and n["subtype"] not in subtypes:
            continue
        holders = {}
        for e in view.edges_of(n["id"]):
            if relation_types and e["type"] not in relation_types:
                continue
            o = _other(e, n["id"])
            node = view.node(o)
            if node and node["type"] in holder_types:
                holders.setdefault(o, {"node": node, "edges": []})["edges"].append(e)
        if len(holders) >= min_holders:
            out.append({"identifier": n, "holders": sorted(holders.values(),
                                                           key=lambda h: h["node"]["label"])})
    out.sort(key=lambda x: (-len(x["holders"]), x["identifier"]["label"]))
    return out[:max(1, min(limit, MAX_RESULTS))]


def timeline(view, eid: str, date_from: str | None = None, date_to: str | None = None) -> list[dict]:
    events = []
    for e in view.edges_of(eid):
        if not e["start"]:
            continue
        if date_from and (e["end"] or e["start"]) < date_from or date_to and e["start"] > date_to:
            continue
        events.append({"date": e["start"], "end": e["end"], "type": e["type"],
                       "other": view.node(_other(e, eid)), "evidence": e["evidence"]})
    events.sort(key=lambda x: (x["date"], x["type"], x["other"]["label"]))
    return events[:MAX_RESULTS]


def neighborhood(view, eid: str, hops: int = 1, limit: int = 200) -> dict:
    if view.node(eid) is None:
        return {"nodes": [], "edges": []}
    hops = max(1, min(hops, 3))
    seen = {eid}
    edges = {}
    frontier = [eid]
    for _ in range(hops):
        nxt = []
        for cur in frontier:
            for e in view.edges_of(cur):
                edges[e["id"]] = e
                o = _other(e, cur)
                if o not in seen and len(seen) < limit:
                    seen.add(o)
                    nxt.append(o)
        frontier = nxt
    kept = [e for e in edges.values() if e["a"] in seen and e["b"] in seen]
    return {"nodes": [view.node(x) for x in sorted(seen)], "edges": kept,
            "truncated": len(seen) >= limit}


def communities(view, types=(PERSON,), max_nodes: int = 20000, rounds: int = 20) -> list[dict]:
    """Deterministic label propagation on the visible graph between `types`.

    Identifiers and places shared by two people connect them (a projection),
    so a community is "people tied together by documented links"."""
    types = set(types)
    members = [n["id"] for n in view.nodes(types)][:max_nodes]
    member_set = set(members)
    adj: dict[str, set[str]] = {m: set() for m in members}
    for m in members:
        for e in view.edges_of(m):
            o = _other(e, m)
            if o in member_set:
                adj[m].add(o)
            else:
                for e2 in view.edges_of(o):
                    o2 = _other(e2, o)
                    if o2 in member_set and o2 != m:
                        adj[m].add(o2)
    label = {m: m for m in members}
    for _ in range(rounds):
        changed = False
        for m in members:
            if not adj[m]:
                continue
            counts = Counter(label[n] for n in adj[m])
            top = max(counts.values())
            best = min(lbl for lbl, c in counts.items() if c == top)
            if best != label[m]:
                label[m] = best
                changed = True
        if not changed:
            break
    groups: dict[str, list[str]] = {}
    for m, lbl in label.items():
        groups.setdefault(lbl, []).append(m)
    out = []
    for nodes in groups.values():
        if len(nodes) < 2:
            continue
        inside = set(nodes)
        degree = {n: len(adj[n] & inside) for n in nodes}
        ranked = sorted(nodes, key=lambda n: (-degree[n], view.node(n)["label"]))
        out.append({"size": len(nodes), "central": view.node(ranked[0]),
                    "members": [{"node": view.node(n), "degree": degree[n]} for n in ranked]})
    out.sort(key=lambda c: (-c["size"], c["central"]["label"]))
    return out[:MAX_RESULTS]


def degree_ranking(view, types=(PERSON,), limit: int = 20) -> list[dict]:
    ranked = []
    for n in view.nodes(set(types)):
        degree = len({_other(e, n["id"]) for e in view.edges_of(n["id"])})
        if degree:
            ranked.append((-degree, n["label"], n))
    ranked.sort(key=lambda x: x[:2])
    return [{"node": n, "degree": -d} for d, _, n in ranked[:max(1, min(limit, MAX_RESULTS))]]


def bfs_distance(view, a: str, b: str, max_hops: int = MAX_HOPS) -> int | None:
    """Plain BFS, used by the tests to cross-check shortest_path."""
    if view.node(a) is None or view.node(b) is None:
        return None
    dist = {a: 0}
    q = deque([a])
    while q:
        cur = q.popleft()
        if cur == b:
            return dist[cur]
        if dist[cur] >= max_hops:
            continue
        for e in view.edges_of(cur):
            o = _other(e, cur)
            if o not in dist:
                dist[o] = dist[cur] + 1
                q.append(o)
    return None
