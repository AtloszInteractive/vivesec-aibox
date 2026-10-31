"""Name-based joining of person records for analytics (E26).

Resolution keeps two person records apart unless a strong key, a birth date or
a mother's name confirms them, so a visit log that names an inmate only by name
stays a separate record from the placement list that gives the birth date.
Patterns, sequences and per-entity counts then miss every chain that crosses
documents. When the caller asks for it, JoinedView treats the visible records
of one name as one person for the length of a query; the graph itself is never
changed, and the administrator's review stays the authority.

A name group is joined only when nothing speaks against it: the records may
not carry different subtypes (an inmate and a visitor of one name are two
people) unless an identifying key confirms they are one (the same registry or
ID number, or the same birth date and mother's name: extraction now and then
types an inmate as a visitor in one document), different birth dates,
mothers' names or ID numbers, and no pair of
them may have been decided "separate". Only attributes from documents the
caller may see are compared, so a hidden document can neither join nor split
records. Joined nodes carry "joined": [record ids], so the caller can say which
links rest on the name alone.
"""
from __future__ import annotations

from collections import Counter

from extraction import fold
from resolution import _person_keys

CONFLICT_ATTRS = ("birth_date", "mother_name", "id_number", "registry_number")
# Any one of these, equal and present on both records, confirms the same person.
CONFIRMING_KEYS = (("registry_number",), ("id_number",), ("birth_date", "mother_name"))


class JoinedView:
    def __init__(self, view, separate=frozenset()):
        self.view = view
        self.rep: dict[str, str] = {}
        self.members: dict[str, list[str]] = {}
        self.ambiguous: list[str] = []
        self._nodes: dict[str, dict | None] = {}
        self._build(separate)

    def _build(self, separate) -> None:
        groups: dict[str, list[dict]] = {}
        for node in self.view.nodes(types=("person",)):
            key = _person_keys(node["label"])[0]
            if key:
                groups.setdefault(key, []).append(node)
        for key in sorted(groups):
            nodes = groups[key]
            if len(nodes) < 2:
                continue
            if self._conflict(nodes, separate):
                self.ambiguous.append(nodes[0]["label"])
                continue
            # The record with the most confirmed detail stands for the group.
            nodes.sort(key=lambda n: (-len(n.get("attributes") or []),
                                      -len(n.get("documents") or []), n["id"]))
            ids = sorted(n["id"] for n in nodes)
            self.members[nodes[0]["id"]] = ids
            for nid in ids:
                self.rep[nid] = nodes[0]["id"]

    @staticmethod
    def _values(node) -> dict:
        out: dict[str, set] = {}
        for a in node.get("attributes") or []:
            if a["name"] in CONFLICT_ATTRS and a.get("value"):
                out.setdefault(a["name"], set()).add(fold(a["value"]))
        return out

    @classmethod
    def _subtype_conflict(cls, nodes) -> bool:
        """Two subtypes are two people, unless a key confirms the records are one:
        the subtypes of records confirmed together count as one person's."""
        parent = {n["id"]: n["id"] for n in nodes}

        def root(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x
        values = {n["id"]: cls._values(n) for n in nodes}
        for i, a in enumerate(nodes):
            for b in nodes[i + 1:]:
                va, vb = values[a["id"]], values[b["id"]]
                if any(all(va.get(k) and va.get(k) == vb.get(k) for k in key)
                       for key in CONFIRMING_KEYS):
                    parent[root(a["id"])] = root(b["id"])
        units: dict[str, Counter] = {}
        for n in nodes:
            if n.get("subtype"):
                units.setdefault(root(n["id"]), Counter())[n["subtype"]] += \
                    len(n.get("documents") or []) or 1
        # A confirmed person counts as the subtype most of their documents give, so
        # another record typed otherwise and not confirmed still stays apart.
        sets = [{k for k, c in u.items() if c == max(u.values())} for u in units.values()]
        return any(not (x & y) for i, x in enumerate(sets) for y in sets[i + 1:])

    @classmethod
    def _conflict(cls, nodes, separate) -> bool:
        if cls._subtype_conflict(nodes):
            return True
        for name in CONFLICT_ATTRS:
            values = {fold(a["value"]) for n in nodes for a in n.get("attributes") or []
                      if a["name"] == name and a.get("value")}
            if len(values) > 1:
                return True
        ids = {n["id"] for n in nodes}
        return any(a in ids and b in ids for a, b in separate)

    def canon_spec(self, spec: dict | None) -> dict | None:
        """A side filter whose ids point at the joined records."""
        if not spec or not spec.get("ids"):
            return spec
        return dict(spec, ids=sorted({self.rep.get(i, i) for i in spec["ids"]}))

    def node(self, eid: str) -> dict | None:
        rid = self.rep.get(eid, eid)
        if rid not in self._nodes:
            node = self.view.node(rid)
            ids = self.members.get(rid)
            if node is not None and ids:
                parts = [self.view.node(i) for i in ids]
                parts = [p for p in parts if p]
                # The subtype most documents give (a record typed otherwise was
                # confirmed to be the same person by a key).
                kinds = Counter()
                for p in parts:
                    if p.get("subtype"):
                        kinds[p["subtype"]] += len(p.get("documents") or []) or 1
                subtype = min(kinds, key=lambda k: (-kinds[k], k)) if kinds else ""
                node = dict(node, joined=ids, subtype=subtype,
                            mentions=sum(p.get("mentions", 0) for p in parts),
                            documents=sorted({d for p in parts for d in p.get("documents") or []}),
                            aliases=sorted({a for p in parts for a in p.get("aliases") or []}),
                            attributes=[a for p in parts for a in p.get("attributes") or []])
            self._nodes[rid] = node
        return self._nodes[rid]

    def _rewrite(self, edge: dict) -> dict | None:
        a, b = self.rep.get(edge["a"], edge["a"]), self.rep.get(edge["b"], edge["b"])
        if a == b:
            return None  # a link between two records of one person
        if (a, b) == (edge["a"], edge["b"]):
            return edge
        return dict(edge, a=a, b=b)

    def edges_of(self, eid: str) -> list[dict]:
        rid = self.rep.get(eid, eid)
        out, seen = [], set()
        for member in self.members.get(rid, [rid]):
            for edge in self.view.edges_of(member):
                if edge["id"] in seen:
                    continue
                seen.add(edge["id"])
                edge = self._rewrite(edge)
                if edge:
                    out.append(edge)
        return out

    def edges(self):
        for edge in self.view.edges():
            edge = self._rewrite(edge)
            if edge:
                yield edge

    def __getattr__(self, name):
        return getattr(self.view, name)
