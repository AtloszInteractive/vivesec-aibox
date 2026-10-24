"""The graph queries on perfect extraction must reproduce the answer key."""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(1, os.path.join(ROOT, "graph_service"))
sys.path.append(os.path.join(ROOT, "demo-corpus"))

import domain_profile  # noqa: E402
import generate  # noqa: E402
import oracle  # noqa: E402
import queries  # noqa: E402
import world  # noqa: E402
from graph_store import MemoryGraphStore  # noqa: E402
from ingest import parse_document_payload  # noqa: E402
from render import Renderer  # noqa: E402
from visibility import Scope  # noqa: E402


class OracleGraphTest(unittest.TestCase):
    @classmethod
    def make_store(cls):
        return MemoryGraphStore()

    @classmethod
    def setUpClass(cls):
        w = world.build(90, 11)
        docs = Renderer(w, 11).render_all()
        cls.key = generate.build_answer_key(w, docs, 11)
        meta = {}
        for d in docs:
            source = "%s/%s/%s" % (generate.DRIVE_PREFIX, d.drive, d.rel)
            corpus = generate.corpus_id_of_drive_root("%s/%s" % (generate.DRIVE_PREFIX, d.drive))
            meta[d.key] = {"doc_id": generate.make_doc_id("default", corpus, source),
                           "corpus_id": corpus, "source_path": source, "kind": d.kind}
        cls.corpora = sorted({m["corpus_id"] for m in meta.values()})
        profile = domain_profile.load("bv")
        cls.store = cls.make_store()
        for body in oracle.payloads(cls.key, meta):
            cls.store.put_document(*parse_document_payload(body, profile))
        cls.view = cls.store.view(Scope(cls.corpora))
        cls.ents = oracle.entity_index(cls.key)

    def test_shared_identifiers_match_the_key(self):
        expected = {self.ents[s["value"]]["id"]: set(s["persons"])
                    for s in self.key["questions"]["shared_identifiers"]}
        got = {}
        for item in queries.shared_identifiers(self.view, ("identifier", "place"), ("person",),
                                               limit=500, subtypes=("phone", "vehicle_plate",
                                                                    "address"),
                                               relation_types=("USES", "OWNS", "RESIDES_AT")):
            got[item["identifier"]["id"]] = {h["node"]["id"] for h in item["holders"]}
        self.assertEqual(expected, got)

    def test_cellmates_match_the_key(self):
        for q in self.key["questions"]["copresence"]:
            found = {c["other"]["id"] for c in queries.copresence(self.view, q["person"],
                                                                  ["PLACED_IN"])
                     if c["place"]["id"] == q["cell"]}
            self.assertEqual({m["person"] for m in q["cellmates"]}, found, q["person"])

    def test_gold_pairs_are_connected_by_shortest_paths(self):
        for q in self.key["questions"]["path"]:
            path = queries.shortest_path(self.view, q["from"], q["to"], max_hops=8)
            self.assertIsNotNone(path, q)
            self.assertEqual(queries.bfs_distance(self.view, q["from"], q["to"]), path["hops"])
            for edge in path["edges"]:
                self.assertTrue(edge["evidence"])

    def test_name_twins_stay_separate_entities(self):
        for twin, original in self.key["questions"]["must_not_merge"]:
            a, b = self.view.node(twin), self.view.node(original)
            self.assertIsNotNone(a)
            self.assertIsNotNone(b)
            self.assertNotEqual(a["id"], b["id"])

    def test_timeline_is_sorted_and_sourced(self):
        pid = self.key["questions"]["timeline"][0]["person"]
        events = queries.timeline(self.view, pid)
        self.assertTrue(events)
        self.assertEqual(sorted(e["date"] for e in events), [e["date"] for e in events])
        self.assertTrue(all(e["evidence"] for e in events))

    def test_narrower_scope_hides_other_drives(self):
        jelentesek = [c for c in self.corpora if c.startswith("jelentesek")]
        view = self.store.view(Scope(jelentesek))
        for edge in view.edges():
            self.assertIn(edge["type"], {"MET", "LOCATED_AT", "INVOLVED_IN", "USES"})

    def test_communities_recover_groups(self):
        found = queries.communities(self.view)
        self.assertTrue(found)
        self.assertTrue(all(c["size"] >= 2 for c in found))


NEO4J_URI = os.environ.get("GRAPH_TEST_NEO4J_URI", "")
NEO4J_PASSWORD_FILE = os.environ.get("GRAPH_TEST_NEO4J_PASSWORD_FILE", "")


@unittest.skipUnless(NEO4J_URI and NEO4J_PASSWORD_FILE, "GRAPH_TEST_NEO4J_URI not set (wipes the graph)")
class Neo4jOracleGraphTest(OracleGraphTest):
    @classmethod
    def make_store(cls):
        from neo4j_store import Neo4jGraphStore

        with open(NEO4J_PASSWORD_FILE, encoding="utf-8") as f:
            store = Neo4jGraphStore(NEO4J_URI, "neo4j", f.read().strip())
        store.wipe()
        return store

    @classmethod
    def tearDownClass(cls):
        cls.store.close()


if __name__ == "__main__":
    unittest.main()
