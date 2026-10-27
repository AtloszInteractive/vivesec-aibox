"""Background extraction, the review list and their scope rules (scripted model)."""
import base64
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import domain_profile  # noqa: E402
import ingest_jobs  # noqa: E402
import service  # noqa: E402
from graph_store import MemoryGraphStore  # noqa: E402
from ingest_jobs import IngestWorker  # noqa: E402

NOTE_1 = "Kovács János (szül. 1980. 01. 02.) a 102. zárkában van elhelyezve."
NOTE_2 = "Kovács János és Nagy Éva 2026. 03. 12-én találkozott."


class FakeLLM:
    calls = 0
    seconds = 0.0
    # A model names its local references freely: a re-extraction may use others.
    ref = ""

    def json_call(self, system, user, schema, max_tokens=4096, retries=1):
        self.calls += 1
        r = self.ref
        if "zárkában" in user:
            return {"entities": [
                {"ref": r + "a", "type": "person", "names": ["Kovács János"],
                 "birth_date": "1980-01-02"},
                {"ref": r + "c", "type": "place", "subtype": "cell", "names": ["102. zárkában"]}],
                "relations": [{"type": "PLACED_IN", "from": r + "a", "to": r + "c",
                               "quote": NOTE_1}]}
        return {"entities": [{"ref": r + "a", "type": "person", "names": ["Kovács János"]},
                             {"ref": r + "b", "type": "person", "names": ["Nagy Éva"]}],
                "relations": [{"type": "MET", "from": r + "a", "to": r + "b", "quote": NOTE_2,
                               "date": "2026-03-12"}]}


def doc(doc_id, corpus, text):
    return {"doc_id": doc_id, "corpus_id": corpus, "source_path": "/%s/%s.txt" % (corpus, doc_id),
            "content_b64": base64.b64encode(text.encode("utf-8")).decode()}


class IngestWorkerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.review_path = os.path.join(self.tmp.name, "review.jsonl")
        self.state = service.State(MemoryGraphStore(), domain_profile.load("bv"))
        self.state.worker = IngestWorker(self.state, FakeLLM(), review_path=self.review_path)

    def tearDown(self):
        self.tmp.cleanup()

    def post(self, path, body):
        return service.handle_post(self.state, path, body)

    def ingest_both(self):
        self.post("/ingest/document", doc("d1", "c1", NOTE_1))
        self.post("/ingest/document", doc("d2", "c2", NOTE_2))
        self.assertTrue(self.state.worker.wait_idle())

    def test_documents_are_extracted_and_stored(self):
        self.ingest_both()
        status = self.post("/ingest/status", {})
        self.assertEqual({"done": 2}, status["totals"])
        self.assertEqual(["done", "done"], [r["status"] for r in status["recent"]])
        found = self.post("/query/find", {"corpus_ids": ["c1", "c2"], "text": "Nagy"})
        self.assertEqual(1, len(found["entities"]))

    def test_name_only_match_waits_for_review_then_merges(self):
        self.ingest_both()
        scope = {"corpus_ids": ["c1", "c2"]}
        [item] = self.post("/review/list", scope)["review"]
        self.assertEqual("same name", item["reason"])
        self.assertEqual("Kovács János", item["a_label"])
        self.post("/review/decide", dict(scope, a=item["a"], b=item["b"], decision="merge"))
        self.assertEqual([], self.post("/review/list", scope)["review"])
        merged = self.state.store.entities[item["a"]]
        self.assertEqual(("merged", item["b"]), (merged.status, merged.merged_into))

    def test_review_is_scoped(self):
        self.ingest_both()
        self.assertEqual([], self.post("/review/list", {"corpus_ids": ["c2"]})["review"])
        item = self.state.worker.resolver.review[0]
        with self.assertRaises(service.HttpError) as ctx:
            self.post("/review/decide", {"corpus_ids": ["c2"], "a": item["a"], "b": item["b"],
                                         "decision": "merge"})
        self.assertEqual(404, ctx.exception.status)

    def test_review_survives_a_restart(self):
        self.ingest_both()
        again = IngestWorker(self.state, FakeLLM(), review_path=self.review_path)
        self.assertEqual(1, len(again.open_review()))
        item = again.open_review()[0]
        again.decide(item["a"], item["b"], False)
        third = IngestWorker(self.state, FakeLLM(), review_path=self.review_path)
        self.assertEqual([], third.open_review())

    def test_full_queue_answers_429_and_bad_base64_400(self):
        worker = self.state.worker
        worker.max_queue_bytes = 10
        with worker.cond:  # hold the worker so the queue cannot drain
            worker.queue.append(({"doc_id": "x"}, b"12345678", 0))
            worker.queued_bytes = 8
            with self.assertRaises(service.HttpError) as ctx:
                self.post("/ingest/document", doc("d3", "c1", NOTE_1))
            self.assertEqual(429, ctx.exception.status)
            worker.queue.clear()
            worker.queued_bytes = 0
        body = doc("d4", "c1", NOTE_1)
        body["content_b64"] = "not base64!"
        with self.assertRaises(service.HttpError) as ctx:
            self.post("/ingest/document", body)
        self.assertEqual(400, ctx.exception.status)

    def test_sync_by_path_new_version_acl_and_delete(self):
        self.ingest_both()
        store = self.state.store
        newer = doc("d1-v2", "c1", NOTE_1)
        newer["source_path"] = "/c1/d1.txt"
        self.post("/ingest/document", newer)
        self.assertTrue(self.state.worker.wait_idle())
        self.assertEqual(["d1-v2"], store.doc_ids_by_path("c1", "/c1/d1.txt"))
        self.assertNotIn("d1", store.documents)
        acl = {"restricted": True, "allow": ["user:u1"], "deny": [], "classification": 0}
        out = self.post("/graph/acl/update", {"corpus_id": "c1",
                                              "entries": [{"path": "/c1/d1.txt", "acl": acl}]})
        self.assertEqual(1, out["updated"])
        self.assertIsNotNone(store.documents["d1-v2"].acl)
        out = self.post("/graph/document/drop_tree", {"corpus_id": "c1", "path": "/c1"})
        self.assertEqual(1, out["dropped"])
        self.assertEqual(["d2"], sorted(store.documents))

    def test_the_same_file_again_is_not_extracted_again(self):
        self.ingest_both()
        llm = self.state.worker.llm
        calls = llm.calls
        self.assertTrue(self.state.store.documents["d1"].content_sha256)
        out = self.post("/ingest/document", doc("d1", "c1", NOTE_1))
        self.assertEqual({"queued": 0, "unchanged": True}, out)
        self.assertEqual(calls, llm.calls)
        status = self.post("/ingest/status", {})
        self.assertEqual(1, status["totals"]["unchanged"])
        self.assertEqual("unchanged", status["recent"][-1]["status"])
        # An ACL sent with it is applied without extraction.
        acl = {"restricted": True, "allow": ["user:u1"], "deny": [], "classification": 0}
        out = self.post("/ingest/document", dict(doc("d1", "c1", NOTE_1), acl=acl))
        self.assertTrue(out["unchanged"])
        self.assertIsNotNone(self.state.store.documents["d1"].acl)
        self.post("/ingest/document", doc("d1", "c1", NOTE_1))
        self.assertIsNone(self.state.store.documents["d1"].acl)
        self.assertEqual(calls, llm.calls)

    def test_changed_content_profile_or_a_waiting_version_is_extracted(self):
        self.ingest_both()
        worker = self.state.worker
        self.assertFalse(self.post("/ingest/document",
                                   doc("d1", "c1", NOTE_1 + " ")).get("unchanged"))
        self.assertTrue(worker.wait_idle())
        newer = dict(self.state.profile, version=self.state.profile["version"] + 1)
        self.post("/profile/set", {"profile": newer})
        self.assertFalse(self.post("/ingest/document",
                                   doc("d1", "c1", NOTE_1 + " ")).get("unchanged"))
        self.assertTrue(worker.wait_idle())
        # The stored copy matches, but a different version is still waiting: the
        # one sent last must win, so it queues behind it.
        with worker.cond:
            worker.queue.append(({"doc_id": "d1", "corpus_id": "c1",
                                  "source_path": "/c1/d1.txt"}, b"other", 0))
            self.assertFalse(worker.unchanged(
                {"doc_id": "d1", "corpus_id": "c1", "source_path": "/c1/d1.txt"},
                (NOTE_1 + " ").encode("utf-8")))
            worker.queue.clear()
        self.assertTrue(worker.unchanged(
            {"doc_id": "d1", "corpus_id": "c1", "source_path": "/c1/d1.txt"},
            (NOTE_1 + " ").encode("utf-8")))

    def test_a_long_queue_leaves_the_prose_to_reprocessing(self):
        worker = self.state.worker
        meta = {"doc_id": "d1", "corpus_id": "c1", "source_path": "/c1/d1.txt"}
        with mock.patch.object(ingest_jobs, "DEFER_PROSE_QUEUE", 1):
            with worker.cond:  # one document waiting behind this one
                worker.queue.append(({"doc_id": "x"}, b"", 0))
            try:
                out = worker._process(meta, NOTE_1.encode("utf-8"))
            finally:
                with worker.cond:
                    worker.queue.clear()
        self.assertEqual(("deferred", 1), (out["status"], out["stats"]["prose_deferred"]))
        self.assertEqual(0, worker.llm.calls)
        current = domain_profile.profile_tag(self.state.profile)
        self.assertEqual(current + "/partial", self.state.store.documents["d1"].profile)
        listed = self.post("/documents/profile", {"corpus_ids": ["c1"]})
        self.assertEqual((1, 1, ["d1"]), (listed["stale_total"], listed["partial_total"],
                                          [d["doc_id"] for d in listed["stale"]]))
        # Fetched again (reprocessing): the same bytes are read in full this time.
        self.assertFalse(self.post("/ingest/document", doc("d1", "c1", NOTE_1)).get("unchanged"))
        self.assertTrue(worker.wait_idle())
        self.assertEqual(1, worker.llm.calls)
        self.assertEqual(current, self.state.store.documents["d1"].profile)
        self.assertEqual(0, self.post("/documents/profile", {"corpus_ids": ["c1"]})["stale_total"])

    def test_entities_named_in_a_question(self):
        self.ingest_both()
        out = self.post("/query/mentioned", {"corpus_ids": ["c2"],
                                             "text": "Mikor találkozott Kovács Jánossal Nagy Éva?"})
        self.assertEqual(["Kovács János", "Nagy Éva"], [e["label"] for e in out["entities"]])
        self.assertEqual(["/c2/d2.txt"],
                         [d["source_path"] for d in out["documents"].values()])
        out = self.post("/query/mentioned", {"corpus_ids": ["c2"], "text": "Kovácsné jött"})
        self.assertEqual([], out["entities"])

    def reprocess_d2(self):
        # A byte more (same text): a new version, so it is extracted again.
        self.state.worker.llm.ref = "x"
        self.post("/ingest/document", doc("d2", "c2", NOTE_2 + "\n"))
        self.assertTrue(self.state.worker.wait_idle())

    def test_a_profile_change_makes_documents_stale_until_reprocessed(self):
        self.ingest_both()
        scope = {"corpus_ids": ["c1", "c2"]}
        old = domain_profile.profile_tag(self.state.profile)
        out = self.post("/documents/profile", scope)
        self.assertEqual((old, {old: 2}, 0), (out["current"], out["counts"], out["stale_total"]))
        newer = dict(self.state.profile, version=self.state.profile["version"] + 1)
        self.post("/profile/set", {"profile": newer})
        out = self.post("/documents/profile", scope)
        self.assertEqual(["/c1/d1.txt", "/c2/d2.txt"], [d["source_path"] for d in out["stale"]])
        self.assertEqual(["/c2/d2.txt"], [d["source_path"] for d in self.post(
            "/documents/profile", dict(scope, path_prefix="/c2"))["stale"]])
        self.assertEqual([], self.post("/documents/profile", {"corpus_ids": []})["stale"])
        self.assertNotIn("paths", out)
        self.assertEqual([["c1", "/c1/d1.txt"], ["c2", "/c2/d2.txt"]],
                         self.post("/documents/profile", dict(scope, with_paths=True))["paths"])
        self.assertEqual([["c2", "/c2/d2.txt"]], self.post(
            "/documents/profile", {"corpus_ids": ["c2"], "with_paths": True})["paths"])
        self.reprocess_d2()
        out = self.post("/documents/profile", scope)
        self.assertEqual({old: 1, domain_profile.profile_tag(newer): 1}, out["counts"])
        self.assertEqual(["/c1/d1.txt"], [d["source_path"] for d in out["stale"]])

    def test_reprocessing_keeps_a_keep_apart_decision(self):
        self.ingest_both()
        scope = {"corpus_ids": ["c1", "c2"]}
        [item] = self.post("/review/list", scope)["review"]
        self.post("/review/decide", dict(scope, a=item["a"], b=item["b"], decision="separate"))
        people = sorted(e.id for e in self.state.store.entities.values() if e.type == "person")
        self.reprocess_d2()
        self.assertEqual([], self.post("/review/list", scope)["review"])
        self.assertEqual(people, sorted(e.id for e in self.state.store.entities.values()
                                        if e.type == "person"))

    def test_reprocessing_keeps_a_merge(self):
        self.ingest_both()
        scope = {"corpus_ids": ["c1", "c2"]}
        [item] = self.post("/review/list", scope)["review"]
        self.post("/review/decide", dict(scope, a=item["a"], b=item["b"], decision="merge"))
        self.reprocess_d2()
        self.assertEqual([], self.post("/review/list", scope)["review"])
        found = self.post("/query/find", dict(scope, text="Kovács"))["entities"]
        self.assertEqual([item["b"]], [e["id"] for e in found])

    def test_disabled_extraction_answers_503(self):
        self.state.worker = None
        with self.assertRaises(service.HttpError) as ctx:
            self.post("/ingest/status", {})
        self.assertEqual(503, ctx.exception.status)


if __name__ == "__main__":
    unittest.main()
