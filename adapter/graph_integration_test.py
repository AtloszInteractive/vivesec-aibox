"""E26 end to end: synced files reach the graph, and the graph answers only
what the caller may see.

A real rag_service and a real graph_service (memory store) run as
subprocesses behind the real adapter handler; the extraction model is a tiny
fake that names the people it finds in each excerpt. Every hiding check is
paired with a caller who MAY see the document. This proves the plumbing and
the filtering, not extraction quality.
"""
import base64
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

import acl
import directory
import graph
from meta import MetaMirror

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DRIVE = "/storage/drives/ops"
PUBLIC = DRIVE + "/reports/note.txt"
HIDDEN = DRIVE + "/hr/secret.txt"
DOCS = {
    PUBLIC: "Kovács János és Nagy Éva a Fő utcán találkozott.",
    HIDDEN: "Kovács János és Titkos Tamás a raktárban találkozott.",
}
PEOPLE = ("Kovács János", "Nagy Éva", "Titkos Tamás")
GROUPS = {"lars": [], "hanna": ["hr"]}


def b64(text):
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii")


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class FakeModel(BaseHTTPRequestHandler):
    """Ollama /api/chat stand-in: every known name in the excerpt, MET between the first two."""

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        text = body["messages"][-1]["content"].split("---\n", 1)[-1]
        names = [n for n in PEOPLE if n in text]
        entities = [{"ref": "e%d" % i, "type": "person", "names": [n]}
                    for i, n in enumerate(names)]
        relations = []
        if len(names) >= 2:
            line = next(l for l in text.split("\n") if names[0] in l and names[1] in l)
            relations.append({"type": "MET", "from": "e0", "to": "e1", "quote": line.strip()})
        data = json.dumps({"message": {"content": json.dumps(
            {"entities": entities, "relations": relations})}}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


class FakeDirectory:
    source = "token"

    def lookup(self, identity):
        user = identity.user if identity is not None else ""
        if user not in GROUPS:
            return directory.DirectoryInfo("token", error="directory unreachable")
        return directory.DirectoryInfo("token", GROUPS[user], [])

    def settings(self):
        return {"source": "token"}


def _start(script, env, url):
    proc = subprocess.Popen([sys.executable, os.path.join(REPO, script)], env=env, cwd=REPO,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.time() + 60
    while True:
        try:
            urllib.request.urlopen(url, timeout=2).read()
            return proc
        except Exception:  # noqa: BLE001
            if time.time() > deadline or proc.poll() is not None:
                proc.kill()
                raise RuntimeError("%s did not start" % script)
            time.sleep(0.2)


class GraphIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.model = ThreadingHTTPServer(("127.0.0.1", 0), FakeModel)
        threading.Thread(target=cls.model.serve_forever, daemon=True).start()
        rag_port, graph_port = free_port(), free_port()
        rag_env = dict(os.environ, VIVESEC_BACKEND="fallback", RAG_STORE_BACKEND="sqlite",
                       RAG_PORT=str(rag_port), RAG_HOST="127.0.0.1", RAG_API_KEY="",
                       RAG_INDEX_PATH=os.path.join(cls.temp.name, "index.sqlite"))
        cls.rag_url = "http://127.0.0.1:%d" % rag_port
        cls.rag = _start(os.path.join("rag_service", "service.py"), rag_env,
                         cls.rag_url + "/ready")
        graph_env = dict(os.environ, GRAPH_PORT=str(graph_port), GRAPH_HOST="127.0.0.1",
                         GRAPH_API_KEY="", GRAPH_STORE="memory", GRAPH_PROFILE="bv",
                         GRAPH_OCR="off", GRAPH_INGEST="on",
                         GRAPH_LLM_URL="http://127.0.0.1:%d" % cls.model.server_port)
        cls.graph_url = "http://127.0.0.1:%d" % graph_port
        cls.graph = _start(os.path.join("graph_service", "service.py"), graph_env,
                           cls.graph_url + "/health")
        with patch.dict(os.environ, {
            "ADAPTER_PKI_DIR": cls.temp.name + "/pki",
            "ADAPTER_FILES_DIR": cls.temp.name + "/files",
            "ADAPTER_FEEDBACK_DIR": cls.temp.name + "/feedback",
            "ADAPTER_JOBS_DIR": cls.temp.name + "/jobs",
            "ADAPTER_SESSION_DIR": cls.temp.name + "/sessions",
            "ADAPTER_STORAGE_MODE": "off",
            "ADAPTER_META_PATH": "",
        }):
            import service
        cls.service = service

    @classmethod
    def tearDownClass(cls):
        for proc in (cls.rag, cls.graph):
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
        cls.model.shutdown()
        cls.temp.cleanup()

    def setUp(self):
        service = self.service
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.rules = os.path.join(self.dir.name, "acl.json")
        with open(self.rules, "w", encoding="utf-8") as f:
            json.dump({}, f)
        self.policy = acl.Policy(rules_path=self.rules)
        client = graph.GraphClient(self.graph_url)
        self.mirror = MetaMirror()
        self.forward = graph.Forwarder(client, lambda p: self.policy.effective(
            p, self.mirror.acl_of).as_rag(), 64 << 20, 8 << 20, retry_seconds=0.1)
        patches = {"RAG_URL": self.rag_url, "MIRROR": self.mirror, "ACL": self.policy,
                   "DIRECTORY": FakeDirectory(), "HTTP_SCOPE": service.HTTP_SCOPE_FULL,
                   "ENTITLEMENTS": None, "GRAPH": client, "GRAPH_FORWARD": self.forward,
                   "GRAPH_ADMINS": frozenset({"hanna"}),
                   "_ACL_SYNC": {"fingerprint": None, "generation": 0}}
        for name, value in patches.items():
            patcher = patch.object(service, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), service.Handler)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.call("/api/v1/index/drop/tree", {"path": DRIVE})
        self.sync_corpus()

    # -- helpers -----------------------------------------------------------------
    def call(self, path, body=None, user=None, raw=None, extra=None):
        headers = {"Content-Type": "application/json"}
        if user:
            headers.update({"VVS-Drive": b64(DRIVE + "/"), "VVS-User": user})
        headers.update(extra or {})
        data = raw if raw is not None else json.dumps(body or {}).encode()
        request = urllib.request.Request("http://127.0.0.1:%d%s" % (self.server.server_port,
                                                                     path),
                                         data=data, method="POST", headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode("utf-8"))

    def sync_file(self, path, text):
        body = {"path": path, "size": len(text.encode()), "mtime": 1700000000,
                "head": base64.b64encode(text.encode()[:64]).decode()}
        status, check = self.call("/api/v1/index/upsert/file/check", body)
        self.assertEqual(status, 200, check)
        status, done = self.call("/api/v1/index/upsert/file/content/" + check["token"],
                                 raw=text.encode(), extra={"Content-Type": "text/plain"})
        self.assertEqual(status, 200, done)

    def sync_corpus(self):
        self.call("/api/v1/index/upsert/directory", {"path": DRIVE})
        self.call("/api/v1/index/upsert/directory", {"path": DRIVE + "/reports"})
        self.call("/api/v1/index/upsert/directory",
                  {"path": DRIVE + "/hr", "acl": {"allow": ["group:hr"]}})
        for path, text in DOCS.items():
            self.sync_file(path, text)
        self.settle()

    def settle(self):
        deadline = time.time() + 30
        while time.time() < deadline:
            if not self.forward.stats()["pending"]:
                st = graph.GraphClient(self.graph_url).post("/ingest/status", {})
                if not st["queued"] and not st["running"]:
                    return
            time.sleep(0.05)
        self.fail("graph did not settle")

    def query(self, user, name, **params):
        return self.call("/api/v1/ui/graph/query", {"query": name, "params": params}, user=user)

    def people(self, user, text):
        status, out = self.query(user, "find", text=text, types=["person"])
        self.assertEqual(status, 200, out)
        return {e["label"] for e in out["entities"]}

    # -- tests -----------------------------------------------------------------------
    def test_synced_files_become_a_graph_and_hidden_ones_stay_hidden(self):
        self.assertEqual({"Kovács János"}, self.people("lars", "Kovács"))
        self.assertEqual({"Nagy Éva"}, self.people("lars", "Nagy"))
        self.assertIn("Titkos Tamás", self.people("hanna", "Titkos"))
        self.assertEqual(set(), self.people("lars", "Titkos"))
        status, found = self.query("lars", "find", text="Kovács", types=["person"])
        kovacs = found["entities"][0]["id"]
        status, hood = self.query("lars", "neighborhood", id=kovacs, hops=2)
        self.assertEqual(200, status, hood)
        blob = json.dumps(hood, ensure_ascii=False)
        self.assertNotIn("Titkos", blob)
        self.assertNotIn("raktár", blob)
        self.assertEqual([PUBLIC], [d["source_path"] for d in hood["documents"].values()])
        status, found = self.query("hanna", "find", text="Titkos", types=["person"])
        status, control = self.query("hanna", "neighborhood", id=found["entities"][0]["id"])
        self.assertIn("Kovács János", json.dumps(control, ensure_ascii=False))
        # The two Kovács János (name only) wait for a human; only hanna sees both.
        status, review = self.call("/api/v1/ui/graph/review/list", {}, user="hanna")
        self.assertEqual((200, 1), (status, review["total"]), review)
        status, review = self.call("/api/v1/ui/graph/review/list", {}, user="lars")
        self.assertEqual((200, 0), (status, review["total"]), review)

    def test_client_cannot_widen_its_scope(self):
        status, out = self.call("/api/v1/ui/graph/query", {
            "query": "find", "params": {"text": "Titkos", "corpus_ids": ["x"],
                                        "access": None}}, user="lars")
        self.assertEqual(200, status, out)
        self.assertEqual([], out["entities"])
        status, out = self.call("/api/v1/ui/graph/query", {"query": "drop_everything"},
                                user="lars")
        self.assertEqual(400, status)

    def test_acl_change_and_delete_follow_the_sync(self):
        self.call("/api/v1/index/upsert/acl", {"path": DRIVE + "/hr", "acl": None})
        self.settle()
        self.assertIn("Titkos Tamás", self.people("lars", "Titkos"))
        self.call("/api/v1/index/drop/tree", {"path": DRIVE + "/hr"})
        self.settle()
        self.assertEqual(set(), self.people("hanna", "Titkos"))
        self.assertIn("Kovács János", self.people("hanna", "Kovács"))

    def test_stale_graph_acl_fails_closed(self):
        with patch.object(self.forward, "acl", lambda *a, **k: None):
            self.call("/api/v1/index/upsert/acl",
                      {"path": DRIVE + "/reports", "acl": {"allow": ["group:hr"]}})
        status, out = self.query("lars", "find", text="Nagy", types=["person"])
        self.assertEqual(503, status, out)

    def test_status_shows_only_visible_files(self):
        status, out = self.call("/api/v1/ui/graph/status", {}, user="lars")
        self.assertEqual(200, status, out)
        paths = {r["source_path"] for r in out["recent"]}
        self.assertIn(PUBLIC, paths)
        self.assertNotIn(HIDDEN, paths)

    def test_only_graph_admins_change_shared_state(self):
        status, out = self.call("/api/v1/ui/graph/profile/get", {}, user="lars")
        self.assertEqual((200, False), (status, out["can_edit"]))
        status, out = self.call("/api/v1/ui/graph/review/decide",
                                {"a": "x", "b": "y", "decision": "merge"}, user="lars")
        self.assertEqual(403, status)
        status, out = self.call("/api/v1/ui/graph/review/decide",
                                {"a": "x", "b": "y", "decision": "merge"}, user="hanna")
        self.assertEqual(404, status)  # admin, but no such pair in scope


class ForwarderTest(unittest.TestCase):
    class Client:
        enabled = True

        def __init__(self):
            self.calls, self.fail = [], []

        def post(self, path, body):
            if self.fail:
                raise graph.GraphError(self.fail.pop(0), {"error": "busy"})
            self.calls.append((path, body))
            return {"ok": True}

    def make(self, **kw):
        self.client = self.Client()
        self.acl = {"restricted": False, "allow": [], "deny": [], "classification": 0}
        return graph.Forwarder(self.client, lambda p: self.acl, kw.get("max_bytes", 1000),
                               kw.get("max_file", 500), autostart=False)

    def test_busy_graph_keeps_order_and_acl_is_read_at_send_time(self):
        f = self.make()
        f.file("/d/a.txt", "c", "doc-a", b"abc")
        f.drop_tree("/d/old", "c", False)
        self.client.fail = [429]
        self.assertFalse(f.deliver_one())
        self.acl = dict(self.acl, restricted=True)
        while f.deliver_one():
            pass
        self.assertEqual(["/ingest/document", "/graph/document/drop_tree"],
                         [p for p, _ in self.client.calls])
        self.assertTrue(self.client.calls[0][1]["acl"]["restricted"])

    def test_delete_cancels_waiting_files_and_limits_hold(self):
        f = self.make()
        f.file("/d/x/a.txt", "c", "a", b"12")
        f.file("/d/y.txt", "c", "y", b"12")
        f.drop_tree("/d/x", "c", False)
        self.assertEqual(["y", None], [i.get("doc_id") for i in f.queue])
        self.assertFalse(f.file("/d/big.pdf", "c", "big", b"x" * 600))
        self.assertFalse(f.file("/d/movie.mp4", "c", "m", b"1"))
        self.assertEqual(1, f.stats()["counts"]["dropped"])
        self.assertEqual(1, f.stats()["counts"]["skipped_type"])


if __name__ == "__main__":
    unittest.main()
