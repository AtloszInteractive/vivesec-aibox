"""Score extracted graph payloads against the graph-corpus answer key (E26).

    python graph-corpus/evaluate.py --corpus graph-corpus/out --extracted extracted.jsonl

Reports, over the documents that were processed:
  person mentions   recall of the names the documents contain;
  resolution        wrong merges (two real people in one entity, name twins
                    above all) and fragmentation (one person split up);
  relations         recall and precision per relation type, matched on type +
                    endpoints within the document that states them.
Precision counts every extracted relation the key does not hold as wrong, so
it is a lower bound: a correct relation the generator did not plan also counts.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(1, os.path.join(os.path.dirname(HERE), "graph_service"))
from domain_profile import normalize_value  # noqa: E402
from extraction import fold  # noqa: E402
from oracle import RELATION_MAP  # noqa: E402

NORMALIZE = {"phone": "phone_hu", "vehicle_plate": "nospace_upper"}


def _endpoint_of_gold(value: str, key: dict, idx: dict) -> tuple:
    if value in idx["persons"]:
        return ("person", value)
    if value in idx["phones"]:
        return ("val", normalize_value(value, "phone_hu"))
    if value in idx["plates"]:
        return ("val", normalize_value(value, "nospace_upper"))
    if value in idx["places"]:
        return ("place", fold(idx["places"][value]))
    if value in idx["orgs"]:
        return ("org", fold(idx["orgs"][value]))
    if value in idx["incidents"]:
        return ("incident", value)
    return ("place", fold(value))  # addresses are stored by their text


def _endpoint_of_extracted(eid: str, ent_pid: dict, doc_incident: str | None) -> tuple:
    if eid.startswith("person:"):
        return ("person", ent_pid.get(eid))
    if eid.startswith("id:"):
        return ("val", eid.split(":", 2)[2])
    if eid.startswith("place:"):
        return ("place", eid.split(":", 1)[1])
    if eid.startswith("organization:"):
        return ("org", eid.split(":", 1)[1])
    if eid.startswith("event:"):
        return ("incident", doc_incident)
    return ("other", eid)


def _same(a: tuple, b: tuple) -> bool:
    if a[0] != b[0] or a[1] is None or b[1] is None:
        return False
    if a[0] == "place":
        x, y = a[1], b[1]
        return x == y or (min(len(x), len(y)) >= 8 and (x in y or y in x))
    return a[1] == b[1]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--corpus", required=True)
    ap.add_argument("--extracted", required=True)
    args = ap.parse_args()

    with io.open(os.path.join(args.corpus, "answer_key.json"), encoding="utf-8") as f:
        key = json.load(f)
    items = [json.loads(line) for line in io.open(args.extracted, encoding="utf-8")]
    processed = {it["key"]: it for it in items}
    kind_of = {d["key"]: d["kind"] for d in key["documents"]}
    scanned = {d["key"] for d in key["documents"] if d["scanned"]}

    idx = {"persons": {p["id"] for p in key["persons"]},
           "phones": {ph for p in key["persons"] for ph in p["phones"]}
           | {i["phone"] for i in key["incidents"] if i["phone"]},
           "plates": {pl for p in key["persons"] for pl in p["plates"]},
           "places": {p["id"]: p["label"] for p in key["places"]},
           "orgs": {o["id"]: o["name"] for o in key["organisations"]},
           "incidents": {i["id"] for i in key["incidents"]}}
    for r in key["relations"]:
        if r["type"] in ("USES_VEHICLE", "OWNS_VEHICLE"):
            idx["plates"].add(r["b"])

    gold_surfaces: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    for p in key["persons"]:
        for surface, docs in p["surfaces"].items():
            for d in docs:
                gold_surfaces[d][fold(surface)].add(p["id"])

    # person mentions + entity -> gold person
    found = total = 0
    ent_votes: dict[str, Counter] = defaultdict(Counter)
    for k, it in processed.items():
        mentions = [m for m in it["payload"]["mentions"] if m["entity_id"].startswith("person:")]
        folded = {fold(m["text"]) for m in mentions}
        if k not in scanned or it["stats"]["text"] == "ocr":
            for surface in gold_surfaces.get(k, {}):
                total += 1
                found += surface in folded
        for m in mentions:
            pids = gold_surfaces.get(k, {}).get(fold(m["text"]), set())
            if len(pids) == 1:
                ent_votes[m["entity_id"]][next(iter(pids))] += 1
    ent_pid = {e: c.most_common(1)[0][0] for e, c in ent_votes.items()}
    impure = {e: c for e, c in ent_votes.items() if len(c) > 1}
    pid_entities: dict[str, set[str]] = defaultdict(set)
    for e, pid in ent_pid.items():
        pid_entities[pid].add(e)
    twins_merged = [pair for pair in key["questions"]["must_not_merge"]
                    if any(set(pair) <= set(c) for c in ent_votes.values())]

    # relations
    doc_incident = {}
    for r in key["relations"]:
        if r["type"] == "INVOLVED_IN":
            for d in r["evidence"]:
                doc_incident[d] = r["b"]
    per_type = defaultdict(Counter)
    per_kind = defaultdict(Counter)
    for k, it in processed.items():
        if k in scanned and it["stats"]["text"] != "ocr":
            continue
        gold = [(RELATION_MAP[r["type"]], _endpoint_of_gold(r["a"], key, idx),
                 _endpoint_of_gold(r["b"], key, idx)) for r in key["relations"]
                if k in r["evidence"]]
        got = [(r["type"], _endpoint_of_extracted(r["a"], ent_pid, doc_incident.get(k)),
                _endpoint_of_extracted(r["b"], ent_pid, doc_incident.get(k)))
               for r in it["payload"]["relations"] if r["type"] != "CO_MENTIONED"]
        used = set()
        for t, a, b in gold:
            hit = next((i for i, (gt, ga, gb) in enumerate(got) if i not in used and gt == t
                        and ((_same(a, ga) and _same(b, gb)) or (_same(a, gb) and _same(b, ga)))),
                       None)
            per_type[t]["gold"] += 1
            per_kind[kind_of[k]]["gold"] += 1
            if hit is not None:
                used.add(hit)
                per_type[t]["found"] += 1
                per_kind[kind_of[k]]["found"] += 1
        for i, (t, _, _) in enumerate(got):
            per_type[t]["extracted"] += 1
            per_type[t]["correct"] += i in used
            per_kind[kind_of[k]]["extracted"] += 1
            per_kind[kind_of[k]]["correct"] += i in used

    def pct(a, b):
        return "%5.1f%%" % (100.0 * a / b) if b else "    - "

    texts = Counter(it["stats"]["text"] for it in items)
    print("documents: %d  %s" % (len(items), dict(texts)))
    print("person mentions: recall %s (%d/%d)" % (pct(found, total), found, total))
    print("person entities: %d mapped, %d impure (several real people), %d name-twin merges"
          % (len(ent_pid), len(impure), len(twins_merged)))
    split = [len(v) for v in pid_entities.values()]
    print("fragmentation: %d people -> %d entities (%.2f per person)"
          % (len(split), sum(split), sum(split) / max(1, len(split))))
    print("relations by type:      recall      precision   (gold / extracted)")
    for t in sorted(per_type):
        c = per_type[t]
        print("  %-16s %s (%3d/%3d)  %s (%3d/%3d)" % (t, pct(c["found"], c["gold"]), c["found"],
                                                       c["gold"], pct(c["correct"], c["extracted"]),
                                                       c["correct"], c["extracted"]))
    print("relations by document kind:")
    for kind in sorted(per_kind):
        c = per_kind[kind]
        print("  %-22s recall %s  precision %s" % (kind, pct(c["found"], c["gold"]),
                                                   pct(c["correct"], c["extracted"])))
    tot = Counter()
    for c in per_type.values():
        tot.update(c)
    print("ALL relations: recall %s  precision %s" % (pct(tot["found"], tot["gold"]),
                                                      pct(tot["correct"], tot["extracted"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
