"""Formal leakage tests for derived graph data (E26, pilot step 4).

The rule (concept 5.1): a relationship, a property, a count, a path, a group or
a rank may only come from documents the caller can read, and nothing may hint
at what is hidden ("3 more connections"). Tested differentially: for every
query the graph service answers, a caller restricted by ACL, classification,
a deny rule or the drive scope gets byte for byte what a graph built ONLY from
the documents they can read would answer. The same queries for a caller who
may read everything must differ, or the corpus would prove nothing.

The full graph runs on the memory store here; with GRAPH_TEST_NEO4J_URI set
(neo4j_store_test.py) also on Neo4j, with and without the read cache, against
the same memory-built oracle.
"""
import copy
import itertools
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import domain_profile  # noqa: E402
import service  # noqa: E402
from graph_store import MemoryGraphStore  # noqa: E402
from ingest_jobs import IngestWorker  # noqa: E402
from ingest_jobs_test import FakeLLM  # noqa: E402

PROFILE = domain_profile.load("bv")
LARS = {"principals": ["user:lars"], "clearance": 0, "membership_known": True,
        "default_classification": 0}
HANNA = {"principals": ["user:hanna", "group:hr"], "clearance": 2, "membership_known": True,
         "default_classification": 0}
HR_ONLY = {"restricted": True, "allow": ["group:hr"], "deny": [], "classification": 0}
SECRET_LEVEL = {"restricted": False, "allow": [], "deny": [], "classification": 2}
NOT_LARS = {"restricted": False, "allow": [], "deny": ["user:lars"], "classification": 0}

A, A2, A3, B, C, D, Z = ("person:kiss adam", "person:kiss adam 2", "person:kiss adam 3",
                         "person:nagy bela", "person:szabo csilla", "person:toth denes",
                         "person:titkos zoltan")
X, Y = "id:phone:+36301111111", "id:phone:+36302222222"
CELL, INST, ORG, ADDR = ("place:102. szamu zarka@place:kohegyi intezet", "place:kohegyi intezet",
                         "organization:delibab kft", "place:fo utca 5.")
LABELS = {A: "Kiss Ádám", A2: "Kiss Adam", A3: "Kiss Ádám", B: "Nagy Béla", C: "Szabó Csilla",
          D: "Tóth Dénes", Z: "Titkos Zoltán", X: "+36 30 111 1111", Y: "+36 30 222 2222",
          CELL: "102. számú zárka", INST: "Kőhegyi Intézet", ORG: "Délibáb Kft",
          ADDR: "Fő utca 5."}
TYPES = {"person": (A, A2, A3, B, C, D, Z), "identifier": (X, Y), "place": (CELL, INST, ADDR),
         "organization": (ORG,)}
TYPE_OF = {eid: t for t, ids in TYPES.items() for eid in ids}


def payload(doc_id, corpus, acl, people, rels, attrs=(), subtypes=None, names=None):
    """A document naming `people` (entity ids), stating `rels`
    [(type, a, b, start, end)] -- CO_MENTIONED ones as weak co-mentions --, with
    attributes [(entity, name, value)]."""
    subtypes, names = subtypes or {}, names or {}
    ents = {}
    for eid in people:
        ents[eid] = {"id": eid, "type": TYPE_OF[eid], "subtype": subtypes.get(eid, ""),
                     "label": LABELS[eid], "attributes": []}
    for eid, name, value in attrs:
        ents[eid]["attributes"].append({"name": name, "value": value, "page": 1})
    mentions = [{"id": "%s:m%d" % (doc_id, i), "entity_id": eid,
                 "text": names.get(eid, LABELS[eid]), "page": 1, "method": "llm"}
                for i, eid in enumerate(people)]
    relations = [{"id": "%s:r%d" % (doc_id, i), "type": t, "a": a, "b": b, "start": s, "end": e,
                  "evidence": [{"quote": "%s %s %s (%s)" % (LABELS[a], t, LABELS[b], doc_id),
                                "page": 1, "confidence": 0.3 if t == "CO_MENTIONED" else 0.8,
                                "method": "cooccurrence" if t == "CO_MENTIONED" else "llm"}]}
                 for i, (t, a, b, s, e) in enumerate(rels)]
    return {"document": {"doc_id": doc_id, "corpus_id": corpus,
                         "source_path": "/%s/%s.txt" % (corpus, doc_id), "title": doc_id,
                         "kind": "note", "acl": acl},
            "entities": list(ents.values()), "mentions": mentions, "relations": relations}


# What lars may read: c1, unrestricted, classification 0, no deny on him.
PUBLIC = [
    payload("p1", "c1", None, [A, B, X],
            [("MET", A, B, "2026-01-05", "2026-01-05"), ("CALLED", A, X, "2026-01-06", None),
             ("CO_MENTIONED", A, B, None, None)]),
    payload("p2", "c1", None, [B, C, CELL, INST],
            [("PLACED_IN", B, CELL, "2026-02-01", "2026-03-01"),
             ("PLACED_IN", C, CELL, "2026-02-10", "2026-02-20"),
             ("PART_OF", CELL, INST, None, None)],
            attrs=[(B, "birth_date", "1980-01-02")], subtypes={CELL: "cell"}),
    payload("p3", "c1", None, [C, D, X, Y],
            [("USES", C, X, None, None), ("USES", D, X, None, None), ("USES", D, Y, None, None)]),
    payload("p4", "c1", None, [C, D, ORG, A2, ADDR],
            [("WORKS_FOR", C, ORG, None, None), ("WORKS_FOR", D, ORG, None, None),
             ("RESIDES_AT", A2, ADDR, None, None)]),
]
# What lars may not read, each hidden a different way, each touching what he does see.
HIDDEN = [
    # A shortcut A-C, a second name and a birth date for A, a weak edge C-X.
    payload("h1", "c1", HR_ONLY, [A, C, X],
            [("MET", A, C, "2026-01-10", "2026-01-10"), ("CO_MENTIONED", C, X, None, None)],
            attrs=[(A, "birth_date", "1975-05-05")], names={A: "K. Ádám"}),
    # A third holder of X, a cellmate of B and C, a subtype for B, the cell's prison.
    payload("h2", "c1", HR_ONLY, [Z, B, X, CELL, INST],
            [("USES", Z, X, None, None), ("MET", Z, B, "2026-02-15", "2026-02-15"),
             ("PLACED_IN", Z, CELL, "2026-02-12", "2026-02-25"), ("PART_OF", CELL, INST, None, None)],
            attrs=[(CELL, "within", "Kőhegyi Intézet")],
            subtypes={B: "inmate", Z: "inmate", CELL: "cell"}),
    # More evidence for a visible relation, and a mother's name.
    payload("h3", "c1", HR_ONLY, [A, B], [("MET", A, B, "2026-01-05", "2026-01-05")],
            attrs=[(B, "mother_name", "Rejtett Rozália")]),
    # A namesake of A only a higher clearance may see.
    payload("h4", "c1", SECRET_LEVEL, [D, B, A3], [("MET", D, B, "2026-03-03", "2026-03-03"),
                                                    ("MET", A3, D, "2026-03-04", "2026-03-04")]),
    payload("h5", "c1", NOT_LARS, [C, D], [("RELATIVE_OF", C, D, None, None)]),
    # Another drive: outside lars' scope whatever its ACL.
    payload("o1", "c2", None, [A, D, Y], [("MET", A, D, "2026-04-01", "2026-04-01"),
                                          ("USES", A, Y, None, None)]),
]
# Possible same-person pairs the resolver left for review, and decisions on them.
REVIEW = [{"a": A2, "b": A, "score": 0.7, "reason": "same name", "doc_id": "p4",
           "names": ["Kiss Adam"]},
          {"a": Z, "b": D, "score": 0.6, "reason": "same phone", "doc_id": "h2",
           "names": ["Titkos Zoltán"]},
          {"a": Z, "b": B, "score": 0.5, "reason": "same cell", "doc_id": "h2",
           "names": ["Titkos Zoltán"]},
          {"a": A3, "b": A, "score": 0.75, "reason": "same name", "doc_id": "h4",
           "names": ["Kiss Ádám"]}]


def build(store, docs, merge=False):
    state = service.State(store, PROFILE)
    for body in docs:
        service.handle_post(state, "/graph/document/put", copy.deepcopy(body))
    state.worker = IngestWorker(state, FakeLLM())
    ids = getattr(store, "mirror", store).entities
    state.worker.resolver.review.extend(dict(i) for i in REVIEW if i["a"] in ids and i["b"] in ids)
    if Z in ids:
        state.worker.decide(Z, D, False, "admin")
    if merge:
        state.worker.decide(A2, A, True, "admin")
    return state


def requests():
    """(path, body) for every graph read, with the parameters that touch hidden data."""
    ids = sorted(LABELS)
    out = []
    for text in ("Kiss", "K. Ádám", "Titkos", "Nagy Béla", "+36 30 111", "Csilla", "Délibáb"):
        out += [("/query/find", {"text": text}), ("/query/find", {"text": text, "types": ["person"]})]
    for text in ("Kiss Ádám és Titkos Zoltán", "K. Ádám telefonja +36 30 111 1111",
                 "Ki hívta a +36 30 222 2222 számot?", "Nagy Béla és Tóth Dénes"):
        out.append(("/query/mentioned", {"text": text}))
    for eid in ids:
        out += [("/query/entity", {"id": eid}), ("/query/copresence", {"id": eid}),
                ("/query/timeline", {"id": eid}),
                ("/query/timeline", {"id": eid, "date_from": "2026-02", "date_to": "2026-03"})]
        out += [("/query/neighborhood", {"id": eid, "hops": h}) for h in (1, 2, 3)]
    for a, b in itertools.combinations(ids, 2):
        out += [("/query/path", {"a": a, "b": b}), ("/query/path", {"a": a, "b": b, "max_hops": 2}),
                ("/query/common", {"a": a, "b": b})]
    out += [("/query/shared", {}), ("/query/shared", {"min_holders": 3}),
            ("/query/shared", {"types": ["identifier"], "holder_types": ["person"]}),
            ("/query/communities", {}), ("/query/communities", {"types": ["person", "organization"]}),
            ("/query/ranking", {}), ("/query/ranking", {"types": list(TYPES), "limit": 3})]
    person = {"types": ["person"]}
    for join in (False, True):
        out += [
            ("/query/match", {"start": person, "join_namesakes": join,
                              "steps": [{"relations": ["MET"], "target": person}]}),
            ("/query/match", {"start": {"ids": [A]}, "join_namesakes": join,
                              "steps": [{"relations": ["MET"], "target": person},
                                        {"relations": ["PLACED_IN"], "target": {"types": ["place"]}}]}),
            ("/query/match", {"start": person, "date_from": "2026-02", "join_namesakes": join,
                              "steps": [{"target": {"types": ["identifier"]}}]}),
            ("/query/aggregate", {"relations": ["MET", "CALLED", "USES"], "focus": person,
                                  "join_namesakes": join}),
            ("/query/aggregate", {"relations": ["USES"], "focus": person,
                                  "other": {"types": ["identifier"]}, "group_by": "other",
                                  "join_namesakes": join}),
            ("/query/sequence", {"first": {"relations": ["MET"], "other": person},
                                 "then": {"relations": ["PLACED_IN", "CALLED"]},
                                 "within_days": 60, "join_namesakes": join}),
        ]
    out = [(p, dict(b, include_weak=w)) if p.startswith("/query/") else (p, b)
           for p, b in out for w in (False, True)]
    out += [("/documents/profile", {}), ("/documents/profile", {"with_paths": True}),
            ("/documents/profile", {"path_prefix": "/c1"}),
            ("/review/list", {}), ("/review/list", {"q": "kiss"}), ("/review/history", {})]
    return out


def answer(state, path, body, access, corpora):
    """The service's answer as canonical JSON (an HTTP error as its status and text)."""
    try:
        out = service.handle_post(state, path, dict(body, corpus_ids=list(corpora),
                                                     access=access))
    except service.HttpError as e:
        out = {"error": e.status, "message": str(e)}
    if path == "/review/history":
        for item in out.get("history", []):
            item.pop("at", None)  # when the decision was made in this process
    return json.dumps(out, sort_keys=True, ensure_ascii=False)


def first_difference(got, want):
    at = next((i for i, (x, y) in enumerate(zip(got, want)) if x != y), min(len(got), len(want)))
    return "...%s\n  want: ...%s" % (got[max(0, at - 160):at + 160], want[max(0, at - 160):at + 160])


class LeakageTest(unittest.TestCase):
    def make_store(self):
        return MemoryGraphStore()

    def close_store(self, store):
        pass

    @classmethod
    def setUpClass(cls):
        cls.oracle = build(MemoryGraphStore(), PUBLIC)
        cls.oracle_merged = build(MemoryGraphStore(), PUBLIC, merge=True)

    def setUp(self):
        self.store = self.make_store()
        self.full = build(self.store, PUBLIC + HIDDEN)

    def tearDown(self):
        self.close_store(self.store)

    def assert_no_leak(self, full, oracle):
        leaks, shown = [], set()
        for path, body in requests():
            got = answer(full, path, body, LARS, ["c1"])
            want = answer(oracle, path, body, LARS, ["c1"])
            if got != want:
                leaks.append(path)
                if path not in shown:
                    shown.add(path)
                    print("\nLEAK %s %s\n  got:  %s" % (path, body, first_difference(got, want)))
        self.assertEqual([], leaks, "%d answers differ" % len(leaks))

    def test_a_restricted_caller_sees_exactly_the_graph_of_their_documents(self):
        self.assert_no_leak(self.full, self.oracle)

    def test_the_same_after_a_merge(self):
        # A2 (public only) folded into A (public and hidden documents).
        self.close_store(self.store)
        self.store = self.make_store()
        self.assert_no_leak(build(self.store, PUBLIC + HIDDEN, merge=True), self.oracle_merged)

    def test_the_same_holds_across_drives_for_a_caller_who_may_read_everything(self):
        # hanna reads every c1 document; c2 stays out of a c1 scope.
        full_c1 = build(MemoryGraphStore(), PUBLIC + HIDDEN[:-1])
        for path, body in requests():
            self.assertEqual(answer(full_c1, path, body, HANNA, ["c1"]),
                             answer(self.full, path, body, HANNA, ["c1"]), (path, body))

    def test_the_hidden_documents_matter_to_a_caller_who_may_read_them(self):
        differs = {path for path, body in requests()
                   if answer(self.full, path, body, HANNA, ["c1"])
                   != answer(self.oracle, path, body, HANNA, ["c1"])}
        expected = {"/query/" + q for q in (
            "find", "mentioned", "entity", "copresence", "timeline", "neighborhood", "path",
            "common", "shared", "communities", "ranking", "match", "aggregate", "sequence")}
        expected |= {"/documents/profile", "/review/list", "/review/history"}
        self.assertEqual(set(), expected - differs)

    def test_empty_scope_and_unknown_drive_see_nothing(self):
        for corpora in ([], ["c9"]):
            out = json.loads(answer(self.full, "/query/ranking", {"types": list(TYPES)}, HANNA,
                                    corpora))
            self.assertEqual([], out["ranking"])
            out = json.loads(answer(self.full, "/documents/profile", {"with_paths": True}, HANNA,
                                    corpora))
            self.assertEqual(({}, []), (out["counts"], out["paths"]))


if __name__ == "__main__":
    unittest.main()
