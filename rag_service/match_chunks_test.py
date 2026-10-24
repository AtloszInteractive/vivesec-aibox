"""Full coverage: match_chunks returns every visible chunk that matches, in
reading order, page by page -- the same in the JSON and the SQLite store.

    VIVESEC_BACKEND=fallback python -m unittest match_chunks_test
"""
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest.mock import patch

os.environ.setdefault("VIVESEC_BACKEND", "fallback")
sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "poc"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import retrieval  # noqa: E402
import store  # noqa: E402

REPORTS = "reports-1111"
OTHER = "other-2222"
ROOT = "/storage/drives/reports"
HR_ONLY = {"restricted": True, "allow": ["group:hr"], "deny": [], "classification": 1}
LARS = {"principals": ["everyone", "user:lars"], "clearance": 1, "membership_known": True,
        "default_classification": 1}


class RuleTest(unittest.TestCase):
    def test_words_match_by_stem_and_close_together(self):
        words = retrieval.tokens("Bogdán Balázst a 102. zárkában látogatásai után kihallgatták.")
        self.assertTrue(retrieval.chunk_matches(words, ["Bogdán Balázs"]))
        self.assertTrue(retrieval.chunk_matches(words, ["látogatás"]))
        self.assertFalse(retrieval.chunk_matches(words, ["Balázs Bogdánné Kiss"]))
        # Short words match whole only.
        self.assertFalse(retrieval.chunk_matches(retrieval.tokens("kisebb ügy"), ["Kis"]))
        self.assertTrue(retrieval.chunk_matches(retrieval.tokens("Kis Pál"), ["Kis"]))
        far = retrieval.tokens("Bogdán ma reggel a folyosón Balázs")
        self.assertFalse(retrieval.chunk_matches(far, ["Bogdán Balázs"]))
        self.assertTrue(retrieval.chunk_matches(words, ["Nagy", "zárka"], "any"))
        self.assertFalse(retrieval.chunk_matches(words, ["Nagy", "zárka"], "all"))

    def test_fts_expression(self):
        self.assertEqual('text : ("nagy"* OR NEAR("bogdan"* "balazs"*, 2))',
                         retrieval.fts_query(["Nagy", "Bogdán Balázs"]))
        self.assertEqual("", retrieval.fts_query(["!", " "]))


class JsonStoreTest(unittest.TestCase):
    def make_store(self):
        return store.RagStore()

    def setUp(self):
        self.store = self.make_store()
        self.index(REPORTS, ROOT + "/b/visits.txt", [
            "Fazekas Péter meglátogatta Bogdán Balázst.",
            "Szabó Vera nem jelent meg.",
            "Bogdán Balázzsal egy órát beszélt."])
        self.index(REPORTS, ROOT + "/a/incident.txt", ["Bogdán Balázs a vitát nem látta."])
        self.index(REPORTS, ROOT + "/hr/secret.txt", ["Bogdán Balázs fegyelmi ügye."], HR_ONLY)
        self.index(OTHER, "/storage/drives/other/x.txt", ["Bogdán Balázs máshol."])

    def index(self, corpus_id, path, pages, acl=None):
        self.store._index_document(
            corpus_id=corpus_id, tenant_id="default", source_path=path,
            title=path.rsplit("/", 1)[-1], pages=[(i, "root", t) for i, t in enumerate(pages, 1)],
            mtime=None, size=None, acl=acl)

    def match(self, terms, **kw):
        kw.setdefault("access", LARS)
        return self.store.match_chunks([REPORTS], terms, **kw)

    def test_every_visible_match_in_reading_order(self):
        chunks, counts = self.match(["Bogdán Balázs"])
        self.assertEqual([(ROOT + "/a/incident.txt", 1), (ROOT + "/b/visits.txt", 1)],
                         [(c["source_path"], c["page_number"]) for c in chunks])
        self.assertEqual({"total": 2, "documents": 2}, counts)
        self.assertTrue(all("fegyelmi" not in c["text"] for c in chunks))
        # The stem finds the inflected form on page 3.
        chunks, counts = self.match(["Bogdán Baláz"])
        self.assertEqual(3, counts["total"])
        _, counts = self.match(["Bogdán Balázs"], access={**LARS, "principals": ["group:hr"]})
        self.assertEqual(3, counts["total"])

    def test_pages_folders_and_scope(self):
        first, counts = self.match(["Bogdán Baláz"], limit=2)
        rest, _ = self.match(["Bogdán Baláz"], limit=2, offset=2)
        self.assertEqual(3, len(first + rest))
        self.assertEqual(len({c["chunk_id"] for c in first + rest}), 3)
        chunks, counts = self.match(["Bogdán Baláz"], path_prefixes=[ROOT + "/b"])
        self.assertEqual({ROOT + "/b/visits.txt"}, {c["source_path"] for c in chunks})
        self.assertEqual(0, self.match(["Bogdán Baláz"], path_prefixes=[ROOT + "/b/vis"])[1]["total"])
        self.assertEqual(([], {"total": 0, "documents": 0}),
                         self.store.match_chunks([], ["Bogdán Balázs"], access=LARS))


class SqliteStoreTest(JsonStoreTest):
    def make_store(self):
        import sqlite_store
        result = sqlite_store.SqliteVecStore()
        self.addCleanup(result._conn.close)
        return result


class RouteTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        with patch.dict(os.environ, {"RAG_STORE_BACKEND": "json",
                                     "RAG_INDEX_PATH": os.path.join(cls.temp.name, "index.json"),
                                     "RAG_API_KEY": ""}):
            import service
        cls.service = service

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def setUp(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self.service.Handler)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.service.STORE._index_document(
            corpus_id=REPORTS, tenant_id="default", source_path=ROOT + "/v.txt", title="v",
            pages=[(1, "root", "Bogdán Balázs látogatója Fazekas Péter.")],
            mtime=None, size=None, acl=None)

    def post(self, body):
        req = urllib.request.Request(
            "http://127.0.0.1:%d/rag/match_chunks" % self.server.server_port,
            data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.load(r)
        except urllib.error.HTTPError as e:
            return e.code, json.load(e)

    def test_route(self):
        status, out = self.post({"corpus_ids": [REPORTS], "terms": ["Fazekas Péter"], "limit": 1})
        self.assertEqual((200, 1, None), (status, out["total"], out["next_offset"]))
        self.assertEqual(ROOT + "/v.txt", out["chunks"][0]["source_path"])
        status, out = self.post({"corpus_ids": [], "terms": ["Fazekas"]})
        self.assertEqual((200, 0), (status, out["total"]))
        for bad in ({"terms": []}, {"terms": ["!"]}, {"terms": ["x"], "mode": "some"},
                    {"terms": ["Fazekas"], "limit": 501}, {"terms": ["Fazekas"], "offset": -1},
                    {"terms": "Fazekas"}, {"terms": ["Fazekas"], "path_prefixes": "x"}):
            status, _ = self.post(dict({"corpus_ids": [REPORTS]}, **bad))
            self.assertEqual(400, status, bad)


if __name__ == "__main__":
    unittest.main()
