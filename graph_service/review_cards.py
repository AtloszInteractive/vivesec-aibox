"""What a reviewer needs to tell two "may be the same" people apart (E26).

The review queue holds pairs resolution could not decide. A pair with only the
two names is undecidable, so every listed pair carries, for each side, what the
caller's visible documents say of that person: role, identifying details, held
identifiers (phone, ID card ...), source documents and a few relations with
their evidence; plus a field-by-field comparison (same / conflicting / only one
side). Everything comes from the caller's view, so nothing a hidden document
states can appear.

The queue order and the role rule follow the namesake join (namesakes.py):
two records of different roles (an inmate and a visitor) are two people unless
something more than the name links them. Such pairs are left out of the default
list and counted instead; the caller can still ask for them.
"""
from __future__ import annotations

from extraction import fold

# Reasons resting on the name alone (resolution._score).
NAME_ONLY = frozenset({"same name", "near-identical name", "same family name and initial"})
# Compared field by field; the rest of the attributes are only shown.
COMPARED = ("birth_date", "mother_name", "birth_place", "id_number", "registry_number")
MAX_DOCUMENTS = 3
MAX_LINKS = 4
MAX_QUOTE = 240


def supported(item: dict) -> bool:
    """More than the name links the two records."""
    return item.get("reason") not in NAME_ONLY


def role_mismatch(item: dict) -> bool:
    """Two different roles linked by the name alone: not the same person by the
    namesake rule, so not worth a reviewer's time by default."""
    a, b = item.get("a_subtype") or "", item.get("b_subtype") or ""
    return bool(a and b and a != b and not supported(item))


def order(items: list[dict]) -> list[dict]:
    """Pairs with supporting evidence first, then by score; namesakes stay
    together so the records of one name can be compared one after the other."""
    return sorted(items, key=lambda i: (0 if supported(i) else 1, -float(i.get("score") or 0),
                                        fold(i.get("a_label") or ""), i["a"], i["b"]))


def _short(text: str) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= MAX_QUOTE else text[:MAX_QUOTE - 1].rstrip() + "…"


def describe(view, eid: str, holding=()) -> dict | None:
    """The visible facts of one person record."""
    node = view.node(eid)
    if node is None:
        return None
    attributes, seen = [], set()
    for a in node.get("attributes") or []:
        key = (a["name"], a["value"])
        if key not in seen:
            seen.add(key)
            attributes.append({"name": a["name"], "value": a["value"]})
    documents = []
    for doc_id in node.get("documents") or []:
        doc = view.document(doc_id)
        if doc is None:
            continue
        documents.append({"doc_id": doc_id, "path": doc.source_path,
                          "name": doc.title or doc.source_path.rsplit("/", 1)[-1]})
        if len(documents) >= MAX_DOCUMENTS:
            break
    identifiers, links = [], []
    edges = view.edges_of(eid)
    for edge in edges:
        other_id = edge["b"] if edge["a"] == eid else edge["a"]
        other = view.node(other_id)
        if other is None:
            continue
        evidence = edge["evidence"][0] if edge.get("evidence") else {}
        if edge["type"] in holding and not other_id.startswith("person:"):
            # What the person holds: a phone, an ID card, a vehicle, an address.
            label = other["label"]
            if label == other_id and other_id.startswith("id:"):
                label = other_id.split(":", 2)[-1]
            identifiers.append({"id": other_id, "label": label,
                                "subtype": other.get("subtype") or ""})
            continue
        if len(links) < MAX_LINKS:
            links.append({"type": edge["type"], "raw_label": edge.get("raw_label") or "",
                          "other": other["label"], "other_id": other_id,
                          "other_type": other.get("type") or "",
                          "start": edge.get("start"), "end": edge.get("end"),
                          "quote": _short(evidence.get("quote") or ""),
                          "doc_id": evidence.get("doc_id") or ""})
    return {"subtype": node.get("subtype") or "", "attributes": attributes,
            "identifiers": identifiers, "documents": documents, "links": links,
            "counts": {"documents": len(node.get("documents") or []), "links": len(edges)}}


def compare(a: dict, b: dict) -> list[dict]:
    """Field by field: "same" (a shared value), "conflict" (both state one and
    none is shared), "a_only" / "b_only"; fields neither side states are left out."""
    out = []

    def values(info, name):
        return sorted({x["value"] for x in info["attributes"] if x["name"] == name})

    fields = [(name, values(a, name), values(b, name)) for name in COMPARED]
    fields.append(("identifiers", sorted({x["label"] for x in a["identifiers"]}),
                   sorted({x["label"] for x in b["identifiers"]})))
    for name, va, vb in fields:
        if not va and not vb:
            continue
        if va and vb:
            if name == "identifiers":
                ids_a = {x["id"] for x in a["identifiers"]}
                state = "same" if ids_a & {x["id"] for x in b["identifiers"]} else "different"
            else:
                state = "same" if {fold(v) for v in va} & {fold(v) for v in vb} else "conflict"
        else:
            state = "a_only" if va else "b_only"
        out.append({"name": name, "a": va, "b": vb, "state": state})
    return out


def enrich(view, items: list[dict], holding=()) -> list[dict]:
    """The listed page with both sides described and compared."""
    ids = sorted({x for i in items for x in (i["a"], i["b"])})
    view.prefetch(ids)
    view.prefetch_edges(ids)
    out = []
    for item in items:
        a, b = describe(view, item["a"], holding), describe(view, item["b"], holding)
        if a is None or b is None:
            out.append(item)
            continue
        out.append(dict(item, a_info=a, b_info=b, fields=compare(a, b)))
    return out
