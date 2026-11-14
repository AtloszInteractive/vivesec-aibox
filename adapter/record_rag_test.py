"""The RAG copy of structured records (E29): record_rag.RecordRag against a fake
graph and a fake RAG -- what is written, kept, dropped, and with which rule."""
import threading
import unittest

import record_rag

FOLDER = "/storage/drives/bv/export"
ROOT = FOLDER + "/~records"
FLAT = {"restricted": False, "allow": [], "deny": [], "classification": 0}


def record(n, tag="t", require=("group:bv-intezet-01",)):
    return {"path": "%s/intel_log/Log %d (abc%d)" % (ROOT, n, n), "tag": "%s%d" % (tag, n),
            "title": "Log %d" % n, "text": "Információ: %d" % n,
            "require": list(require) if require is not None else None,
            "require_classification": 0}


class Fakes:
    def __init__(self, graph, indexed):
        self.graph = {r["path"]: r for r in graph}
        self.indexed = dict(indexed)
        self.calls = []
        self.flat = dict(FLAT)
        self.pushed = []
        self.fail = 0

    def graph_post(self, path, body):
        if self.fail:
            self.fail -= 1
            raise RuntimeError("graph down")
        if path == "/import/rag/list":
            items = sorted((p, r["tag"]) for p, r in self.graph.items() if p > body["after"])
            return {"documents": [{"path": p, "tag": t} for p, t in items], "next": None}
        assert path == "/import/rag/get" and len(body["paths"]) <= record_rag.BATCH
        return {"documents": [self.graph[p] for p in body["paths"] if p in self.graph]}

    def rag_post(self, path, body):
        self.calls.append((path, body))
        if path == "/index/records":
            assert body["prefix"] == ROOT
            return {"documents": [{"path": p, "tag": t} for p, t in sorted(self.indexed.items())
                                  if p > body["after"]], "next": None}
        if path == "/index/drop/tree":
            self.indexed.pop(body["path"], None)
        elif path == "/ingest":
            self.indexed[body["path"]] = body["metadata"]["tag"]
        return {}

    def sync(self, **kw):
        return record_rag.RecordRag(self.graph_post, self.rag_post, lambda f: dict(self.flat),
                                    lambda f: "c1", "default", self.pushed.append, **kw)


class RecordRagTest(unittest.TestCase):
    def test_writes_the_changed_drops_the_stale_and_keeps_the_rest(self):
        fakes = Fakes([record(1), record(2), record(3, require=None)],
                      {record(1)["path"]: "t1", record(2)["path"]: "old",
                       ROOT + "/intel_log/gone (x)": "t9"})
        out = fakes.sync().sync(FOLDER)
        self.assertEqual({"records": 3, "written": 2, "removed": 1, "unchanged": 1}, out)
        ingests = {b["path"]: b for p, b in fakes.calls if p == "/ingest"}
        self.assertEqual({record(2)["path"], record(3)["path"]}, set(ingests))
        acl = ingests[record(2)["path"]]["metadata"]["acl"]
        self.assertEqual((["group:bv-intezet-01"], 0, FOLDER),
                         (acl["require"], acl["require_classification"], acl["follows"]))
        self.assertNotIn("require", ingests[record(3)["path"]]["metadata"]["acl"])
        self.assertEqual(ingests[record(2)["path"]]["text"], "Információ: 2")
        # Aligned now: a second run writes and drops nothing.
        fakes.calls.clear()
        self.assertEqual({"records": 3, "written": 0, "removed": 0, "unchanged": 3},
                         fakes.sync().sync(FOLDER))
        self.assertEqual(["/index/records"], [p for p, _ in fakes.calls])

    def test_an_empty_rule_is_kept_and_nothing_outside_the_folder_is_written(self):
        stray = dict(record(1), path="/storage/drives/other/~records/x/y (1)")
        fakes = Fakes([record(2, require=[])], {})
        fakes.graph[record(1)["path"]] = stray  # the graph answers a path it was not asked
        fakes.sync().sync(FOLDER)
        written = [b["path"] for p, b in fakes.calls if p == "/ingest"]
        self.assertEqual([record(2)["path"]], written)
        self.assertEqual([], [b for p, b in fakes.calls if p == "/ingest"][0]["metadata"]["acl"]
                         ["require"])

    def test_a_folder_rule_changed_meanwhile_is_pushed_again(self):
        fakes = Fakes([record(1)], {})
        real = fakes.rag_post

        def changing(path, body):
            if path == "/ingest":
                fakes.flat = dict(FLAT, restricted=True, allow=["group:hr"])
            return real(path, body)
        fakes.rag_post = changing
        fakes.sync().sync(FOLDER)
        self.assertEqual([FOLDER], fakes.pushed)

    def test_the_worker_retries_and_reports(self):
        fakes = Fakes([record(1)], {})
        fakes.fail = 1
        sync = fakes.sync(retries=(0.01,))
        sync.request(FOLDER + "/")
        state = sync.wait(FOLDER, 10)
        self.assertEqual(("done", 1), (state["state"], state["written"]))
        fakes.fail = 5
        sync.request(FOLDER)
        state = sync.wait(FOLDER, 10)
        self.assertEqual(("failed", 2), (state["state"], state["tries"]))
        self.assertIn("graph down", state["error"])
        self.assertEqual({"state": "none"}, sync.status("/elsewhere"))


if __name__ == "__main__":
    unittest.main()
