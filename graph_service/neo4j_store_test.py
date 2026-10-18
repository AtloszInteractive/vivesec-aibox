"""The Neo4j store must behave exactly like the memory store.

Opt-in, because it WIPES the graph in the target database:
    GRAPH_TEST_NEO4J_URI=bolt://127.0.0.1:7687
    GRAPH_TEST_NEO4J_PASSWORD_FILE=<file with the password>   (user: neo4j)
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import queries  # noqa: E402
import visibility_test  # noqa: E402
from model import Document, Entity, Evidence, Mention, Relation  # noqa: E402
from visibility import Scope  # noqa: E402

URI = os.environ.get("GRAPH_TEST_NEO4J_URI", "")
PASSWORD_FILE = os.environ.get("GRAPH_TEST_NEO4J_PASSWORD_FILE", "")


def neo4j_store():
    from neo4j_store import Neo4jGraphStore

    with open(PASSWORD_FILE, encoding="utf-8") as f:
        store = Neo4jGraphStore(URI, "neo4j", f.read().strip())
    store.wipe()
    return store


@unittest.skipUnless(URI and PASSWORD_FILE, "GRAPH_TEST_NEO4J_URI not set")
class Neo4jVisibilityTest(visibility_test.VisibilityTest):
    def make_store(self):
        self._store = neo4j_store()
        return self._store

    def tearDown(self):
        self._store.close()


@unittest.skipUnless(URI and PASSWORD_FILE, "GRAPH_TEST_NEO4J_URI not set")
class Neo4jStoreTest(unittest.TestCase):
    def setUp(self):
        self.s = neo4j_store()

    def tearDown(self):
        self.s.close()

    def test_reingesting_a_document_replaces_its_data(self):
        doc = Document("d1", "c1", "/x/d1.pdf")
        a, b = Entity("A", "person", "", "Kiss Ádám"), Entity("B", "person", "", "Nagy Béla")
        self.s.put_document(doc, [a, b], [Mention("m1", "d1", "A", "Kiss Ádám"),
                                          Mention("m2", "d1", "B", "Nagy Béla")],
                            [Relation("r1", "MET", "A", "B", "2026-01-01", "2026-01-01", "",
                                      [Evidence("d1", "q")])])
        self.s.put_document(doc, [a], [Mention("m1", "d1", "A", "Kiss Ádám")], [])
        self.assertEqual({"documents": 1, "entities": 1, "mentions": 1, "relations": 0},
                         self.s.counts())

    def test_failed_document_is_rolled_back(self):
        from graph_store import StoreError

        doc = Document("d1", "c1", "/x/d1.pdf")
        with self.assertRaises(StoreError):
            self.s.put_document(doc, [Entity("A", "person", "", "x")],
                                [Mention("m1", "d1", "A", "x")],
                                [Relation("r1", "MET", "A", "missing", None, None, "",
                                          [Evidence("d1", "q")])])
        self.assertEqual(0, self.s.counts()["entities"])

    def test_path_over_persisted_graph(self):
        for i in range(4):
            doc = Document("d%d" % i, "c1", "/x/d%d.pdf" % i)
            ents = [Entity("P%d" % i, "person", "", "P%d" % i),
                    Entity("P%d" % (i + 1), "person", "", "P%d" % (i + 1))]
            self.s.put_document(doc, ents,
                                [Mention("m%d-%d" % (i, j), doc.doc_id, e.id, e.label)
                                 for j, e in enumerate(ents)],
                                [Relation("r%d" % i, "MET", ents[0].id, ents[1].id, None, None,
                                          "", [Evidence(doc.doc_id, "q")])])
        path = queries.shortest_path(self.s.view(Scope(["c1"])), "P0", "P4")
        self.assertEqual(["P0", "P1", "P2", "P3", "P4"], [n["id"] for n in path["nodes"]])


if __name__ == "__main__":
    unittest.main()
