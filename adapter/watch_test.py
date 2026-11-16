import os
import tempfile
import unittest

import watch

EDGES = [
    {"id": "r1", "type": "MET", "a": "p1", "b": "p2", "start": "2026-01-02",
     "evidence": [{"doc_id": "d1"}]},
    {"id": "r2", "type": "CONTACT_OF", "a": "p3", "b": "p1", "start": None,
     "evidence": [{"doc_id": "d1"}, {"doc_id": "d1"}]},
    {"id": "r3", "type": "MET", "a": "p2", "b": "p3", "evidence": [{"doc_id": "d2"}]},
]
P1 = {"id": "p1", "label": "Kovács János", "type": "person", "subtype": "inmate"}
NODES = [P1, {"id": "p2", "label": "Nagy Éva"}, {"id": "p3", "label": "Titkos Tamás"}]
MET = "MET|p2|nagy éva"
CONTACT = "CONTACT_OF|p3|titkos tamás"


class Clock:
    now = 1000.0

    def __call__(self):
        return self.now


class WatchTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.clock = Clock()
        self.store = watch.WatchStore(self.temp.name, max_days=30, max_items=2, clock=self.clock)

    def test_snapshot_counts_documents_of_the_entitys_own_relations(self):
        self.assertEqual({MET: 1, CONTACT: 1}, watch.snapshot(EDGES, "p1", NODES))

    def test_diff_reports_new_relations_and_new_evidence_only(self):
        item, _ = self.store.add("lars", "/d", P1, EDGES[:1], nodes=NODES)
        later = [dict(EDGES[0], evidence=[{"doc_id": "d1"}, {"doc_id": "d3"}]), EDGES[1],
                 EDGES[2]]
        change = watch.diff(item, {"nodes": NODES, "edges": later})
        self.assertEqual(["Titkos Tamás"], [r["other"]["label"] for r in change["new"]])
        self.assertEqual([(MET, 1, 2)], [(r["id"], r["before"], r["documents"])
                                         for r in change["more"]])
        self.assertEqual(0, change["gone"])
        self.assertEqual(1, watch.diff(item, {"nodes": [P1], "edges": []})["gone"])

    def test_a_document_read_again_can_give_an_id_to_another_name(self):
        item, _ = self.store.add("lars", "/d", P1, EDGES[:1], nodes=NODES)
        renamed = [P1, {"id": "p2", "label": "Titkos Tamás"}]
        change = watch.diff(item, {"nodes": renamed, "edges": EDGES[:1]})
        self.assertEqual((["Titkos Tamás"], 1), ([r["other"]["label"] for r in change["new"]],
                                                 change["gone"]))

    def test_entries_are_per_user_and_scope_capped_and_expire(self):
        item, created = self.store.add("lars", "/d", P1, EDGES)
        self.assertTrue(created)
        self.assertFalse(self.store.add("lars", "/d", P1, EDGES)[1])
        self.assertEqual([], self.store.list("hanna", "/d"))
        self.assertEqual([], self.store.list("lars", "/other"))
        self.store.add("lars", "/d", dict(P1, id="p2"), EDGES)
        with self.assertRaises(watch.WatchFull):
            self.store.add("lars", "/d", dict(P1, id="p3"), EDGES)
        # A fresh store reads them back; after max_days they are gone.
        again = watch.WatchStore(self.temp.name, max_days=30, max_items=2, clock=self.clock)
        self.assertEqual(2, len(again.list("lars", "/d")))
        self.clock.now += 31 * 86400
        self.assertEqual([], again.list("lars", "/d"))
        self.assertEqual([], os.listdir(self.temp.name))

    def test_seen_takes_over_the_last_check_and_keeps_relations_not_visible_now(self):
        item, _ = self.store.add("lars", "/d", P1, EDGES, nodes=NODES)
        self.assertFalse(self.store.seen("lars", "/d", item["id"]))  # not checked yet
        self.store.checked("lars", "/d", item["id"], {MET: 2})
        self.assertTrue(self.store.seen("lars", "/d", item["id"]))
        self.assertEqual({MET: 2, CONTACT: 1}, self.store.get("lars", "/d", item["id"])["snapshot"])
        self.assertNotIn("snapshot", watch.WatchStore.public(item))

    def test_only_on_turns_it_on(self):
        for value, expected in (("on", True), ("ON ", True), ("yes", False), ("", False)):
            self.assertEqual(expected, watch.enabled_from_env({"ADAPTER_WATCHLIST": value}))
        self.assertFalse(watch.enabled_from_env({}))


if __name__ == "__main__":
    unittest.main()
