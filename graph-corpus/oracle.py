"""Perfect-extraction payloads from the answer key (E26, dev only).

Turns graph-corpus truth into the graph_service ingest payload, document by
document, as if extraction had been flawless. Used to test the graph queries
on their own and to demo the UI before the extraction pipeline exists.

    python graph-corpus/oracle.py --out graph-corpus/out --service http://127.0.0.1:8095
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
import urllib.request

RELATION_MAP = {
    "USES_PHONE": "USES", "USES_VEHICLE": "USES", "OWNS_VEHICLE": "OWNS",
    "RESIDES_AT": "RESIDES_AT", "WORKS_FOR": "WORKS_FOR", "RELATIVE_OF": "RELATIVE_OF",
    "PLACED_IN": "PLACED_IN", "TRANSFERRED": "TRANSFERRED_TO", "VISITED": "VISITED",
    "CALLED": "CALLED", "AT_PLACE": "LOCATED_AT", "MET": "MET", "INVOLVED_IN": "INVOLVED_IN",
}
SYMMETRIC = {"RELATIVE_OF", "MET"}
# Person attributes each document kind states (render.py), for inmates and for others.
STATED = {
    "befogadasi_adatlap": {"inmate": ("registry_number", "birth_place", "birth_date",
                                      "mother_name", "id_number")},
    "hatarozat": {"inmate": ("registry_number", "birth_place", "birth_date", "mother_name")},
    "elhelyezesi_kimutatas": {"inmate": ("registry_number", "birth_date")},
    "latogatasi_naplo": {"inmate": ("registry_number",), "other": ("id_number",)},
    "hivasnaplo": {"inmate": ("registry_number", "birth_date")},
    "letszamjelentes": {"inmate": ("registry_number",)},
    "elofizetoi_kivonat": {"inmate": ("birth_date", "mother_name"),
                           "other": ("birth_date", "mother_name")},
    "lakcim_kivonat": {"other": ("birth_date", "mother_name", "id_number")},
    "gepjarmu_kivonat": {"other": ("birth_date",)},
}
ATTRIBUTE_OF = {"registry_number": "registry_number", "birth_place": "birthplace",
                "birth_date": "dob", "mother_name": "mother", "id_number": "id_number"}


def stated_attributes(person: dict, kind: str) -> list[dict]:
    """The attributes of an answer-key person a document of `kind` states."""
    side = "inmate" if person["role"] == "inmate" else "other"
    out = []
    for name in (STATED.get(kind) or {}).get(side, ()):
        value = person.get(ATTRIBUTE_OF[name])
        if value:
            out.append({"name": name, "value": value, "page": 1})
    return out


def entity_index(key: dict) -> dict[str, dict]:
    """Every world object as a graph entity, keyed by the value the key uses."""
    out = {}
    for p in key["persons"]:
        out[p["id"]] = {"id": p["id"], "type": "person",
                        "subtype": "inmate" if p["role"] == "inmate" else "visitor"
                        if p["role"] == "visitor" else "", "label": p["name"]}
        for ph in p["phones"]:
            out.setdefault(ph, {"id": "phone:" + ph, "type": "identifier", "subtype": "phone",
                                "label": ph})
        for pl in p["plates"]:
            out.setdefault(pl, {"id": "plate:" + pl, "type": "identifier",
                                "subtype": "vehicle_plate", "label": pl})
    for o in key["organisations"]:
        out[o["id"]] = {"id": o["id"], "type": "organization", "subtype": "", "label": o["name"]}
    for pl in key["places"]:
        out[pl["id"]] = {"id": pl["id"], "type": "place", "subtype": pl["kind"],
                         "label": pl["label"]}
    for inc in key["incidents"]:
        out[inc["id"]] = {"id": inc["id"], "type": "event", "subtype": "incident",
                          "label": "%s (%s)" % (inc["kind"], inc["date"])}
        if inc["phone"]:
            out.setdefault(inc["phone"], {"id": "phone:" + inc["phone"], "type": "identifier",
                                          "subtype": "phone", "label": inc["phone"]})
    for r in key["relations"]:
        if r["type"] in ("RESIDES_AT", "AT_PLACE") and r["b"] not in out:
            out[r["b"]] = {"id": "addr:" + r["b"], "type": "place", "subtype": "address",
                           "label": r["b"]}
        if r["type"] in ("USES_VEHICLE", "OWNS_VEHICLE") and r["b"] not in out:
            out[r["b"]] = {"id": "plate:" + r["b"], "type": "identifier",
                           "subtype": "vehicle_plate", "label": r["b"]}
    return out


def payloads(key: dict, docs: dict[str, dict], attributes: bool = False) -> list[dict]:
    """docs: answer-key document key -> {doc_id, corpus_id, source_path, kind}.
    attributes: also the person attributes each document states (registry
    number, birth data, mother's name, ID number), as extraction stores them."""
    ents = entity_index(key)
    persons = {p["id"]: p for p in key["persons"]}
    surfaces: dict[str, list[tuple[str, str]]] = {}
    for p in key["persons"]:
        for text, keys in p["surfaces"].items():
            for k in keys:
                surfaces.setdefault(k, []).append((p["id"], text))
    rels_by_doc: dict[str, list[dict]] = {}
    for r in key["relations"]:
        for k in r["evidence"]:
            rels_by_doc.setdefault(k, []).append(r)

    out = []
    for k, meta in sorted(docs.items()):
        used: dict[str, dict] = {}
        mentions = []
        for pid, text in surfaces.get(k, []):
            used[pid] = ents[pid]
            mentions.append({"id": "%s:m%d" % (k, len(mentions)), "entity_id": pid, "text": text,
                             "method": "manual"})
        relations = []
        for r in rels_by_doc.get(k, []):
            a, b = ents[r["a"]], ents[r["b"]]
            for e in (a, b):
                if e["id"] not in used:
                    used[e["id"]] = e
                    mentions.append({"id": "%s:m%d" % (k, len(mentions)), "entity_id": e["id"],
                                     "text": e["label"], "method": "manual"})
            type_ = RELATION_MAP[r["type"]]
            ea, eb = a["id"], b["id"]
            if type_ in SYMMETRIC and eb < ea:
                ea, eb = eb, ea
            relations.append({"id": "%s:%s" % (r["id"], k), "type": type_, "a": ea, "b": eb,
                              "start": r["start"], "end": r["end"], "raw_label": r["type"],
                              "evidence": [{"quote": "", "method": "manual"}]})
        entities = []
        for e in used.values():
            entity = {"id": e["id"], "type": e["type"], "subtype": e["subtype"],
                      "label": e["label"]}
            if attributes and e["id"] in persons:
                found = stated_attributes(persons[e["id"]], meta["kind"])
                if found:
                    entity["attributes"] = found
            entities.append(entity)
        out.append({"document": {"doc_id": meta["doc_id"], "corpus_id": meta["corpus_id"],
                                 "source_path": meta["source_path"], "kind": meta["kind"]},
                    "entities": entities, "mentions": mentions, "relations": relations})
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "out"))
    ap.add_argument("--service", default="http://127.0.0.1:8095")
    ap.add_argument("--api-key", default=os.environ.get("GRAPH_API_KEY", ""))
    ap.add_argument("--attributes", action="store_true",
                    help="also load the person attributes the documents state")
    args = ap.parse_args()
    with io.open(os.path.join(args.out, "answer_key.json"), encoding="utf-8") as f:
        key = json.load(f)
    docs = {}
    with io.open(os.path.join(args.out, "manifest.jsonl"), encoding="utf-8") as f:
        for line in f:
            m = json.loads(line)
            docs[m["key"]] = {"doc_id": m["doc_id"], "corpus_id": m["corpus_id"],
                              "source_path": m["source_path"], "kind": m["kind"]}
    sent = 0
    for body in payloads(key, docs, attributes=args.attributes):
        req = urllib.request.Request(args.service + "/graph/document/put",
                                     data=json.dumps(body).encode("utf-8"), method="POST",
                                     headers={"Content-Type": "application/json",
                                              "X-API-Key": args.api_key})
        with urllib.request.urlopen(req, timeout=60) as resp:
            json.loads(resp.read())
        sent += 1
    print("loaded %d documents into %s" % (sent, args.service))
    return 0


if __name__ == "__main__":
    sys.exit(main())
