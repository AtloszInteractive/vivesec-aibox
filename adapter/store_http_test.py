"""E27 S5 over HTTP: the standalone file store -- upload, quarantine, index,
listing, download, folders and delete -- with the identity token, the drive
scope, the writer setting and the ACL rules file. The RAG and clamd are fakes."""
import base64
import http.client
import json
import os
import tempfile
import threading
import unittest
import urllib.parse
from http.server import ThreadingHTTPServer
from unittest.mock import patch

import acl
import directory
import drivestore
import identity
import integration
import lifecycle
from meta import MetaMirror

ROOM = "/storage/drives/cases"
OTHER = "/storage/drives/other"
SECRET = b"s" * 40
NOW = 1_800_000_000


def b64(text):
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii")


class FakeScanner:
    def __init__(self):
        self.down = False

    def scan_file(self, path):
        import clamd
        if self.down:
            raise clamd.ScanUnavailable("down")
        with open(path, "rb") as handle:
            data = handle.read()
        if b"EICAR" in data:
            return "infected", "Eicar-Signature"
        return "clean", None

    def ping(self):
        return not self.down


class FakeRag:
    def __init__(self):
        self.calls = []
        self.contents = {}

    def post_json(self, path, body):
        self.calls.append((path, body))
        if path == "/index/upsert/file/check":
            if body["path"].endswith(".csv") and body["size"] > 1000:
                return {"token": None, "reason": "too_large"}
            return {"token": "tok-" + str(len(self.calls))}
        if path == "/index/drop/tree":
            return {"deleted_documents": 2}
        return {"ok": True}

    def post_raw(self, path, raw):
        self.calls.append((path, len(raw)))
        self.contents[path] = raw
        return {"doc_id": "d1", "chunks": 2}

    def paths(self, kind):
        return [body["path"] for path, body in self.calls if path == kind]


class StoreHttpTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        with patch.dict(os.environ, {
            "ADAPTER_PKI_DIR": cls.temp.name + "/pki",
            "ADAPTER_FILES_DIR": cls.temp.name + "/files",
            "ADAPTER_FEEDBACK_DIR": cls.temp.name + "/feedback",
            "ADAPTER_JOBS_DIR": cls.temp.name + "/jobs",
            "ADAPTER_SESSION_DIR": cls.temp.name + "/sessions",
            "ADAPTER_STORE_DIR": cls.temp.name + "/never-used",
            "ADAPTER_STORAGE_MODE": "off",
            "ADAPTER_META_PATH": "",
        }):
            import service
        cls.service = service

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        secret_file = os.path.join(self.dir.name, "secret")
        with open(secret_file, "wb") as handle:
            handle.write(SECRET)
        self.rules_file = os.path.join(self.dir.name, "acl.json")
        self.write_rules({})
        verifier = identity.Verifier(mode=identity.MODE_REQUIRE, hs256_secret_file=secret_file,
                                     audience="aibox", clock=lambda: NOW)
        self.store = drivestore.DriveStore(os.path.join(self.dir.name, "store"), max_bytes=4096)
        self.scanner = FakeScanner()
        self.rag = FakeRag()
        self.mirror = MetaMirror()
        self.worker = drivestore.UploadWorker(self.store, self.scanner, "required",
                                              self.service._store_index,
                                              self.service._store_unindex)
        for name, value in (
                ("INTEGRATION", integration.MODE_STANDALONE), ("IDENTITY", verifier),
                ("LIFECYCLE", lifecycle.LifecycleStore(os.path.join(self.dir.name, "lc.json"))),
                ("DIRECTORY", directory.TokenDirectory()),
                ("HTTP_SCOPE", self.service.HTTP_SCOPE_FULL),
                ("ACL", acl.Policy(rules_path=self.rules_file)),
                ("MIRROR", self.mirror), ("STORE", self.store), ("UPLOADS", self.worker),
                ("CLAMD", self.scanner),
                ("STORE_WRITERS", drivestore.parse_writers(None)),
                ("rag_post_json", self.rag.post_json), ("rag_post_raw", self.rag.post_raw),
                ("rag_get", lambda *a, **k: {"ok": True, "stats": {}})):
            patcher = patch.object(self.service, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self.service.Handler)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def write_rules(self, rules):
        with open(self.rules_file, "w", encoding="utf-8") as handle:
            json.dump(rules, handle)
        os.utime(self.rules_file, (NOW + len(json.dumps(rules)), NOW + len(json.dumps(rules))))

    # -- helpers ---------------------------------------------------------------
    def headers(self, user="anna", roles=("admin",), drives=(ROOM + "/",), drive=ROOM + "/",
                groups=()):
        token = identity.sign_hs256({"sub": user, "aud": "aibox", "iat": NOW, "exp": NOW + 300,
                                     "roles": list(roles), "groups": list(groups),
                                     "drives": list(drives)}, SECRET)
        return {"VVS-Drive": b64(drive), "VVS-User": user, "VVS-Identity": token,
                "X-Forwarded-Host": "box"}

    def request(self, method, path, body=None, headers=None, chunked=False):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=10)
        try:
            conn.request(method, path, body=body, headers=headers or {}, encode_chunked=chunked)
            response = conn.getresponse()
            raw = response.read()
            return response.status, raw, response
        finally:
            conn.close()

    def call(self, method, path, body=None, **who):
        data = None if body is None else json.dumps(body).encode("utf-8")
        headers = self.headers(**who)
        if data is not None:
            headers["Content-Type"] = "application/json"
        status, raw, _ = self.request(method, path, data, headers)
        return status, (json.loads(raw.decode("utf-8")) if raw else {})

    def upload(self, path, data=b"hello world", overwrite=False, chunked=False, **who):
        headers = self.headers(**who)
        headers["X-Store-Path"] = urllib.parse.quote(path, safe="")
        if overwrite:
            headers["X-Store-Overwrite"] = "1"
        body = iter([data[:5], data[5:]]) if chunked else data
        status, raw, _ = self.request("POST", "/api/v1/ui/store/upload", body, headers, chunked)
        return status, json.loads(raw.decode("utf-8")) if raw else {}

    # -- tests -----------------------------------------------------------------
    def test_vivesecbox_mode_has_no_store(self):
        with patch.object(self.service, "INTEGRATION", integration.MODE_VIVESECBOX):
            status, _ = self.call("GET", "/api/v1/ui/store/info")
            self.assertEqual(status, 404)
            status, _ = self.upload(ROOM + "/a.txt")
            self.assertEqual(status, 404)
        self.assertEqual(self.store.records(), [])

    def test_info(self):
        status, body = self.call("GET", "/api/v1/ui/store/info",
                                 drives=(ROOM + "/", OTHER + "/"))
        self.assertEqual(status, 200)
        self.assertTrue(body["writable"])
        self.assertEqual(body["max_bytes"], 4096)
        self.assertEqual(body["scan"], "required")
        self.assertEqual(body["scanner"], {"ok": True})
        self.assertIn(".pdf", body["extensions"])
        self.assertEqual(body["rooms"], [ROOM, OTHER])
        status, body = self.call("GET", "/api/v1/ui/store/info", user="bob", roles=("reader",))
        self.assertFalse(body["writable"])
        self.assertIsNone(body["scanner"])

    def test_upload_scan_store_index_list_download(self):
        name = ROOM + "/Ügyek/jelentés ő.txt"
        status, body = self.upload(name)
        self.assertEqual(status, 202, body)
        self.assertEqual(body["upload"]["status"], "queued")
        self.assertIsNone(self.store.kind(name))
        self.worker.drain()
        status, body = self.call("GET", "/api/v1/ui/store/uploads")
        self.assertEqual(body["uploads"][0]["status"], "indexed")
        self.assertEqual(body["uploads"][0]["chunks"], 2)
        self.assertNotIn("user", body["uploads"][0])
        self.assertEqual(self.rag.paths("/index/upsert/directory"), [ROOM, ROOM + "/Ügyek"])
        check = [b for p, b in self.rag.calls if p == "/index/upsert/file/check"][0]
        self.assertEqual(check["path"], name)
        self.assertEqual(check["size"], 11)
        self.assertEqual(check["acl"]["restricted"], False)
        self.assertIn(b"hello world", self.rag.contents.values())
        self.assertEqual(self.mirror.get(name)["size"], 11)

        status, body = self.call("POST", "/api/v1/ui/store/list", {"path": ROOM + "/Ügyek"})
        self.assertEqual(status, 200)
        self.assertEqual([(e["name"], e["stored"], e["indexed"]) for e in body["entries"]],
                         [("jelentés ő.txt", True, True)])
        status, raw, response = self.request("POST", "/api/v1/ui/file",
                                             json.dumps({"path": name}).encode("utf-8"),
                                             self.headers())
        self.assertEqual(status, 200)
        self.assertEqual(raw, b"hello world")
        self.assertIn("filename*=UTF-8''jelent%C3%A9s%20%C5%91.txt",
                      response.getheader("Content-Disposition"))
        self.assertEqual(response.getheader("X-Content-Type-Options"), "nosniff")

    def test_chunked_upload(self):
        status, body = self.upload(ROOM + "/c.txt", b"chunked body", chunked=True)
        self.assertEqual(status, 202, body)
        self.worker.drain()
        self.assertEqual(self.store.read_bytes(ROOM + "/c.txt"), b"chunked body")

    def test_refusals(self):
        cases = [
            ({"user": "bob", "roles": ("reader",)}, ROOM + "/a.txt", 403, "not_allowed"),
            ({}, OTHER + "/a.txt", 403, "path outside the request scope"),
            ({}, ROOM + "/../other/a.txt", 400, "invalid_path"),
            ({}, ROOM + "/.hidden.txt", 400, "invalid_path"),
            ({}, ROOM, 400, "invalid_path"),
            ({}, ROOM + "/tool.exe", 415, "type_not_allowed"),
        ]
        for who, path, status, error in cases:
            got, body = self.upload(path, **who)
            self.assertEqual((got, body.get("error")), (status, error), path)
        got, body = self.upload(ROOM + "/big.txt", b"x" * 5000)
        self.assertEqual((got, body.get("error")), (413, "too_large"))
        got, body = self.upload(ROOM + "/big.txt", b"x" * 5000, chunked=True)
        self.assertEqual((got, body.get("error")), (413, "too_large"))
        headers = self.headers(drive=OTHER + "/")
        headers["X-Store-Path"] = urllib.parse.quote(ROOM + "/a.txt")
        got, _, _ = self.request("POST", "/api/v1/ui/store/upload", b"x", headers)
        self.assertEqual(got, 403)
        self.assertEqual(self.store.records(), [])
        self.assertEqual(os.listdir(os.path.join(self.dir.name, "store", "quarantine")), [])

    def test_existing_file_and_pending_upload_need_overwrite(self):
        self.assertEqual(self.upload(ROOM + "/a.txt")[0], 202)
        self.assertEqual(self.upload(ROOM + "/a.txt")[1]["error"], "pending")
        self.worker.drain()
        self.assertEqual(self.upload(ROOM + "/a.txt")[1]["error"], "exists")
        self.assertEqual(self.upload(ROOM + "/a.txt/b.txt")[1]["error"], "parent_is_file")
        status, _ = self.upload(ROOM + "/a.txt", b"new", overwrite=True)
        self.assertEqual(status, 202)
        self.worker.drain()
        self.assertEqual(self.store.read_bytes(ROOM + "/a.txt"), b"new")

    def test_infected_upload(self):
        status, body = self.upload(ROOM + "/eicar.txt", b"X5O EICAR test")
        self.assertEqual(status, 202)
        self.worker.drain()
        status, body = self.call("GET", "/api/v1/ui/store/uploads")
        self.assertEqual((body["uploads"][0]["status"], body["uploads"][0]["signature"]),
                         ("infected", "Eicar-Signature"))
        self.assertIsNone(self.store.kind(ROOM + "/eicar.txt"))
        self.assertEqual(self.rag.calls, [])
        status, body = self.call("GET", "/api/v1/ui/store/uploads", user="carl")
        self.assertEqual(body["uploads"], [])
        upload_id = self.store.records()[0]["id"]
        status, _ = self.call("POST", "/api/v1/ui/store/uploads/dismiss", {"id": upload_id},
                              user="carl")
        self.assertEqual(status, 404)
        status, _ = self.call("POST", "/api/v1/ui/store/uploads/dismiss", {"id": upload_id})
        self.assertEqual(status, 200)
        self.assertEqual(self.store.records(), [])

    def test_scanner_down_keeps_the_upload_in_quarantine(self):
        self.scanner.down = True
        self.upload(ROOM + "/a.txt")
        self.worker.drain()
        status, body = self.call("GET", "/api/v1/ui/store/uploads")
        self.assertEqual(body["uploads"][0]["status"], "waiting_scanner")
        self.assertEqual(self.rag.calls, [])
        status, body = self.call("GET", "/api/v1/ui/store/info")
        self.assertEqual(body["scanner"], {"ok": False})

    def test_not_indexable_file_is_stored(self):
        self.upload(ROOM + "/big.csv", b"a,b\n" * 300)
        self.worker.drain()
        record = self.store.records()[0]
        self.assertEqual((record["status"], record["reason"]), ("stored", "too_large"))
        self.assertEqual(self.rag.paths("/index/upsert/file/content/tok-1"), [])

    def test_mkdir_and_delete(self):
        status, body = self.call("POST", "/api/v1/ui/store/mkdir", {"path": ROOM + "/a/b"})
        self.assertEqual(status, 200)
        self.assertEqual(body["created"], [ROOM, ROOM + "/a", ROOM + "/a/b"])
        self.assertEqual(self.rag.paths("/index/upsert/directory"),
                         [ROOM, ROOM + "/a", ROOM + "/a/b"])
        self.upload(ROOM + "/a/b/x.txt")
        self.worker.drain()
        status, body = self.call("POST", "/api/v1/ui/store/delete", {"path": ROOM + "/a"},
                                 user="bob", roles=("reader",))
        self.assertEqual(status, 403)
        status, body = self.call("POST", "/api/v1/ui/store/delete", {"path": ROOM})
        self.assertEqual((status, body["error"]), (400, "room_root"))
        status, body = self.call("POST", "/api/v1/ui/store/delete", {"path": ROOM + "/a"})
        self.assertEqual(status, 200, body)
        self.assertEqual(self.rag.paths("/index/drop/tree"), [ROOM + "/a"])
        self.assertIsNone(self.store.kind(ROOM + "/a"))
        self.assertEqual(self.mirror.paths_under(ROOM + "/a"), [])
        status, _ = self.call("POST", "/api/v1/ui/store/delete", {"path": ROOM + "/a"})
        self.assertEqual(status, 404)
        with open(os.path.join(self.dir.name, "store", "events.jsonl"), encoding="utf-8") as log:
            events = [json.loads(line)["action"] for line in log]
        self.assertIn("mkdir", events)
        self.assertIn("delete", events)

    def test_acl_rules_hide_and_protect(self):
        self.upload(ROOM + "/open/a.txt")
        self.upload(ROOM + "/secret/b.txt")
        self.worker.drain()
        self.write_rules({"paths": {ROOM + "/secret": {"allow": ["group:investigators"]}}})
        status, body = self.call("POST", "/api/v1/ui/store/list", {"path": ROOM})
        self.assertEqual([e["name"] for e in body["entries"]], ["open"])
        status, _ = self.call("POST", "/api/v1/ui/store/list", {"path": ROOM + "/secret"})
        self.assertEqual(status, 404)
        self.assertEqual(self.upload(ROOM + "/secret/c.txt")[0], 404)
        status, raw, _ = self.request("POST", "/api/v1/ui/file",
                                      json.dumps({"path": ROOM + "/secret/b.txt"}).encode(),
                                      self.headers())
        self.assertEqual(status, 404)
        # A folder whose tree holds something the caller cannot see stays.
        self.write_rules({"paths": {ROOM + "/open/a.txt": {"deny": ["user:anna"]}}})
        status, body = self.call("POST", "/api/v1/ui/store/delete", {"path": ROOM + "/open"})
        self.assertEqual((status, body["error"]), (403, "not_allowed"))
        self.assertEqual(self.store.kind(ROOM + "/open/a.txt"), "file")
        status, body = self.call("POST", "/api/v1/ui/store/list", {"path": ROOM + "/secret"},
                                 groups=("investigators",))
        self.assertEqual([e["name"] for e in body["entries"]], ["b.txt"])

    def test_nfd_spelling_cannot_pass_an_acl_rule(self):
        folder = ROOM + "/Vezet\u0151s\u00e9g"
        self.upload(folder + "/b\u00e9r.txt", b"SECRET")
        self.worker.drain()
        self.write_rules({"paths": {folder: {"deny": ["everyone"]}}})
        nfd = ROOM + "/Vezet\u006f\u030bse\u0301g/be\u0301r.txt"
        for spelling in (folder + "/b\u00e9r.txt", nfd):
            for method, path, body in (
                    ("POST", "/api/v1/ui/file", json.dumps({"path": spelling}).encode("utf-8")),
                    ("GET", "/api/v1/ui/file?" + urllib.parse.urlencode({"path": spelling}), None)):
                status, raw, _ = self.request(method, path, body, self.headers())
                self.assertEqual(status, 404, (method, ascii(spelling)))
                self.assertNotIn(b"SECRET", raw)
        status, _ = self.call("POST", "/api/v1/ui/store/list", {"path": ROOM + "/Vezet\u006f\u030bse\u0301g"})
        self.assertEqual(status, 404)
        status, _ = self.call("POST", "/api/v1/ui/store/delete", {"path": nfd})
        self.assertEqual(status, 404)

    def test_index_only_entries_are_listed(self):
        self.mirror.upsert(ROOM, file=False, mtime=None, size=None)
        self.mirror.upsert(ROOM + "/synced.pdf", file=True, mtime=1, size=42)
        status, body = self.call("POST", "/api/v1/ui/store/list", {"path": ROOM})
        self.assertEqual(status, 200)
        self.assertEqual(body["entries"], [{"path": ROOM + "/synced.pdf", "name": "synced.pdf",
                                            "file": True, "size": 42, "modified": None,
                                            "stored": False, "indexed": True}])
        status, raw, _ = self.request("POST", "/api/v1/ui/file",
                                      json.dumps({"path": ROOM + "/synced.pdf"}).encode(),
                                      self.headers())
        self.assertEqual(status, 404)
        status, body = self.call("POST", "/api/v1/ui/store/delete", {"path": ROOM + "/synced.pdf"})
        self.assertEqual(status, 200)
        self.assertIsNone(self.mirror.get(ROOM + "/synced.pdf"))

    def test_reprocess_fetch_reads_the_store(self):
        self.upload(ROOM + "/r.txt", b"stored text")
        self.worker.drain()
        self.assertEqual(self.service._reprocess_fetch("anna", ROOM + "/r.txt"), b"stored text")
        self.assertIsNone(self.service._reprocess_fetch("anna", ROOM + "/missing.txt"))


if __name__ == "__main__":
    unittest.main()
