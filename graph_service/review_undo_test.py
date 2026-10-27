"""Bulk review decisions, the decision history and undoing a decision (E26)."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import domain_profile  # noqa: E402
import service  # noqa: E402
from graph_store import MemoryGraphStore  # noqa: E402
from ingest_jobs import IngestWorker  # noqa: E402
from ingest_jobs_test import NOTE_1, NOTE_2, FakeLLM, doc  # noqa: E402
from model import AttributeValue, Document, Entity, Evidence, Mention, Relation  # noqa: E402

SCOPE = {"corpus_ids": ["c1", "c2"]}


def snapshot(store):
    """The graph without relation ids (an undone merge re-creates relations)."""
    store = getattr(store, "mirror", store)
    return {
        "active": sorted(e.id for e in store.entities.values() if e.status == "active"
                         and (store._mentions_of.get(e.id) or e.attributes)),
        "mentions": sorted((m.id, m.entity_id) for m in store.mentions.values()),
        "attrs": sorted((e.id, a.name, a.value, a.doc_id) for e in store.entities.values()
                        for a in e.attributes),
        "relations": sorted((r.type, r.a, r.b, tuple(sorted(ev.quote for ev in r.evidence)))
                            for r in store.relations.values()),
    }


class ReviewFlowTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.review_path = os.path.join(self.tmp.name, "review.jsonl")
        self.state = service.State(MemoryGraphStore(), domain_profile.load("bv"))
        self.state.worker = IngestWorker(self.state, FakeLLM(), review_path=self.review_path)
        self.post("/ingest/document", doc("d1", "c1", NOTE_1))
        self.post("/ingest/document", doc("d2", "c2", NOTE_2))
        self.assertTrue(self.state.worker.wait_idle())
        [self.item] = self.post("/review/list", SCOPE)["review"]

    def tearDown(self):
        self.tmp.cleanup()

    def post(self, path, body):
        return service.handle_post(self.state, path, body)

    def pair(self, **extra):
        return dict(SCOPE, a=self.item["a"], b=self.item["b"], **extra)

    def test_list_filters_and_counts_reasons(self):
        out = self.post("/review/list", dict(SCOPE, q="kovacs"))
        self.assertEqual((1, 1, {"same name": 1}), (out["total"], out["open_total"], out["reasons"]))
        self.assertEqual(0, self.post("/review/list", dict(SCOPE, q="nagy"))["total"])
        self.assertEqual(0, self.post("/review/list", dict(SCOPE, reason="initial only"))["total"])
        self.assertEqual([], self.post("/review/list", dict(SCOPE, offset=1))["review"])

    def test_bulk_decision_reports_pairs_it_could_not_decide(self):
        out = self.post("/review/decide", dict(SCOPE, decision="separate", by="admin", pairs=[
            {"a": self.item["a"], "b": self.item["b"]}, {"a": "person:x", "b": "person:y"}]))
        self.assertEqual(("separate", 1), (out["decided"], out["done"]))
        self.assertEqual(["person:x"], [s["a"] for s in out["skipped"]])
        self.assertEqual(0, self.post("/review/list", SCOPE)["total"])
        for bad in ([], "x", [1]):
            with self.assertRaises(service.HttpError) as ctx:
                self.post("/review/decide", dict(SCOPE, decision="merge", pairs=bad))
            self.assertEqual(400, ctx.exception.status)

    def test_a_merge_is_listed_and_undone(self):
        before = snapshot(self.state.store)
        self.post("/review/decide", self.pair(decision="merge", by="admin"))
        self.assertNotEqual(before, snapshot(self.state.store))
        [entry] = self.post("/review/history", SCOPE)["history"]
        self.assertEqual(("merge", "admin", "Kovács János", "Kovács János", True, "same name"),
                         (entry["decision"], entry["by"], entry["a_label"], entry["b_label"],
                          entry["undoable"], entry["reason"]))
        out = self.post("/review/undo", self.pair(by="admin"))
        self.assertEqual(("merge", 1, []), (out["undone"], out["documents"], out["failed"]))
        self.assertEqual(before, snapshot(self.state.store))
        self.assertEqual([self.item["a"]], [i["a"] for i in self.post("/review/list", SCOPE)["review"]])
        self.assertEqual([], self.post("/review/history", SCOPE)["history"])
        # The revived person resolves again: the pair can be decided anew.
        self.post("/review/decide", self.pair(decision="separate"))
        with open(self.review_path, encoding="utf-8") as f:
            events = [json.loads(line)["op"] for line in f]
        self.assertEqual(["candidate", "decided", "undone", "decided"], events)

    def test_undo_after_the_folded_document_was_extracted_again(self):
        before = snapshot(self.state.store)
        self.post("/review/decide", self.pair(decision="merge"))
        # A changed file comes back under a new document id at the same path.
        self.state.worker.llm.ref = "x"
        newer = doc("d2-v2", "c2", NOTE_2)
        newer["source_path"] = "/c2/d2.txt"
        self.post("/ingest/document", newer)
        self.assertTrue(self.state.worker.wait_idle())
        self.assertEqual(["d1", "d2-v2"], sorted(self.state.store.documents))
        self.assertTrue(self.post("/review/history", SCOPE)["history"][0]["undoable"])
        self.assertEqual(1, self.post("/review/undo", self.pair())["documents"])
        after = snapshot(self.state.store)
        self.assertEqual(before["active"], after["active"])
        self.assertEqual(before["relations"], after["relations"])

    def test_undoing_keep_apart_reopens_the_pair(self):
        self.post("/review/decide", self.pair(decision="separate", by="admin"))
        [entry] = self.post("/review/history", SCOPE)["history"]
        self.assertEqual(("separate", True), (entry["decision"], entry["undoable"]))
        self.assertEqual("separate", self.post("/review/undo", self.pair())["undone"])
        self.assertEqual(1, self.post("/review/list", SCOPE)["total"])

    def test_history_and_undo_follow_the_scope(self):
        self.post("/review/decide", self.pair(decision="merge"))
        # c2 alone shows the folded person's document but not the survivor's.
        self.assertEqual([], self.post("/review/history", {"corpus_ids": ["c2"]})["history"])
        with self.assertRaises(service.HttpError) as ctx:
            self.post("/review/undo", {"corpus_ids": ["c2"], "a": self.item["a"],
                                       "b": self.item["b"]})
        self.assertEqual(404, ctx.exception.status)
        # c1 alone: the folded person's name only comes from c2, so it stays hidden.
        self.assertEqual([], self.post("/review/history", {"corpus_ids": ["c1"]})["history"])

    def test_decisions_and_undo_survive_a_restart(self):
        self.post("/review/decide", self.pair(decision="merge"))
        again = IngestWorker(self.state, FakeLLM(), review_path=self.review_path)
        self.state.worker = again
        self.assertTrue(self.post("/review/history", SCOPE)["history"][0]["undoable"])
        self.post("/review/undo", self.pair())
        third = IngestWorker(self.state, FakeLLM(), review_path=self.review_path)
        self.assertEqual(1, len(third.open_review()))

    def test_a_merge_from_before_undo_existed_is_not_undoable(self):
        a, b = self.item["a"], self.item["b"]
        self.post("/review/decide", self.pair(decision="merge"))
        del self.state.worker.decisions[(a, b)]["record"]
        [entry] = self.post("/review/history", SCOPE)["history"]
        self.assertFalse(entry["undoable"])
        with self.assertRaises(service.HttpError) as ctx:
            self.post("/review/undo", self.pair())
        self.assertEqual(409, ctx.exception.status)


class SharedDocumentUndoTest(unittest.TestCase):
    """A document naming both people moves back only what the merge record lists,
    and a relation between the two (deleted by the merge) comes back."""

    def make_store(self):
        return MemoryGraphStore()

    def setUp(self):
        store = self.make_store()
        self.store = store
        p1 = Entity("person:p1", "person", label="Kiss Anna")
        place = Entity("place:x", "place", label="X")
        store.put_document(Document("d1", "c1", "/c1/d1.txt"), [p1, place],
                           [Mention("m1", "d1", "person:p1", "Kiss Anna")],
                           [Relation("r1", "PLACED_IN", "person:p1", "place:x",
                                     evidence=[Evidence("d1", "q1")])])
        p2 = Entity("person:p2", "person", label="Kiss Anna",
                    attributes=[AttributeValue("birth_date", "1990-01-01", "d2")])
        store.put_document(Document("d2", "c1", "/c1/d2.txt"),
                           [p2, Entity("person:p1", "person"), Entity("place:x", "place")],
                           [Mention("m2", "d2", "person:p2", "Kiss Anna"),
                            Mention("m3", "d2", "person:p1", "Kiss A.")],
                           [Relation("r2", "MET", "person:p2", "person:p1",
                                     evidence=[Evidence("d2", "q2")]),
                            Relation("r3", "PLACED_IN", "person:p2", "place:x",
                                     evidence=[Evidence("d2", "q3")])])
        self.state = service.State(store, domain_profile.load("bv"))
        self.worker = IngestWorker(self.state, FakeLLM())

    def test_undo_restores_the_graph(self):
        before = snapshot(self.store)
        self.worker.decide("person:p2", "person:p1", True)
        self.assertEqual(0, sum(r.type == "MET"
                                for r in getattr(self.store, "mirror", self.store).relations.values()))
        out = self.worker.undo("person:p2", "person:p1")
        self.assertEqual((1, []), (out["documents"], out["failed"]))
        self.assertEqual(before, snapshot(self.store))
        # The resolver knows both people again.
        self.assertEqual({"person:p1", "person:p2"},
                         {p for p in self.worker.resolver.people if p.startswith("person:p")})

    def test_a_later_merge_into_the_survivor_blocks_nothing_but_keeps_its_documents(self):
        p3 = Entity("person:p3", "person", label="Kiss Anna")
        self.store.put_document(Document("d3", "c1", "/c1/d3.txt"), [p3],
                                [Mention("m4", "d3", "person:p3", "Kiss Anna")], [])
        self.worker.decide("person:p2", "person:p1", True)
        self.worker.decide("person:p3", "person:p1", True)
        self.worker.undo("person:p2", "person:p1")
        mentions = getattr(self.store, "mirror", self.store).mentions
        self.assertEqual("person:p1", mentions["m4"].entity_id)
        self.assertEqual("person:p2", mentions["m2"].entity_id)

    def test_undo_needs_the_survivor_to_be_active(self):
        p3 = Entity("person:p3", "person", label="Kiss Anna")
        self.store.put_document(Document("d3", "c1", "/c1/d3.txt"), [p3],
                                [Mention("m4", "d3", "person:p3", "Kiss Anna")], [])
        self.worker.decide("person:p2", "person:p1", True)
        self.worker.decide("person:p1", "person:p3", True)
        # p2's survivor is now p3: undoing the first merge still works (chain).
        self.worker.undo("person:p2", "person:p1")
        mentions = getattr(self.store, "mirror", self.store).mentions
        self.assertEqual("person:p2", mentions["m2"].entity_id)
        self.assertEqual("person:p3", mentions["m1"].entity_id)


if __name__ == "__main__":
    unittest.main()
