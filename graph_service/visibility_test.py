"""Edge-level visibility: a relation is only as visible as its evidence."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import queries  # noqa: E402
from graph_store import MemoryGraphStore  # noqa: E402
from model import Document, Entity, Evidence, Mention, Relation  # noqa: E402
from visibility import Scope  # noqa: E402

SECRET = {"restricted": True, "allow": ["user:anna"], "deny": [], "classification": 0}
ANNA = {"principals": ["user:anna"], "clearance": 0, "membership_known": True,
        "default_classification": 0}
BELA = {"principals": ["user:bela"], "clearance": 0, "membership_known": True,
        "default_classification": 0}


def person(eid, name):
    return Entity(eid, "person", "", name)


class VisibilityTest(unittest.TestCase):
    def setUp(self):
        s = MemoryGraphStore()
        # d1 (open): X met Y.  d2 (restricted to anna): Y met Z, and Z's alias.
        s.put_document(Document("d1", "c1", "/a/d1.pdf"),
                       [person("X", "Kiss Ádám"), person("Y", "Nagy Béla")],
                       [Mention("m1", "d1", "X", "Kiss Ádám"), Mention("m2", "d1", "Y", "Nagy Béla")],
                       [Relation("r1", "MET", "X", "Y", "2026-01-02", "2026-01-02", "",
                                 [Evidence("d1", "Kiss Ádám találkozott Nagy Bélával")])])
        s.put_document(Document("d2", "c1", "/a/d2.pdf", acl=SECRET),
                       [person("Y", "Nagy Béla"), person("Z", "Titkos Zoltán")],
                       [Mention("m3", "d2", "Y", "N. Béla"), Mention("m4", "d2", "Z", "Titkos Zoltán")],
                       [Relation("r2", "MET", "Y", "Z", "2026-02-03", "2026-02-03", "",
                                 [Evidence("d2", "N. Béla találkozott Titkos Zoltánnal")]),
                        Relation("r1b", "MET", "X", "Y", "2026-01-02", "2026-01-02", "",
                                 [Evidence("d2", "titkos megerősítés")])])
        self.s = s

    def view(self, access, corpora=("c1",)):
        return self.s.view(Scope(list(corpora), access))

    def test_hidden_document_hides_its_only_edge_and_entity(self):
        v = self.view(BELA)
        self.assertIsNone(v.node("Z"))
        self.assertEqual(["r1"], [e["id"] for e in v.edges_of("Y")])
        self.assertIsNone(queries.shortest_path(v, "X", "Z"))

    def test_shared_edge_keeps_only_visible_evidence(self):
        edge = self.view(BELA).edges_of("X")[0]
        self.assertEqual(["d1"], [ev["doc_id"] for ev in edge["evidence"]])
        edge = self.view(ANNA).edges_of("X")[0]
        self.assertEqual(["d1", "d2"], sorted(ev["doc_id"] for ev in edge["evidence"]))

    def test_label_and_aliases_come_from_visible_mentions_only(self):
        self.assertEqual(["Nagy Béla"], self.view(BELA).node("Y")["aliases"])
        self.assertEqual(["N. Béla", "Nagy Béla"], self.view(ANNA).node("Y")["aliases"])

    def test_authorised_user_sees_the_whole_path(self):
        path = queries.shortest_path(self.view(ANNA), "X", "Z")
        self.assertEqual(["X", "Y", "Z"], [n["id"] for n in path["nodes"]])

    def test_counts_do_not_reveal_hidden_neighbours(self):
        ranking = {r["node"]["id"]: r["degree"] for r in queries.degree_ranking(self.view(BELA))}
        self.assertEqual({"X": 1, "Y": 1}, ranking)

    def test_other_corpus_and_empty_scope_see_nothing(self):
        self.assertIsNone(self.view(ANNA, corpora=("c2",)).node("X"))
        self.assertEqual([], list(self.view(ANNA, corpora=()).nodes()))

    def test_dropping_a_document_removes_its_evidence_only(self):
        self.s.drop_document("d2")
        v = self.view(ANNA)
        self.assertIsNone(v.node("Z"))
        self.assertEqual(["d1"], [ev["doc_id"] for ev in v.edges_of("X")[0]["evidence"]])

    def test_acl_change_applies_without_reingest(self):
        self.s.set_acl("d2", None)
        self.assertIsNotNone(self.view(BELA).node("Z"))

    def test_merge_moves_mentions_and_edges(self):
        self.s.put_document(Document("d3", "c1", "/a/d3.pdf"), [person("Y2", "Nagy Bela")],
                            [Mention("m5", "d3", "Y2", "Nagy Bela")], [])
        self.s.merge_entities("Y", "Y2")
        v = self.view(BELA)
        self.assertIsNone(v.node("Y2"))
        self.assertIn("Nagy Bela", v.node("Y")["aliases"])


if __name__ == "__main__":
    unittest.main()
