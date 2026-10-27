"""Purpose-bound queries (purpose.py) and their tamper-evident log (querylog.py),
unit and over HTTP with a mocked RAG and model.

  Push-Location adapter; python -m unittest purpose_test; Pop-Location
"""
import base64
import json
import os
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest.mock import patch

import llm
import purpose
import querylog


class PurposeTest(unittest.TestCase):
    def test_modes_and_fail_safe_defaults(self):
        self.assertEqual("off", purpose.settings({})["mode"])
        self.assertEqual("optional", purpose.settings({"ADAPTER_PURPOSE": "Optional"})["mode"])
        self.assertEqual("required", purpose.settings({"ADAPTER_PURPOSE": "maybe"})["mode"])
        broken = purpose.settings({"ADAPTER_PURPOSE": "required", "ADAPTER_CASE_REF_PATTERN": "("})
        with self.assertRaises(purpose.PurposeError):
            purpose.parse({"case_ref": "BV-1"}, broken)  # an invalid pattern accepts nothing
        self.assertEqual({"legal_basis": "x"}, purpose.parse({"legal_basis": "x"}, broken))

    def test_required_optional_and_off(self):
        required = purpose.settings({"ADAPTER_PURPOSE": "required",
                                     "ADAPTER_LEGAL_BASES": "Bv. tv. 21. §; Infotv. 5. §",
                                     "ADAPTER_CASE_REF_PATTERN": r"BV-\d{4}/\d+"})
        with self.assertRaises(purpose.PurposeError) as caught:
            purpose.parse({}, required)
        self.assertEqual("purpose_required", caught.exception.code)
        self.assertEqual({"case_ref": "BV-2026/51754"},
                         purpose.parse({"case_ref": "  BV-2026/51754 "}, required))
        self.assertEqual({"legal_basis": "Infotv. 5. §"},
                         purpose.parse({"legal_basis": "Infotv. 5. §"}, required))
        for bad in ({"case_ref": "51754"}, {"legal_basis": "kíváncsiság"},
                    {"case_ref": "BV-2026/1\x00"}, {"case_ref": "BV-2026/" + "1" * 80}):
            with self.assertRaises(purpose.PurposeError, msg=bad) as caught:
                purpose.parse(bad, required)
            self.assertEqual("purpose_invalid", caught.exception.code)
        optional = purpose.settings({"ADAPTER_PURPOSE": "optional"})
        self.assertEqual({}, purpose.parse({}, optional))
        self.assertEqual({}, purpose.parse({"case_ref": "x"}, purpose.settings({})))
        self.assertEqual({"mode": "required", "legal_bases": ["Bv. tv. 21. §", "Infotv. 5. §"],
                          "case_ref_pattern": r"BV-\d{4}/\d+"}, purpose.public(required))


class QueryLogTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

    def test_records_chain_and_keep_no_question_text(self):
        log = querylog.QueryLog(self.temp.name)
        first = log.record({"user": "lars", "kind": "ask", "case_ref": "BV-1",
                            "question": "Ki látogatta Kovács Jánost?"})
        second = querylog.QueryLog(self.temp.name).record({"user": "lars", "kind": "ask",
                                                           "question": "És mikor?"})
        self.assertEqual(first["hash"], second["prev"])  # a new process continues the chain
        path = os.path.join(self.temp.name, os.listdir(self.temp.name)[0])
        self.assertEqual((True, 2, None), querylog.verify(path))
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
        self.assertNotIn("Kovács", text)
        self.assertIn(querylog.question_hash("Ki látogatta Kovács Jánost?"), text)
        lines = text.splitlines()
        edited = lines[0].replace("BV-1", "BV-2")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("\n".join([edited, lines[1]]) + "\n")
        self.assertEqual((False, 0, 1), querylog.verify(path))
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(lines[1] + "\n")  # the first record removed
        self.assertEqual((False, 0, 1), querylog.verify(path))

    def test_text_only_on_request_and_disabled_log_refuses(self):
        kept = querylog.QueryLog(self.temp.name, keep_text=True).record({"question": "Mit?"})
        self.assertEqual("Mit?", kept["question"])
        self.assertFalse(querylog.QueryLog("").available())
        with self.assertRaises(OSError):
            querylog.QueryLog("").record({})


class PurposeHttpTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
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
        cls.temp.cleanup()

    def setUp(self):
        import session
        service = self.service
        service.SESSIONS = session.SessionManager()
        self.logdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.logdir.cleanup)
        contexts = [{"text": "Kovács János 2026-ban két látogatót fogadott.",
                     "source_path": "/storage/drives/finance/visits.txt", "page_number": 1,
                     "chunk_id": "c1", "score": 0.8}]
        for target, name, value in (
                (service, "PURPOSE", purpose.settings({"ADAPTER_PURPOSE": "required",
                                                       "ADAPTER_LEGAL_BASES": "Bv. tv. 21. §"})),
                (service, "QUERY_LOG", querylog.QueryLog(self.logdir.name)),
                (service, "rag_post_json", lambda path, body: {"contexts": contexts}),
                (llm, "_chat", lambda *a, **k: ("Két látogatót fogadott [#1].", 0, 0)),
                (llm, "GENERATE", "on"), (llm, "TITLES", False),
                (llm, "condense", lambda query, history, lang: (query, False))):
            guard = patch.object(target, name, value)
            guard.start()
            self.addCleanup(guard.stop)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), service.Handler)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def request(self, path, body):
        headers = {"Content-Type": "application/json", "VVS-User": "lars",
                   "VVS-Drive": base64.urlsafe_b64encode(b"/storage/drives/finance").decode()}
        req = urllib.request.Request("http://127.0.0.1:%d/api/v1/%s" % (self.server.server_port, path),
                                     data=json.dumps(body).encode("utf-8"), headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=20) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as error:
            return error.code, json.load(error)

    def records(self):
        names = sorted(os.listdir(self.logdir.name))
        if not names:
            return []
        with open(os.path.join(self.logdir.name, names[-1]), encoding="utf-8") as handle:
            return [json.loads(line) for line in handle if line.strip()]

    def test_a_query_without_purpose_is_refused_and_a_bound_one_logged(self):
        status, out = self.request("ui/query", {"query": "Ki látogatta Kovács Jánost?"})
        self.assertEqual((428, "purpose_required"), (status, out["error"]))
        self.assertEqual(["Bv. tv. 21. §"], out["purpose"]["legal_bases"])
        status, out = self.request("ui/query", {"query": "Ki?", "legal_basis": "Kíváncsiság"})
        self.assertEqual((400, "purpose_invalid"), (status, out["error"]))
        self.assertEqual([], self.records())
        status, out = self.request("ui/query", {"query": "Ki látogatta Kovács Jánost?",
                                                "case_ref": "BV-2026/51754",
                                                "legal_basis": "Bv. tv. 21. §"})
        self.assertEqual(200, status, out)
        record, = self.records()
        self.assertEqual(("lars", "ask", "BV-2026/51754", "Bv. tv. 21. §"),
                         (record["user"], record["kind"], record["case_ref"], record["legal_basis"]))
        self.assertEqual(["/storage/drives/finance/visits.txt"], record["result"]["sources"])
        self.assertEqual(out["audit_id"], record["result"]["audit_id"])
        self.assertNotIn("Kovács", json.dumps(record, ensure_ascii=False))
        status, out = self.request("status", {})
        self.assertEqual("required", out["app"]["purpose"]["mode"])

    def test_a_job_logs_its_outcome(self):
        status, sub = self.request("ui/ask", {"query": "Ki?", "case_ref": "BV-1"})
        self.assertEqual(202, status, sub)
        deadline = time.time() + 20
        while time.time() < deadline:
            status, done = self.request("ui/poll", {"job_id": sub["job_id"], "timeout": 2})
            if done.get("status") != "pending":
                break
        self.assertEqual("done", done.get("status"), done)
        for _ in range(50):
            if self.records():
                break
            time.sleep(0.05)
        record, = self.records()
        self.assertEqual(("BV-1", 200, 1), (record["case_ref"], record["result"]["status"],
                                            record["result"]["citations"]))

    def test_without_a_writable_log_a_bound_query_is_not_answered(self):
        with patch.object(self.service, "QUERY_LOG", querylog.QueryLog("")):
            status, out = self.request("ui/query", {"query": "Ki?", "case_ref": "BV-1"})
        self.assertEqual((503, "query log unavailable"), (status, out["error"]))

    def test_off_keeps_the_old_behaviour(self):
        with patch.object(self.service, "PURPOSE", purpose.settings({})):
            status, out = self.request("ui/query", {"query": "Ki?"})
        self.assertEqual(200, status, out)
        self.assertEqual([], self.records())


if __name__ == "__main__":
    unittest.main()
