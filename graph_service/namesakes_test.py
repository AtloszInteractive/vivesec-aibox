"""Analytics over records joined by name, and the cases that must stay apart."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import analytics  # noqa: E402
import namesakes  # noqa: E402
from graph_store import MemoryGraphStore  # noqa: E402
from model import AttributeValue, Document, Entity, Evidence, Mention, Relation  # noqa: E402
from visibility import Scope  # noqa: E402

ANYONE = {"principals": ["user:anna"], "clearance": 0, "membership_known": True,
          "default_classification": 0}
SECRET = {"restricted": True, "allow": ["user:zoe"], "deny": [], "classification": 0}


def person(eid, label, subtype="", **attrs):
    return Entity(eid, "person", subtype, label,
                  [AttributeValue(k, v, "d-" + eid) for k, v in attrs.items()])


def put(store, doc, entities, relations, acl=None):
    store.put_document(Document(doc, "c1", "/a/%s.txt" % doc, acl=acl), entities,
                       [Mention("m-%s-%s" % (doc, e.id), doc, e.id, e.label) for e in entities],
                       relations)


def rel(rid, typ, a, b, start, doc):
    return Relation(rid, typ, a, b, start, None, "", [Evidence(doc, "q")])


class NamesakesTest(unittest.TestCase):
    def setUp(self):
        s = MemoryGraphStore()
        cell = Entity("C1", "place", "cell", "102. zárka")
        # The placement list knows the birth date ...
        put(s, "d-P1", [person("P1", "Bogdán Balázs", "inmate", birth_date="1981-08-09"), cell],
            [rel("p1", "PLACED_IN", "P1", "C1", "2025-08-02", "d-P1")])
        # ... the visit log and the incident report only the name.
        put(s, "d-P2", [person("P2", "Bogdán Balázs", "inmate"), person("V1", "Fazekas Péter",
                                                                       "visitor")],
            [rel("v1", "VISITED", "V1", "P2", "2026-04-16", "d-P2")])
        put(s, "d-P3", [person("P3", "Bogdán Balázs"), Entity("E1", "event", "incident", "vita")],
            [rel("x1", "INVOLVED_IN", "P3", "E1", "2026-05-01", "d-P3")])
        # Two Nagy Bélas with different birth dates are two people.
        put(s, "d-N1", [person("N1", "Nagy Béla", "inmate", birth_date="1970-01-01"), cell],
            [rel("p2", "PLACED_IN", "N1", "C1", "2025-01-01", "d-N1")])
        put(s, "d-N2", [person("N2", "Nagy Béla", "inmate", birth_date="1990-01-01"),
                        person("V2", "Kiss Éva", "visitor")],
            [rel("v2", "VISITED", "V2", "N2", "2026-01-01", "d-N2")])
        # An inmate and a visitor of one name are two people.
        put(s, "d-K1", [person("K1", "Kovács Péter", "inmate"), cell],
            [rel("p3", "PLACED_IN", "K1", "C1", "2025-02-01", "d-K1")])
        put(s, "d-K2", [person("K2", "Kovács Péter", "visitor"), person("I9", "Tóth Ödön",
                                                                       "inmate")],
            [rel("v3", "VISITED", "K2", "I9", "2026-02-01", "d-K2")])
        self.s = s

    def view(self, access=ANYONE, separate=frozenset()):
        return namesakes.JoinedView(self.s.view(Scope(["c1"], access)), separate)

    def test_cross_document_pattern_and_sequence_join_by_name(self):
        steps = [{"relations": ["VISITED"], "target": {"subtypes": ["inmate"]}},
                 {"relations": ["PLACED_IN"], "target": {"subtypes": ["cell"]}}]
        plain = self.s.view(Scope(["c1"], ANYONE))
        self.assertEqual([], analytics.match(plain, {"subtypes": ["visitor"]}, steps))
        paths = analytics.match(self.view(), {"subtypes": ["visitor"]}, steps)
        self.assertEqual([["Fazekas Péter", "Bogdán Balázs", "102. zárka"]],
                         [[n["label"] for n in p["nodes"]] for p in paths])
        joined = paths[0]["nodes"][1]
        self.assertEqual(["P1", "P2", "P3"], joined["joined"])
        self.assertEqual("P1", joined["id"])  # the record with the birth date stands for them
        self.assertEqual("inmate", joined["subtype"])
        pairs = analytics.sequence(self.view(), {"relations": ["VISITED"]},
                                   {"relations": ["INVOLVED_IN"]}, 30,
                                   {"subtypes": ["inmate"]})
        self.assertEqual([("Bogdán Balázs", 15)], [(p["focus"]["label"], p["days"])
                                                   for p in pairs])

    def test_conflicting_records_and_separate_decisions_stay_apart(self):
        view = self.view()
        self.assertEqual(["Kovács Péter", "Nagy Béla"], sorted(view.ambiguous))
        self.assertNotIn("N1", view.rep)
        self.assertNotIn("K1", view.rep)
        view = self.view(separate={("P3", "P1")})
        self.assertNotIn("P1", view.rep)
        self.assertIn("Bogdán Balázs", view.ambiguous)

    def test_counts_merge_and_ids_are_canonical(self):
        view = self.view()
        rows = analytics.aggregate(view, None, view.canon_spec({"ids": ["P3"]}), None, "relation")
        self.assertEqual({"PLACED_IN": 1, "VISITED": 1, "INVOLVED_IN": 1},
                         {r["key"]: r["count"] for r in rows})

    def test_hidden_documents_neither_join_nor_split(self):
        s = self.s
        # A hidden record with another birth date must not split the visible ones ...
        put(s, "d-H1", [person("H1", "Bogdán Balázs", "inmate", birth_date="2000-01-01")], [],
            acl=SECRET)
        view = self.view()
        self.assertEqual("P1", view.rep.get("P2"))
        self.assertNotIn("H1", view.rep)
        # ... and its links stay out of the joined person.
        self.assertTrue(all("d-H1" not in e["evidence"][0]["doc_id"]
                            for e in view.edges_of("P1")))

    def test_service_joins_only_on_request(self):
        import domain_profile
        import service

        state = service.State(self.s, domain_profile.load("bv"))
        body = {"corpus_ids": ["c1"], "access": ANYONE, "start": {"subtypes": ["visitor"]},
                "steps": [{"relations": ["VISITED"]}, {"relations": ["PLACED_IN"]}]}
        out = service.run_query(state, "match", body)
        self.assertEqual(([], None), (out["paths"], out.get("namesakes")))
        out = service.run_query(state, "match", dict(body, join_namesakes=True))
        self.assertEqual(1, len(out["paths"]))
        self.assertEqual({"ambiguous": 2}, out["namesakes"])
        self.assertTrue({"d-P1", "d-P2"} <= set(out["documents"]))
        # Named ids of any record of the person find the joined one.
        out = service.run_query(state, "aggregate", {
            "corpus_ids": ["c1"], "access": ANYONE, "focus": {"ids": ["P2"]},
            "group_by": "relation", "join_namesakes": True})
        self.assertEqual(3, len(out["rows"]))
        with self.assertRaises(service.HttpError):
            service.run_query(state, "match", dict(body, join_namesakes="yes"))


if __name__ == "__main__":
    unittest.main()
