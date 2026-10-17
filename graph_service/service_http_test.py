import json
import os
import sys
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import domain_profile  # noqa: E402
import service  # noqa: E402
from graph_store import MemoryGraphStore  # noqa: E402

DOC = {
    "document": {"doc_id": "d1", "corpus_id": "jelentesek-1", "source_path": "/s/d1.pdf"},
    "entities": [{"id": "p1", "type": "person", "subtype": "inmate", "label": "Kovács János"},
                 {"id": "p2", "type": "person", "label": "Szabó Éva"},
                 {"id": "ph", "type": "identifier", "subtype": "phone", "label": "+36301234567"}],
    "mentions": [{"id": "m1", "entity_id": "p1", "text": "Kovács János", "page": 1},
                 {"id": "m2", "entity_id": "p2", "text": "Szabó Éva", "page": 1},
                 {"id": "m3", "entity_id": "ph", "text": "+36 30 123 4567", "page": 1}],
    "relations": [{"id": "r1", "type": "VISITED", "a": "p2", "b": "p1", "start": "2026-03-01",
                   "end": "2026-03-01",
                   "evidence": [{"quote": "Szabó Éva meglátogatta Kovács Jánost", "page": 1}]},
                  {"id": "r2", "type": "USES", "a": "p2", "b": "ph",
                   "evidence": [{"quote": "+36 30 123 4567 (Szabó Éva)", "page": 1,
                                 "method": "rule"}]}],
}
SCOPE = {"corpus_ids": ["jelentesek-1"]}


class ServiceHttpTest(unittest.TestCase):
    def setUp(self):
        self.state = service.State(MemoryGraphStore(), domain_profile.load("bv"))
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), service.make_handler(self.state))
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.base = "http://127.0.0.1:%d" % self.httpd.server_address[1]

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    def post(self, path, body, headers=None):
        req = urllib.request.Request(self.base + path, data=json.dumps(body).encode("utf-8"),
                                     method="POST", headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_put_then_query_within_scope(self):
        self.assertEqual(200, self.post("/graph/document/put", DOC)[0])
        status, body = self.post("/query/find", dict(SCOPE, text="kovacs janos"))
        self.assertEqual(200, status)
        self.assertEqual(["p1"], [e["id"] for e in body["entities"]])
        status, body = self.post("/query/path", dict(SCOPE, a="p1", b="ph"))
        self.assertEqual(2, body["path"]["hops"])
        status, body = self.post("/query/timeline", dict(SCOPE, id="p1"))
        self.assertEqual(["VISITED"], [e["type"] for e in body["timeline"]])

    def test_query_without_scope_fails_closed(self):
        self.post("/graph/document/put", DOC)
        self.assertEqual(400, self.post("/query/find", {"text": "Kovács"})[0])
        status, body = self.post("/query/find", {"corpus_ids": [], "text": "Kovács"})
        self.assertEqual((200, []), (status, body["entities"]))

    def test_relation_outside_profile_is_rejected(self):
        bad = json.loads(json.dumps(DOC))
        bad["relations"][0]["type"] = "BRIBED"
        status, body = self.post("/graph/document/put", bad)
        self.assertEqual(400, status)
        self.assertIn("RELATED_TO", body["error"])

    def test_relation_endpoint_types_are_checked(self):
        bad = json.loads(json.dumps(DOC))
        bad["relations"][1]["type"] = "VISITED"
        self.assertEqual(400, self.post("/graph/document/put", bad)[0])

    def test_profile_set_needs_a_higher_version(self):
        profile = domain_profile.load("bv")
        self.assertEqual(409, self.post("/profile/set", {"profile": profile})[0])
        profile["version"] = 2
        status, body = self.post("/profile/set", {"profile": profile})
        self.assertEqual((200, True), (status, body["reprocess_required"]))
        self.assertEqual(2, self.state.profile["version"])

    def test_api_key_is_enforced_when_set(self):
        old = service.API_KEY
        service.API_KEY = "secret"
        try:
            self.assertEqual(401, self.post("/profile/get", {})[0])
            self.assertEqual(200, self.post("/profile/get", {}, {"X-API-Key": "secret"})[0])
        finally:
            service.API_KEY = old


if __name__ == "__main__":
    unittest.main()
