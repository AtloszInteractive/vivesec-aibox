"""Reprocessing after a profile change (reprocess.py) with a fake graph,
forwarder and file source.

  Push-Location adapter; python -m unittest reprocess_test; Pop-Location
"""
import threading
import unittest

import reprocess


class FakeGraph:
    def __init__(self):
        self.queued = 0

    def post(self, path, body):
        return {"queued": self.queued, "running": None}


class FakeForward:
    def __init__(self):
        self.files = []
        self.pending = 0

    def stats(self):
        return {"pending": self.pending}

    def file(self, path, corpus_id, doc_id, raw):
        if raw == b"too big":
            return False
        self.files.append((path, corpus_id, doc_id, raw))
        return True


def docs(*names):
    return [{"doc_id": "d-" + n, "corpus_id": "c1", "source_path": "/storage/drives/ops/" + n}
            for n in names]


class ReprocessorTest(unittest.TestCase):
    def make(self, fetch):
        self.graph, self.forward = FakeGraph(), FakeForward()
        return reprocess.Reprocessor(self.graph, self.forward, fetch, idle_wait=0.01)

    def test_every_stale_document_is_fetched_and_forwarded(self):
        content = {"a.txt": b"A", "big.txt": b"too big"}

        def fetch(user, path):
            self.assertEqual("hanna", user)
            name = path.rsplit("/", 1)[-1]
            if name == "bad.txt":
                raise RuntimeError("permission")
            return content.get(name)

        runner = self.make(fetch)
        runner.start("hanna", docs("a.txt", "gone.txt", "big.txt", "bad.txt"), "bv@3", "/x")
        self.assertTrue(runner.wait(5))
        status = runner.status()
        self.assertEqual(("done", 4, 4, 1, 1, 2), (status["state"], status["total"],
                                                    status["done"], status["sent"],
                                                    status["missing"], status["failed"]))
        self.assertEqual([("/storage/drives/ops/a.txt", "c1", "d-a.txt", b"A")], self.forward.files)
        self.assertIn("bad.txt", status["error"])
        self.assertEqual(("bv@3", "/x", "hanna"), (status["profile"], status["path_prefix"],
                                                   status["started_by"]))

    def test_it_waits_for_an_idle_graph_and_can_be_cancelled(self):
        runner = self.make(lambda user, path: b"x")
        self.graph.queued = 1  # the graph is busy: nothing is fetched
        runner.start("hanna", docs("a.txt", "b.txt"), "bv@3")
        with self.assertRaises(reprocess.ReprocessError):
            runner.start("hanna", docs("c.txt"), "bv@3")
        self.assertTrue(runner.running())
        self.assertEqual([], self.forward.files)
        runner.cancel()
        self.assertTrue(runner.wait(5))
        self.assertEqual(("cancelled", 0), (runner.status()["state"], runner.status()["done"]))

    def test_a_missing_source_stops_the_run(self):
        def fetch(user, path):
            raise reprocess.ReprocessError("ViVeSecBox not connected")

        runner = self.make(fetch)
        runner.start("hanna", docs("a.txt", "b.txt"), "bv@3")
        self.assertTrue(runner.wait(5))
        status = runner.status()
        self.assertEqual(("failed", 0, "ViVeSecBox not connected"),
                         (status["state"], status["done"], status["error"]))

    def test_the_file_being_read_is_shown_only_to_who_may_see_it(self):
        gate = threading.Event()

        def fetch(user, path):
            gate.wait(5)
            return b"x"

        runner = self.make(fetch)
        runner.start("hanna", docs("secret.txt"), "bv@3")
        for _ in range(100):
            if runner.status().get("current"):
                break
            gate.wait(0.01)
        self.assertEqual("/storage/drives/ops/secret.txt", runner.status()["current"])
        self.assertNotIn("current", runner.status(lambda p: False))
        gate.set()
        self.assertTrue(runner.wait(5))
        self.assertEqual({"state": "idle"}, reprocess.Reprocessor(None, None, None).status())


if __name__ == "__main__":
    unittest.main()
