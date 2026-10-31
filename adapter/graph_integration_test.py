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
# The adapter image ships the adapter alone; these tests need the source tree.
HAVE_SERVICES = all(os.path.exists(os.path.join(REPO, d, "service.py"))
                    for d in ("rag_service", "graph_service"))


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


@unittest.skipUnless(HAVE_SERVICES, "needs rag_service/ and graph_service/ next to adapter/")
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
                   "GRAPH_ADMINS": frozenset({"hanna"}), "_GRAPH_PATHS": {},
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

    def test_chat_gets_graph_facts_within_the_callers_access(self):
        import llm

        def echo(query, contexts, **_kw):
            return "\n".join(c.get("text") or "" for c in contexts) or "NO-CONTEXT", "echo"

        with patch.object(llm, "generate", echo), patch.object(llm, "TITLES", False), \
                patch.object(llm, "condense", lambda q, h, lang=None: (q, False)):
            status, out = self.call("/api/v1/ui/query",
                                    {"query": "Kivel találkozott Kovács János?"}, user="lars")
            self.assertEqual(200, status, out)
            self.assertEqual(["Kovács János"], out["graph"]["entities"])
            self.assertRegex(out["answer"], r"Kovács János -- \w+ -- Nagy Éva")
            self.assertNotIn("Titkos", json.dumps(out, ensure_ascii=False))
            self.assertIn(PUBLIC, {h["path"] for h in out["hits"]})
            status, out = self.call("/api/v1/ui/query",
                                    {"query": "Kivel találkozott Titkos Tamás?"}, user="hanna")
            self.assertIn("Titkos Tamás", out["answer"])
            self.assertIn(HIDDEN, {h["path"] for h in out["hits"]})

    def test_dossier_cites_only_visible_sources(self):
        import io
        import zipfile

        def dossier(user, fmt="docx", lang="hu"):
            status, found = self.query(user, "find", text="Kovács", types=["person"])
            ids = [e["id"] for e in found["entities"]]
            outs = []
            for eid in ids:
                status, out = self.call("/api/v1/ui/graph/dossier",
                                        {"id": eid, "format": fmt, "lang": lang}, user=user)
                self.assertEqual(200, status, out)
                outs.append(out)
            return outs

        [out] = dossier("lars")
        self.assertTrue(out["name"].endswith(".docx"))
        body = zipfile.ZipFile(io.BytesIO(base64.b64decode(out["content_b64"])))
        xml = body.read("word/document.xml").decode("utf-8")
        self.assertIn("Nagy Éva", xml)
        self.assertIn("Kapcsolatok", xml)
        self.assertNotIn("Titkos", out["text"] + xml)
        self.assertIn("[1] " + PUBLIC, out["text"])
        self.assertNotIn(HIDDEN, out["text"])
        hanna = " ".join(o["text"] for o in dossier("hanna", lang="en"))
        self.assertIn("Titkos Tamás", hanna)
        self.assertIn(HIDDEN, hanna)
        [pdf] = dossier("lars", fmt="pdf")
        self.assertTrue(base64.b64decode(pdf["content_b64"]).startswith(b"%PDF"))
        status, out = self.call("/api/v1/ui/graph/dossier", {"id": "nope"}, user="lars")
        self.assertEqual(404, status, out)
        status, out = self.call("/api/v1/ui/graph/dossier", {"id": "x", "format": "exe"},
                                user="lars")
        self.assertEqual(400, status, out)

    def test_analyst_mode_orchestrates_and_asks_back(self):
        import llm

        service = self.service

        def echo(query, contexts, **_kw):
            return "\n".join(c.get("text") or "" for c in contexts) or "NO-CONTEXT", "echo"

        plans = []

        def planner(system, user, **kw):
            plans.append(user)
            return plans_out.pop(0), 0, 0

        plans_out = [
            '{"action": "tools", "calls": [{"tool": "relations", "args": '
            '{"entity": "Kovács János"}}], "search": "Kovács János kapcsolatai"}',
            '{"action": "clarify", "question": "Melyik időszakra gondol?"}',
            '{"action": "tools", "calls": [{"tool": "timeline", "args": '
            '{"entity": "Kovács János"}}], "search": "Kovács János 2025"}',
        ]
        with patch.object(service, "APP_MODE", service.APP_ANALYST), \
                patch.object(llm, "GENERATE", "on"), patch.object(llm, "_chat", planner), \
                patch.object(llm, "generate", echo), patch.object(llm, "TITLES", False):
            status, out = self.call("/api/v1/status", {})
            self.assertEqual("analyst", out["app"]["mode"])
            self.assertTrue(out["app"]["orchestrator"])
            status, out = self.call("/api/v1/ui/query", {"query": "x", "action": "summary"},
                                    user="lars")
            self.assertEqual(403, status)
            status, out = self.call("/api/v1/ui/files/children", {}, user="lars")
            self.assertEqual(403, status)

            status, out = self.call("/api/v1/ui/query",
                                    {"query": "Kikkel áll kapcsolatban Kovács János?"},
                                    user="lars")
            self.assertEqual(200, status, out)
            self.assertEqual("relations", out["graph"]["calls"][0]["tool"])
            self.assertRegex(out["answer"], r"Kovács János -- \w+ -- Nagy Éva|"
                                            r"Nagy Éva -- \w+ -- Kovács János")
            self.assertNotIn("Titkos", json.dumps(out, ensure_ascii=False))

            status, out = self.call("/api/v1/ui/query", {"query": "És mikor?"}, user="lars")
            self.assertEqual((200, True), (status, out.get("clarify")), out)
            self.assertTrue(out["answer"].startswith("Melyik időszakra gondol?"))
            status, out = self.call("/api/v1/ui/query", {"query": "2025-ben"}, user="lars")
            self.assertEqual(200, status, out)
            self.assertIn("ASSISTANT: Melyik időszakra gondol?", plans[-1])
            self.assertEqual("timeline", out["graph"]["calls"][0]["tool"])

    def export_investigation(self, user, body):
        payload = dict({"start": "Kovács János", "levels": 2, "lang": "hu"}, **body)
        return self.call("/api/v1/ui/graph/investigation/export", payload, user=user)

    def test_investigation_table_exports_as_xlsx_within_the_callers_access(self):
        import io
        import zipfile

        import llm
        import purpose
        import querylog

        service = self.service
        logdir = tempfile.TemporaryDirectory()
        self.addCleanup(logdir.cleanup)

        def echo(query, contexts, **_kw):
            return "\n".join(c.get("text") or "" for c in contexts) or "NO-CONTEXT", "echo"

        def planner(system, user, **kw):
            return ('{"action": "tools", "calls": [{"tool": "investigate", "args": '
                    '{"start": "Kovács János", "levels": 2}}], "search": "Kovács János"}'), 0, 0

        def sheet(out):
            book = zipfile.ZipFile(io.BytesIO(base64.b64decode(out["content_b64"])))
            return {n: book.read(n).decode("utf-8") for n in book.namelist()}

        # The fake extraction knows only MET: the investigation follows it here.
        with patch.object(service, "APP_MODE", service.APP_ANALYST), \
                patch.object(service, "INVESTIGATE_RELATIONS", ["MET"]), \
                patch.object(service, "INVESTIGATE_VIA", []), \
                patch.object(llm, "GENERATE", "on"), patch.object(llm, "_chat", planner), \
                patch.object(llm, "generate", echo), patch.object(llm, "TITLES", False):
            status, out = self.call("/api/v1/ui/query", {"query": "Kovács János vizsgálata"},
                                    user="lars")
            self.assertEqual(200, status, out)
            [inv] = out["graph"]["investigations"]
            self.assertEqual((["Kovács János", "Nagy Éva"], [DRIVE]),
                             ([r["name"] for r in inv["rows"]], inv["drives"]))

            status, out = self.export_investigation("lars", {"drives": inv["drives"],
                                                             "audit_id": "aud-1"})
            self.assertEqual(200, status, out)
            self.assertEqual(("xlsx", 2, False), (out["format"], out["rows"], out["truncated"]))
            self.assertTrue(out["name"].startswith("vizsgalat_Kovács_János_"), out["name"])
            self.assertTrue(out["name"].endswith(".xlsx"))
            files = sheet(out)
            table = files["xl/worksheets/sheet1.xml"]
            for text in ("Kapcsolódás útja", "Nagy Éva", PUBLIC, "aud-1"):
                self.assertIn(text, table + files["xl/worksheets/sheet2.xml"])
            blob = "".join(files.values())
            self.assertNotIn("Titkos", blob)
            self.assertNotIn(HIDDEN, blob)
            status, out = self.export_investigation("hanna", {"lang": "en"})
            self.assertEqual(200, status, out)
            self.assertIn("Titkos Tamás", "".join(sheet(out).values()))
            self.assertIn(HIDDEN, "".join(sheet(out).values()))

            # Purpose binding: no purpose, no export; with one, a query-log entry.
            with patch.object(service, "PURPOSE",
                              purpose.settings({"ADAPTER_PURPOSE": "required"})), \
                    patch.object(service, "QUERY_LOG", querylog.QueryLog(logdir.name)):
                status, out = self.export_investigation("lars", {})
                self.assertEqual((428, "purpose_required"), (status, out["error"]))
                status, out = self.export_investigation("lars", {"case_ref": "BV-2026/7"})
                self.assertEqual(200, status, out)
                self.assertIn("BV-2026/7", sheet(out)["xl/worksheets/sheet2.xml"])
                # Saved through the generated-file path: no ws-fs channel here, so
                # the stored copy is offered for download.
                status, saved = self.export_investigation(
                    "lars", {"case_ref": "BV-2026/7", "deliver": "drive"})
                self.assertEqual((200, False, True, 2), (status, saved["transferred"],
                                                         saved["stored"], saved["rows"]), saved)
            request = urllib.request.Request(
                "http://127.0.0.1:%d%s" % (self.server.server_port, saved["download"]),
                headers={"VVS-Drive": b64(DRIVE + "/"), "VVS-User": "lars"})
            with urllib.request.urlopen(request, timeout=30) as response:
                self.assertIn("spreadsheetml", response.headers["Content-Type"])
                self.assertIn(b"Nagy \xc3\x89va", zipfile.ZipFile(io.BytesIO(
                    response.read())).read("xl/worksheets/sheet1.xml"))
            name, = os.listdir(logdir.name)
            path = os.path.join(logdir.name, name)
            with open(path, encoding="utf-8") as handle:
                records = [json.loads(line) for line in handle]
            self.assertEqual(["export:investigation"] * 2, [r["kind"] for r in records])
            self.assertEqual(("lars", "BV-2026/7", [PUBLIC]),
                             (records[0]["user"], records[0]["case_ref"],
                              records[0]["result"]["sources"]))
            # The starting point is personal data: kept as a hash unless the
            # box keeps question texts (ADAPTER_QUERY_LOG_TEXT=on).
            self.assertEqual(querylog.question_hash(json.dumps(
                {"audit_id": None, "levels": 2, "rows": 2, "start": "Kovács János"},
                ensure_ascii=False, sort_keys=True)), records[0]["question_sha256"])
            self.assertNotIn("question", records[0])
            self.assertEqual((True, 2, None), querylog.verify(path))

            # Refusals: a drive outside the grant, a hidden-only start, bad input.
            status, _ = self.export_investigation("lars", {"drives": ["/storage/drives/hr/"]})
            self.assertEqual(403, status)
            status, _ = self.export_investigation("lars", {"start": "Titkos Tamás"})
            self.assertEqual(404, status)
            for bad in ({"levels": 9}, {"start": ""}, {"deliver": "mail"}):
                status, _ = self.export_investigation("lars", bad)
                self.assertEqual(400, status, bad)
            with patch.object(service, "INVESTIGATE_RELATIONS", ["NO_SUCH"]):
                status, _ = self.export_investigation("lars", {})
                self.assertEqual(409, status)
            # A spreadsheet is never "rendered" from client text.
            status, _ = self.call("/api/v1/ui/save", {"name": "x", "text": "a",
                                                      "format": "xlsx"}, user="lars")
            self.assertEqual(400, status)

    def test_analyst_counts_then_replans_with_the_results(self):
        import llm

        service = self.service
        prompts = []
        plans_out = [
            '{"action": "tools", "more": true, "calls": [{"tool": "count", "args": '
            '{"relation": "MET", "group_by": "entity"}}], "search": "találkozók"}',
            '{"action": "tools", "calls": [{"tool": "relations", "args": '
            '{"entity": "Kovács János"}}]}',
        ]

        def planner(system, user, **kw):
            prompts.append((system, user))
            return plans_out.pop(0), 0, 0

        def echo(query, contexts, **_kw):
            return "\n".join(c.get("text") or "" for c in contexts) or "NO-CONTEXT", "echo"

        with patch.object(service, "APP_MODE", service.APP_ANALYST), \
                patch.object(llm, "GENERATE", "on"), patch.object(llm, "_chat", planner), \
                patch.object(llm, "generate", echo), patch.object(llm, "TITLES", False):
            status, out = self.call("/api/v1/ui/query",
                                    {"query": "Ki találkozott a legtöbbször másokkal?"},
                                    user="lars")
        self.assertEqual(200, status, out)
        self.assertIn("MET =", prompts[0][0])  # the profile vocabulary reaches the planner
        self.assertIn("RESULTS SO FAR", prompts[1][1])
        self.assertIn("Kovács János", prompts[1][1])
        self.assertEqual([("count", 1), ("relations", 2)],
                         [(c["tool"], c["round"]) for c in out["graph"]["calls"]])
        self.assertIn("Kovács János --", out["answer"])
        self.assertNotIn("Titkos", json.dumps(out, ensure_ascii=False))
        self.assertNotIn("Titkos", "".join(u for _, u in prompts))

    def test_empty_cross_document_result_shows_the_merge_caveat(self):
        import llm
        import orchestrator

        service = self.service
        plan = ('{"action": "tools", "calls": [{"tool": "sequence", "args": {"first": "MET", '
                '"then": "MET", "within_days": 0, "from": "1900", "to": "1900"}}], '
                '"search": "Kovács János találkozói"}')
        with patch.object(service, "APP_MODE", service.APP_ANALYST), \
                patch.object(llm, "GENERATE", "on"), \
                patch.object(llm, "_chat", return_value=(plan, 0, 0)), \
                patch.object(llm, "generate",
                             lambda *a, **k: ("Nincs ilyen.", "echo")), \
                patch.object(llm, "TITLES", False), \
                patch.object(orchestrator.Run, "_pending", return_value=4):
            status, out = self.call("/api/v1/ui/query",
                                    {"query": "Ki találkozott Kovács Jánossal egymás után?"},
                                    user="lars")
        self.assertEqual(200, status, out)
        self.assertEqual(0, out["graph"]["calls"][0]["facts"])
        self.assertRegex(out["answer"], r"^Nincs ilyen\.\n\n> .*\b4\b.*\n\n---\n")

    def test_a_full_reading_reads_every_visible_passage_in_the_heavy_slot(self):
        import deep
        import llm

        service = self.service
        plan = ('{"action": "tools", "calls": [{"tool": "read_all", "args": '
                '{"terms": ["Kovács János"]}}], "search": "Kovács János"}')
        prompts = []

        def model(system, user, **kw):
            prompts.append((system, user))
            if system == deep.MAP_SYSTEM:
                return ('{"findings": [{"excerpt": 1, "quote": "Kovács János és Nagy Éva", '
                        '"note": "Nagy Évával találkozott."}]}'), 0, 0
            return plan, 0, 0

        def echo(query, contexts, **_kw):
            return "\n".join(c.get("text") or "" for c in contexts), "echo"

        with patch.object(service, "APP_MODE", service.APP_ANALYST), \
                patch.object(llm, "GENERATE", "on"), patch.object(llm, "_chat", model), \
                patch.object(llm, "generate", echo), patch.object(llm, "TITLES", False):
            status, out = self.call("/api/v1/ui/query",
                                    {"query": "Sorold fel, hol szerepel Kovács János!"},
                                    user="lars")
            self.assertEqual(200, status, out)
            self.assertEqual((1, 1, 1, False), (out["deep"]["read"], out["deep"]["documents"],
                                                out["deep"]["findings"], out["deep"]["partial"]))
            self.assertIn("1 r\u00e9szlet 1 dokumentumb\u00f3l", out["answer"])
            self.assertIn("\n\n> Teljes \u00e1tn\u00e9z\u00e9s:", out["answer"])  # shown as is
            self.assertIn("Nagy Évával találkozott.", out["answer"])
            self.assertIn("read_all", prompts[0][0])  # offered in the first round
            self.assertNotIn("Titkos", json.dumps(out, ensure_ascii=False))
            self.assertNotIn("Titkos", "".join(u for _, u in prompts))
            # As a job: the phase reaches the job record.
            status, sub = self.call("/api/v1/ui/ask",
                                    {"query": "Sorold fel, hol szerepel Kovács János!"},
                                    user="lars")
            self.assertEqual(202, status, sub)
            status, done = self.call("/api/v1/ui/poll", {"job_id": sub["job_id"], "timeout": 20},
                                     user="lars")
            self.assertEqual((200, "done"), (status, done.get("status")), done)
            status, job = self.call("/api/v1/ui/jobs/get", {"job_id": sub["job_id"]},
                                    user="lars")
            self.assertEqual(("write", 1, 1), (job["job"]["progress_phase"],
                                               job["job"]["progress_done"],
                                               job["job"]["progress_total"]))

    def test_analyst_analyses_one_named_file_in_full(self):
        import deep
        import llm

        service = self.service
        prompts, generated = [], []

        def model(system, user, **kw):
            prompts.append((system, user))
            if system == deep.SECTION_SYSTEM:
                return ('{"findings": [{"excerpt": 1, "quote": "Kovács János és Nagy Éva", '
                        '"note": "Találkoztak."}]}'), 0, 0
            name = "secret.txt" if "secret" in user else "note.txt"
            return ('{"action": "tools", "calls": [{"tool": "analyze_document", "args": '
                    '{"file": "%s"}}], "search": "elemzés"}' % name), 0, 0

        def echo(query, contexts, **kw):
            generated.append(kw.get("task"))
            # An analysis shorter than ANALYZE_MIN_ANSWER_CHARS counts as starved.
            return ("\n".join(c.get("text") or "" for c in contexts) or "NO-CONTEXT") + \
                "\n" + "." * 130, "echo"

        with patch.object(service, "APP_MODE", service.APP_ANALYST), \
                patch.object(llm, "GENERATE", "on"), patch.object(llm, "_chat", model), \
                patch.object(llm, "generate", echo), patch.object(llm, "TITLES", False):
            # Small enough for one call: the whole file is the context.
            status, out = self.call("/api/v1/ui/query", {"query": "Elemezd a note.txt fájlt!"},
                                    user="lars")
            self.assertEqual(200, status, out)
            self.assertIn(DOCS[PUBLIC], out["answer"])
            self.assertEqual(["analyze"], generated)
            self.assertIn("analyze_document", prompts[0][0])
            # Over the window: read in parts inside the heavy slot.
            with patch.object(service, "ANALYZE_MAX_CHARS", 10):
                status, out = self.call("/api/v1/ui/query",
                                        {"query": "Elemezd a note.txt fájlt!"}, user="lars")
            self.assertEqual(200, status, out)
            self.assertEqual(("document", 1, False), (out["deep"]["kind"], out["deep"]["read"],
                                                      out["deep"]["partial"]))
            self.assertIn("Találkoztak.", out["answer"])
            self.assertIn("\n\n> Teljes lefedetts\u00e9g\u0171 elemz\u00e9s", out["answer"])
            # A file the caller may not see is never read, by name or otherwise.
            status, out = self.call("/api/v1/ui/query",
                                    {"query": "Elemezd a secret.txt fájlt!"}, user="lars")
            self.assertEqual(200, status, out)
            self.assertNotIn("Titkos", json.dumps(out, ensure_ascii=False))
            self.assertNotIn("Titkos", "".join(u for _, u in prompts))
            # The citation button: analyze is the one action the analyst box takes.
            status, out = self.call("/api/v1/ui/query", {"query": "Teljes elemzés",
                                                         "action": "analyze",
                                                         "files": [PUBLIC]}, user="lars")
            self.assertEqual(200, status, out)
            self.assertEqual("analyze", out["action"])
            self.assertIn(DOCS[PUBLIC], out["answer"])
            status, out = self.call("/api/v1/ui/query", {"query": "Teljes elemzés",
                                                         "action": "analyze",
                                                         "files": [HIDDEN]}, user="lars")
            self.assertNotIn("Titkos", json.dumps(out, ensure_ascii=False))

    def test_graph_queries_and_exports_carry_their_purpose(self):
        import purpose
        import querylog

        service = self.service
        logdir = tempfile.TemporaryDirectory()
        self.addCleanup(logdir.cleanup)
        with patch.object(service, "PURPOSE", purpose.settings({"ADAPTER_PURPOSE": "required"})), \
                patch.object(service, "QUERY_LOG", querylog.QueryLog(logdir.name)):
            status, out = self.query("lars", "find", text="Kovács", types=["person"])
            self.assertEqual((428, "purpose_required"), (status, out["error"]))
            status, out = self.call("/api/v1/ui/graph/query",
                                    {"query": "find", "case_ref": "BV-2026/7",
                                     "params": {"text": "Kovács", "types": ["person"]}},
                                    user="lars")
            self.assertEqual(200, status, out)
            status, out = self.call("/api/v1/ui/graph/review/list", {}, user="lars")
            self.assertEqual(428, status)
            # Status is not content: it needs no purpose.
            status, _ = self.call("/api/v1/ui/graph/status", {}, user="lars")
            self.assertEqual(200, status)
        name, = os.listdir(logdir.name)
        path = os.path.join(logdir.name, name)
        with open(path, encoding="utf-8") as handle:
            record, = [json.loads(line) for line in handle]
        self.assertEqual(("lars", "graph:find", "BV-2026/7"),
                         (record["user"], record["kind"], record["case_ref"]))
        self.assertEqual([PUBLIC], record["result"]["sources"])
        self.assertEqual((True, 1, None), querylog.verify(path))

    def test_a_profile_change_is_reprocessed_from_the_file_source(self):
        import reprocess

        service = self.service
        source = tempfile.TemporaryDirectory()
        self.addCleanup(source.cleanup)
        for path, text in DOCS.items():
            local = os.path.join(source.name, path[len("/storage/drives/"):])
            os.makedirs(os.path.dirname(local), exist_ok=True)
            with open(local, "w", encoding="utf-8") as handle:
                handle.write(text)
        runner = reprocess.Reprocessor(service.GRAPH, self.forward, service._reprocess_fetch,
                                       idle_wait=0.05)
        with patch.object(service, "REPROCESS_SOURCE_DIR", source.name), \
                patch.object(service, "REPROCESS", runner):
            status, out = self.call("/api/v1/ui/graph/reprocess/status", {}, user="lars")
            self.assertEqual((200, 0, False, "local"), (status, out["stale_total"],
                                                         out["can_start"], out["source"]))
            _, prof = self.call("/api/v1/ui/graph/profile/get", {}, user="hanna")
            newer = dict(prof["profile"], version=prof["profile"]["version"] + 1)
            status, _ = self.call("/api/v1/ui/graph/profile/set", {"profile": newer}, user="hanna")
            self.assertEqual(200, status)
            # Counts cover only what the caller may see.
            self.assertEqual(1, self.call("/api/v1/ui/graph/reprocess/status", {},
                                          user="lars")[1]["stale_total"])
            self.assertEqual(2, self.call("/api/v1/ui/graph/reprocess/status", {},
                                          user="hanna")[1]["stale_total"])
            status, _ = self.call("/api/v1/ui/graph/reprocess/start", {}, user="lars")
            self.assertEqual(403, status)
            status, out = self.call("/api/v1/ui/graph/reprocess/start",
                                    {"path_prefix": "/storage/drives/other"}, user="hanna")
            self.assertEqual(403, status)
            status, out = self.call("/api/v1/ui/graph/reprocess/start", {}, user="hanna")
            self.assertEqual((200, 2), (status, out["run"]["total"]))
            self.assertTrue(runner.wait(30))
            self.settle()
            _, out = self.call("/api/v1/ui/graph/reprocess/status", {}, user="hanna")
            self.assertEqual((0, "done", 2), (out["stale_total"], out["run"]["state"],
                                              out["run"]["sent"]))
            self.assertTrue(out["current"].startswith("%s@%s/e" % (newer["id"], newer["version"])))
        self.assertEqual({"Kovács János"}, self.people("lars", "Kovács"))
        self.assertNotIn("Titkos", json.dumps(self.call("/api/v1/ui/graph/reprocess/status", {},
                                                        user="lars")[1], ensure_ascii=False))

    def test_synced_files_the_graph_never_got_are_caught_up(self):
        import corpus
        import reprocess

        service = self.service
        source = tempfile.TemporaryDirectory()
        self.addCleanup(source.cleanup)
        for path, text in DOCS.items():
            local = os.path.join(source.name, path[len("/storage/drives/"):])
            os.makedirs(os.path.dirname(local), exist_ok=True)
            with open(local, "w", encoding="utf-8") as handle:
                handle.write(text)
        corpus_id = corpus.corpus_id_of_path(HIDDEN)
        raw = DOCS[HIDDEN].encode()
        _, check = self.call("/api/v1/index/upsert/file/check",
                             {"path": HIDDEN, "size": len(raw), "mtime": 1700000001,
                              "head": base64.b64encode(raw[:64]).decode()})
        status, done = self.call("/api/v1/index/upsert/file/content/" + check["token"],
                                 raw=raw, extra={"Content-Type": "text/plain"})
        # The id the adapter gives a file it sends itself is the RAG's.
        self.assertEqual(corpus.doc_id_of(service.TENANT_ID, corpus_id, HIDDEN), done["doc_id"])
        self.settle()
        # The graph lost it (a full forward queue during a large sync, say).
        graph.GraphClient(self.graph_url).post("/graph/document/drop_tree",
                                               {"corpus_id": corpus_id, "path": HIDDEN})
        runner = reprocess.Reprocessor(service.GRAPH, self.forward, service._reprocess_fetch,
                                       idle_wait=0.05)
        with patch.object(service, "REPROCESS_SOURCE_DIR", source.name), \
                patch.object(service, "REPROCESS", runner):
            _, out = self.call("/api/v1/ui/graph/reprocess/status", {}, user="hanna")
            self.assertEqual((1, 0), (out["absent_total"], out["stale_total"]))
            # A file the caller may not see is not counted for them.
            self.assertEqual(0, self.call("/api/v1/ui/graph/reprocess/status", {},
                                          user="lars")[1]["absent_total"])
            self.assertEqual(set(), self.people("hanna", "Titkos"))
            status, out = self.call("/api/v1/ui/graph/reprocess/start", {}, user="hanna")
            self.assertEqual((200, 1), (status, out["run"]["total"]))
            self.assertTrue(runner.wait(30))
            self.settle()
            _, out = self.call("/api/v1/ui/graph/reprocess/status", {}, user="hanna")
            self.assertEqual((0, 1), (out["absent_total"], out["run"]["sent"]))
        self.assertIn("Titkos Tamás", self.people("hanna", "Titkos"))
        self.assertEqual(set(), self.people("lars", "Titkos"))

    def test_unreadable_documents_are_listed_within_the_callers_view(self):
        for path in (DRIVE + "/reports/diagram.vsd", DRIVE + "/hr/org.vsd"):
            status, _ = self.call("/api/v1/index/upsert/file/check",
                                  {"path": path, "size": 10, "mtime": 1700000000, "head": ""})
            self.assertEqual(200, status)
        status, out = self.call("/api/v1/ui/index/skipped", {}, user="lars")
        self.assertEqual(200, status, out)
        self.assertEqual([DRIVE + "/reports/diagram.vsd"],
                         [d["source_path"] for d in out["documents"]])
        self.assertEqual({"unsupported_type": 1}, out["reasons"])
        self.assertNotIn("org.vsd", json.dumps(out))
        status, out = self.call("/api/v1/ui/index/skipped", {}, user="hanna")
        self.assertEqual(2, out["count"])

    def test_only_graph_admins_change_shared_state(self):
        status, out = self.call("/api/v1/ui/graph/profile/get", {}, user="lars")
        self.assertEqual((200, False), (status, out["can_edit"]))
        status, out = self.call("/api/v1/ui/graph/review/decide",
                                {"a": "x", "b": "y", "decision": "merge"}, user="lars")
        self.assertEqual(403, status)
        status, out = self.call("/api/v1/ui/graph/review/decide",
                                {"a": "x", "b": "y", "decision": "merge"}, user="hanna")
        self.assertEqual(404, status)  # admin, but no such pair in scope
        status, out = self.call("/api/v1/ui/graph/review/undo", {"a": "x", "b": "y"},
                                user="lars")
        self.assertEqual(403, status)

    def test_review_in_bulk_with_history_and_undo(self):
        status, review = self.call("/api/v1/ui/graph/review/list", {"q": "kovacs"}, user="hanna")
        self.assertEqual((200, 1, {"same name": 1}),
                         (status, review["total"], review["reasons"]), review)
        pair = {k: review["review"][0][k] for k in ("a", "b")}
        status, out = self.call("/api/v1/ui/graph/review/decide", {
            "decision": "merge", "pairs": [pair, {"a": "x", "b": "y"}]}, user="hanna")
        self.assertEqual((200, 1, 1), (status, out["done"], len(out["skipped"])), out)
        status, hist = self.call("/api/v1/ui/graph/review/history", {}, user="hanna")
        self.assertEqual(200, status, hist)
        [entry] = hist["history"]
        self.assertEqual(("merge", "hanna", True, True),
                         (entry["decision"], entry["by"], entry["undoable"], hist["can_undo"]))
        # lars sees only one of the two Kovács János: no history for him.
        status, hist = self.call("/api/v1/ui/graph/review/history", {}, user="lars")
        self.assertEqual((200, [], False), (status, hist["history"], hist["can_undo"]))
        status, out = self.call("/api/v1/ui/graph/review/undo", pair, user="hanna")
        self.assertEqual((200, "merge"), (status, out["undone"]), out)
        status, review = self.call("/api/v1/ui/graph/review/list", {}, user="hanna")
        self.assertEqual(1, review["total"])
        status, out = self.call("/api/v1/ui/graph/review/undo", pair, user="hanna")
        self.assertEqual(404, status)  # nothing left to undo


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
