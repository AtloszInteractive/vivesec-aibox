"""Level-by-level investigation over the visible subgraph (queries.investigate)."""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import domain_profile  # noqa: E402
import queries  # noqa: E402
import service  # noqa: E402
from graph_store import MemoryGraphStore  # noqa: E402
from model import AttributeValue, Document, Entity, Evidence, Mention, Relation  # noqa: E402
from visibility import Scope  # noqa: E402

SECRET = {"restricted": True, "allow": ["user:anna"], "deny": [], "classification": 0}
ANNA = {"principals": ["user:anna"], "clearance": 0, "membership_known": True,
        "default_classification": 0}
BELA = {"principals": ["user:bela"], "clearance": 0, "membership_known": True,
        "default_classification": 0}
CONTACT, VIA, HOLDING = ["RELATIVE_OF"], ["INVOLVED_IN"], ["USES", "RESIDES_AT"]


def rel(rid, typ, a, b, doc, start=None):
    return Relation(rid, typ, a, b, start, None, "", [Evidence(doc, "%s %s %s" % (a, typ, b))])


def ent(eid, typ, subtype, label, *attrs):
    return Entity(eid, typ, subtype, label,
                  [AttributeValue(n, v, doc) for n, v, doc in attrs])


def put(store, doc_id, ents, rels, acl=None):
    store.put_document(Document(doc_id, "c1", "/a/%s.txt" % doc_id, acl=acl), ents,
                       [Mention("%s:m%d" % (doc_id, i), doc_id, e.id, e.label)
                        for i, e in enumerate(ents)], rels)


class InvestigateTest(unittest.TestCase):
    """S (seed inmate) has contacts K1, K2; K1 is also the contact of I2 and I3,
    K2 of I3 (a Y: I3 is reached through both). I2's other contact is K3, who is
    the contact of I4. S and M were both in incident E; M's contact is K4."""

    def setUp(self):
        s = MemoryGraphStore()
        people = {
            "S": ent("S", "person", "inmate", "Sas Tibor", ("registry_number", "NY-100", "d1")),
            "I2": ent("I2", "person", "inmate", "Ipoly Béla", ("registry_number", "NY-200", "d1")),
            "I3": ent("I3", "person", "inmate", "Izsó Csaba", ("registry_number", "NY-300", "d1")),
            "I4": ent("I4", "person", "inmate", "Illés Dénes"),
            "K1": ent("K1", "person", "", "Kende Anna"),
            "K2": ent("K2", "person", "", "Kiss Bori"),
            "K3": ent("K3", "person", "", "Kun Cecil"),
            "K4": ent("K4", "person", "", "Kardos Dóra"),
            "M": ent("M", "person", "inmate", "Major Ede", ("registry_number", "NY-400", "d2")),
        }
        phone = ent("P", "identifier", "phone", "+36 30 111 1111")
        addr = ent("A", "place", "address", "Fő utca 5.")
        incident = ent("E", "event", "incident", "verekedés (2025-03-01)")
        put(s, "d1", list(people.values()) + [phone, addr], [
            rel("r1", "RELATIVE_OF", "S", "K1", "d1"), rel("r2", "RELATIVE_OF", "S", "K2", "d1"),
            rel("r3", "RELATIVE_OF", "I2", "K1", "d1"), rel("r4", "RELATIVE_OF", "I3", "K1", "d1"),
            rel("r5", "RELATIVE_OF", "I3", "K2", "d1"), rel("r6", "RELATIVE_OF", "I2", "K3", "d1"),
            rel("r7", "RELATIVE_OF", "I4", "K3", "d1"), rel("r8", "RELATIVE_OF", "M", "K4", "d1"),
            rel("u1", "USES", "K1", "P", "d1"), rel("u2", "USES", "K3", "P", "d1"),
            rel("h1", "RESIDES_AT", "K1", "A", "d1"),
            rel("v1", "VISITED", "K4", "S", "d1", "2025-02-01")])
        put(s, "d2", [people["S"], people["M"], incident], [
            rel("x1", "INVOLVED_IN", "S", "E", "d2", "2025-03-01"),
            rel("x2", "INVOLVED_IN", "M", "E", "d2", "2025-03-01")], acl=SECRET)
        self.s = s

    def run_query(self, access, seeds, **kw):
        kw.setdefault("relations", CONTACT)
        kw.setdefault("via_relations", VIA)
        kw.setdefault("holding", HOLDING)
        return queries.investigate(self.s.view(Scope(["c1"], access)), seeds, **kw)

    @staticmethod
    def table(out):
        return [(r["node"]["id"], r["level"]) for r in out["rows"]]

    def test_levels_follow_contacts_and_shared_incidents(self):
        out = self.run_query(ANNA, ["S"], levels=4)
        # Level order, then name order within a level: deterministic.
        self.assertEqual([("S", 0), ("K1", 1), ("K2", 1), ("M", 1),
                          ("I2", 2), ("I3", 2), ("K4", 2), ("K3", 3), ("I4", 4)],
                         self.table(out))
        self.assertFalse(out["truncated"])
        self.assertEqual(4, out["levels"])
        rows = {r["node"]["id"]: r for r in out["rows"]}
        # The Y: I3 is the contact's contact through both K1 and K2.
        self.assertEqual(["K1", "K2"], sorted(v["from"] for v in rows["I3"]["via"]))
        # A shared incident is one level, and the link names the incident.
        via = rows["M"]["via"][0]
        self.assertEqual(("S", "E", "x1", "x2"), (via["from"], via["through"]["id"],
                                                  via["edge"]["id"], via["edge2"]["id"]))
        # The contact data come with the row; a visit is not followed.
        self.assertEqual([("A", "RESIDES_AT"), ("P", "USES")],
                         [(h["node"]["id"], h["edge"]["type"]) for h in rows["K1"]["held"]])
        self.assertEqual("NY-300", rows["I3"]["node"]["attributes"][0]["value"])

    def test_what_the_caller_may_not_read_is_not_followed(self):
        out = self.run_query(BELA, ["S"], levels=4)
        ids = [eid for eid, _ in self.table(out)]
        self.assertNotIn("M", ids)
        self.assertNotIn("K4", ids)
        self.assertEqual(["S", "K1", "K2", "I2", "I3", "K3", "I4"], ids)

    def test_levels_stop_where_asked(self):
        self.assertEqual([("S", 0), ("K1", 1), ("K2", 1), ("M", 1)],
                         self.table(self.run_query(ANNA, ["S"], levels=1)))

    def test_an_identifier_seed_reaches_its_holders_first(self):
        out = self.run_query(ANNA, ["P"], levels=2)
        self.assertEqual([("P", 0), ("K1", 1), ("K3", 1), ("I4", 2), ("I2", 2), ("I3", 2),
                          ("S", 2)], self.table(out))
        self.assertEqual([], out["rows"][0]["held"])

    def test_branch_and_limit_cap_the_table_and_say_so(self):
        out = self.run_query(ANNA, ["S"], levels=1, branch=1)
        self.assertEqual([("S", 0), ("K1", 1)], self.table(out))
        self.assertTrue(out["truncated"])
        self.assertEqual(2, out["rows"][0]["more"])
        out = self.run_query(ANNA, ["S"], levels=4, limit=3)
        self.assertEqual(3, len(out["rows"]))
        self.assertTrue(out["truncated"])

    def test_the_service_validates_and_joins_namesakes(self):
        store = MemoryGraphStore()
        put(store, "d1", [ent("S", "person", "inmate", "Sas Tibor"),
                          ent("K", "person", "", "Kende Anna")],
            [rel("r1", "RELATIVE_OF", "S", "K", "d1")])
        # The same name in another document, with a further contact of its own.
        put(store, "d2", [ent("S2", "person", "inmate", "Sas Tibor"),
                          ent("L", "person", "", "Lukács Lili")],
            [rel("r2", "RELATIVE_OF", "S2", "L", "d2")])
        state = service.State(store, domain_profile.load("bv"))
        body = {"corpus_ids": ["c1"], "access": ANNA, "seeds": ["S"],
                "relations": CONTACT, "levels": 2}

        def names(**extra):
            out = service.handle_post(state, "/query/investigate", dict(body, **extra))
            json.dumps(out)
            return [r["node"]["label"] for r in out["rows"]]
        self.assertEqual(["Sas Tibor", "Kende Anna"], names())
        self.assertEqual(["Sas Tibor", "Kende Anna", "Lukács Lili"],
                         names(join_namesakes=True))
        for bad in ({"seeds": []}, {"seeds": "S"}, {"levels": 5}, {"branch": 0},
                    {"relations": "RELATIVE_OF"}, {"join_namesakes": "yes"}):
            with self.assertRaises(service.HttpError):
                service.handle_post(state, "/query/investigate", dict(body, **bad))
        # An empty scope sees nothing, not the seed.
        out = service.handle_post(state, "/query/investigate", dict(body, corpus_ids=[]))
        self.assertEqual([], out["rows"])


if __name__ == "__main__":
    unittest.main()
