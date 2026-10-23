"""Patterns, counts and sequences over the visible subgraph."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import analytics  # noqa: E402
from graph_store import MemoryGraphStore  # noqa: E402
from model import Document, Entity, Evidence, Mention, Relation  # noqa: E402
from visibility import Scope  # noqa: E402

SECRET = {"restricted": True, "allow": ["user:anna"], "deny": [], "classification": 0}
ANNA = {"principals": ["user:anna"], "clearance": 0, "membership_known": True,
        "default_classification": 0}
BELA = {"principals": ["user:bela"], "clearance": 0, "membership_known": True,
        "default_classification": 0}
INMATE = {"subtypes": ["inmate"]}
VISITOR = {"subtypes": ["visitor"]}


def rel(rid, typ, a, b, start, doc, quote="q"):
    return Relation(rid, typ, a, b, start, None, "", [Evidence(doc, quote)])


class AnalyticsTest(unittest.TestCase):
    def setUp(self):
        s = MemoryGraphStore()
        people = [Entity("I1", "person", "inmate", "Kiss Ádám"),
                  Entity("I2", "person", "inmate", "Nagy Béla"),
                  Entity("V1", "person", "visitor", "Szabó Vera"),
                  Entity("C1", "place", "cell", "104. zárka")]
        s.put_document(Document("d1", "c1", "/a/d1.csv"), people,
                       [Mention("m%d" % i, "d1", e.id, e.label) for i, e in enumerate(people)],
                       [rel("v1", "VISITED", "V1", "I1", "2025-03-01", "d1"),
                        rel("v2", "VISITED", "V1", "I2", "2025-04-10", "d1"),
                        rel("p1", "PLACED_IN", "I1", "C1", "2025-01-01", "d1"),
                        rel("p2", "PLACED_IN", "I2", "C1", "2025-02-01", "d1")])
        incident = Entity("E1", "event", "incident", "verekedés")
        s.put_document(Document("d2", "c1", "/a/d2.pdf", acl=SECRET),
                       [Entity("I1", "person", "inmate", "Kiss Ádám"), incident],
                       [Mention("n1", "d2", "I1", "Kiss Ádám"), Mention("n2", "d2", "E1", "verekedés")],
                       [rel("x1", "INVOLVED_IN", "I1", "E1", "2025-03-20", "d2")])
        self.s = s

    def view(self, access):
        return self.s.view(Scope(["c1"], access))

    def test_pattern_visitor_to_inmate_to_cell(self):
        paths = analytics.match(self.view(BELA), VISITOR, [
            {"relations": ["VISITED"], "target": INMATE},
            {"relations": ["PLACED_IN"], "target": {"subtypes": ["cell"]}}])
        self.assertEqual([["V1", "I1", "C1"], ["V1", "I2", "C1"]],
                         [[n["id"] for n in p["nodes"]] for p in paths])
        # Relations match either way round: the inmate side may start the pattern.
        paths = analytics.match(self.view(BELA), {"ids": ["I2"]},
                                [{"relations": ["VISITED"], "target": None}])
        self.assertEqual([["I2", "V1"]], [[n["id"] for n in p["nodes"]] for p in paths])

    def test_counts_by_entity_and_month_with_period(self):
        rows = analytics.aggregate(self.view(BELA), ["VISITED"], VISITOR, INMATE, "focus")
        self.assertEqual([("Szabó Vera", 2, 2)], [(r["label"], r["count"], r["distinct"])
                                                  for r in rows])
        self.assertEqual({"I1", "I2", "V1"}, {n["id"] for n in rows[0]["nodes"]})
        rows = analytics.aggregate(self.view(BELA), ["VISITED"], group_by="month",
                                   date_from="2025-04")
        self.assertEqual([("2025-04", 1)], [(r["key"], r["count"]) for r in rows])
        rows = analytics.aggregate(self.view(ANNA), None, INMATE, None, "relation")
        self.assertEqual({"PLACED_IN": 2, "VISITED": 2, "INVOLVED_IN": 1},
                         {r["key"]: r["count"] for r in rows})
        with self.assertRaises(ValueError):
            analytics.aggregate(self.view(BELA), group_by="salary")

    def test_sequence_respects_window_and_visibility(self):
        args = ({"relations": ["VISITED"]}, {"relations": ["INVOLVED_IN"]})
        pairs = analytics.sequence(self.view(ANNA), *args, within_days=30, focus=INMATE)
        self.assertEqual([("Kiss Ádám", 19)], [(p["focus"]["label"], p["days"]) for p in pairs])
        self.assertEqual([], analytics.sequence(self.view(ANNA), *args, within_days=10,
                                                focus=INMATE))
        self.assertEqual([], analytics.sequence(self.view(BELA), *args, within_days=30,
                                                focus=INMATE))

    def test_either_end_of_a_link_may_be_the_focus(self):
        person = {"types": ["person"]}
        # A visit links two people; the incident is the visited inmate's, not the visitor's.
        pairs = analytics.sequence(self.view(ANNA), {"relations": ["VISITED"]},
                                   {"relations": ["INVOLVED_IN"]}, within_days=30, focus=person)
        self.assertEqual([("Kiss Ádám", 19)], [(p["focus"]["label"], p["days"]) for p in pairs])
        self.assertEqual(["Szabó Vera", "verekedés"], [n["label"] for n in pairs[0]["nodes"]])
        paths = analytics.match(self.view(BELA), person, [
            {"relations": ["VISITED"], "target": person},
            {"relations": ["PLACED_IN"], "target": {"subtypes": ["cell"]}}])
        self.assertEqual([["V1", "I1", "C1"], ["V1", "I2", "C1"]],
                         [[n["id"] for n in p["nodes"]] for p in paths])
        rows = analytics.aggregate(self.view(BELA), ["VISITED"], person, None, "focus")
        self.assertEqual({"Szabó Vera": 2, "Kiss Ádám": 1, "Nagy Béla": 1},
                         {r["label"]: r["count"] for r in rows})
        # Per month a link still counts once.
        rows = analytics.aggregate(self.view(BELA), ["VISITED"], person, None, "month")
        self.assertEqual([("2025-03", 1), ("2025-04", 1)], [(r["key"], r["count"]) for r in rows])

    def test_service_route_validates_and_lists_documents(self):
        import domain_profile
        import service

        state = service.State(self.s, domain_profile.load("bv"))
        body = {"corpus_ids": ["c1"], "access": BELA}
        out = service.run_query(state, "aggregate", dict(
            body, relations=["VISITED"], focus=VISITOR, other=INMATE, group_by="month"))
        self.assertEqual(["2025-03", "2025-04"], [r["key"] for r in out["rows"]])
        self.assertEqual({"d1"}, set(out["documents"]))
        out = service.run_query(state, "sequence", dict(
            body, first={"relations": ["VISITED"]}, then={"relations": ["PLACED_IN"]},
            within_days=400, focus=INMATE))
        self.assertEqual([], out["pairs"])  # placements precede the visits
        for bad in ({"steps": []}, {"steps": [{"relations": "VISITED"}]},
                    {"steps": [{"target": {"types": ["ghost"]}}]},
                    {"steps": [{}], "date_from": "tegnap"}):
            with self.assertRaises(service.HttpError):
                service.run_query(state, "match", dict(body, **bad))


if __name__ == "__main__":
    unittest.main()
