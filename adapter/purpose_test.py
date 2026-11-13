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
        self.assertEqual({"mode": "required", "fields": ["case_ref", "legal_basis"],
                          "require": "any", "legal_bases": ["Bv. tv. 21. §", "Infotv. 5. §"],
                          "case_ref_pattern": r"BV-\d{4}/\d+"}, purpose.public(required))

    def test_a_case_reference_alone_can_be_required(self):
        # BV (2026-11-13): the case reference is mandatory, a legal basis is not asked for.
        bv = purpose.settings({"ADAPTER_PURPOSE": "required", "ADAPTER_PURPOSE_FIELDS": "case_ref",
                               "ADAPTER_CASE_REF_PATTERN": r"\d{4}/\d+(/\d+)?"})
        self.assertEqual((["case_ref"], "all"), (purpose.public(bv)["fields"],
                                                 purpose.public(bv)["require"]))
        self.assertEqual({"case_ref": "2026/123/4"}, purpose.parse({"case_ref": "2026/123/4"}, bv))
        self.assertEqual({"case_ref": "2026/123"},
                         purpose.parse({"case_ref": "2026/123", "legal_basis": "x"}, bv))
        for bad, code in (({"legal_basis": "Bv. tv."}, "purpose_required"),
                          ({}, "purpose_required"), ({"case_ref": "R-2020/10"}, "purpose_invalid"),
                          ({"case_ref": "2026/123/4/5"}, "purpose_invalid")):
            with self.assertRaises(purpose.PurposeError, msg=bad) as caught:
                purpose.parse(bad, bv)
            self.assertEqual(code, caught.exception.code)
        both = purpose.settings({"ADAPTER_PURPOSE": "required", "ADAPTER_PURPOSE_FIELDS": "both"})
        with self.assertRaises(purpose.PurposeError):
            purpose.parse({"case_ref": "1"}, both)
        self.assertEqual(2, len(purpose.parse({"case_ref": "1", "legal_basis": "x"}, both)))
        with patch("sys.stderr"):
            typo = purpose.settings({"ADAPTER_PURPOSE": "required",
                                     "ADAPTER_PURPOSE_FIELDS": "case-ref"})
        self.assertEqual("both", typo["fields"])  # the stricter one
        self.assertEqual({}, purpose.parse({}, purpose.settings(
            {"ADAPTER_PURPOSE": "optional", "ADAPTER_PURPOSE_FIELDS": "case_ref"})))


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

    def test_retention_removes_whole_old_days_and_the_rest_still_verifies(self):
        import datetime as dt
        day = lambda y, m, d: dt.datetime(y, m, d, 12, tzinfo=dt.timezone.utc)  # noqa: E731
        log = querylog.QueryLog(self.temp.name, retention_days=1827)
        log.record({"user": "lars", "kind": "ask"}, now=day(2021, 11, 11))
        log.record({"user": "lars", "kind": "ask"}, now=day(2021, 11, 12))
        log.record({"user": "anna", "kind": "ask"}, now=day(2026, 11, 13))
        # The first record of a new day removed what is older than the retention
        # (1827 days: five years whatever the leap days).
        self.assertEqual(["queries-20211112.jsonl", "queries-20261113.jsonl"],
                         sorted(os.listdir(self.temp.name)))
        for name in os.listdir(self.temp.name):
            self.assertTrue(querylog.verify(os.path.join(self.temp.name, name))[0])
        self.assertEqual(["queries-20211112.jsonl"], log.purge(day(2026, 11, 15)))
        # 0 (and anything invalid) keeps every file: removal cannot be undone.
        with patch("sys.stderr"):
            for raw in ("", "0", "-5", "5 év"):
                self.assertEqual(0, querylog.QueryLog.from_env(
                    {"ADAPTER_QUERY_LOG_RETENTION_DAYS": raw}).retention_days)
        self.assertEqual([], querylog.QueryLog(self.temp.name).purge(day(2099, 1, 1)))

    def test_read_filters_newest_first_and_marks_broken_days(self):
        import datetime as dt
        log = querylog.QueryLog(self.temp.name)
        for i, (user, case) in enumerate((("lars", "2026/1"), ("Anna", "2026/2/1"),
                                          ("lars", "2026/2/7"))):
            log.record({"user": user, "case_ref": case, "kind": "ask"},
                       now=dt.datetime(2026, 11, 10 + i, tzinfo=dt.timezone.utc))
        since, until = dt.date(2026, 11, 1), dt.date(2026, 11, 30)
        out = log.read(since, until)
        self.assertEqual((3, ["2026/2/7", "2026/2/1", "2026/1"]),
                         (out["total"], [r["case_ref"] for r in out["records"]]))
        self.assertTrue(all(r["intact"] and "prev" not in r for r in out["records"]))
        self.assertEqual(["2026/2/1"], [r["case_ref"] for r in log.read(
            since, until, user="anna")["records"]])
        self.assertEqual(["2026/2/7", "2026/2/1"], [r["case_ref"] for r in log.read(
            since, until, case_ref="2026/2")["records"]])
        page = log.read(since, until, offset=1, limit=1)
        self.assertEqual((3, ["2026/2/1"]), (page["total"], [r["case_ref"] for r in page["records"]]))
        self.assertEqual(1, log.read(dt.date(2026, 11, 11), dt.date(2026, 11, 11))["total"])
        # Later pages of one view leave out what was written since its first page.
        first = log.read(since, until)
        self.assertEqual("20261112:1", first["as_of"])
        log.record({"user": "lars", "case_ref": "2026/9", "kind": "ask"},
                   now=dt.datetime(2026, 11, 12, 23, tzinfo=dt.timezone.utc))
        later = log.read(since, until, offset=1, limit=1, before=first["as_of"])
        self.assertEqual((3, ["2026/2/1"]), (later["total"],
                                             [r["case_ref"] for r in later["records"]]))
        self.assertEqual(4, log.read(since, until)["total"])
        path = os.path.join(self.temp.name, "queries-20261112.jsonl")
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text.replace("2026/2/7", "2026/2/8"))
        self.assertFalse(log.read(since, until)["records"][0]["intact"])
        # Undecodable bytes or a line that is no record never stop the view.
        other = os.path.join(self.temp.name, "queries-20261110.jsonl")
        with open(other, "ab") as handle:
            handle.write(b"\xff\xfe garbage\n[1]\n")
        out = log.read(since, until)
        self.assertEqual(4, out["total"])
        self.assertFalse(out["records"][-1]["intact"])
        self.assertTrue(out["records"][-2]["intact"])  # 2026-11-11 is untouched
        self.assertEqual(False, querylog.verify(other)[0])
        today = dt.date(2026, 11, 13)
        self.assertEqual((dt.date(2026, 10, 15), today), querylog.view_range(None, None, today))
        for bad in (("2026-11-13", "2026-11-01"), ("2020-01-01", "2026-01-01"), ("x", None)):
            with self.assertRaises(ValueError, msg=bad):
                querylog.view_range(*bad, today=today)


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

    def request(self, path, body, user="lars"):
        headers = {"Content-Type": "application/json", "VVS-User": user,
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

    def test_the_log_view_shows_an_analyst_their_own_records_and_a_leader_all(self):
        log = self.service.QUERY_LOG
        log.record({"user": "lars", "kind": "ask", "case_ref": "2026/1",
                    "result": {"ok": True, "sources": ["/storage/drives/finance/visits.txt",
                                                       "/storage/drives/hr/secret.txt"]}})
        log.record({"user": "anna", "kind": "ask", "case_ref": "2026/2"})
        status, out = self.request("ui/querylog", {})
        self.assertEqual((200, "analyst", 1), (status, out["role"], out["total"]), out)
        record, = out["records"]
        self.assertEqual("2026/1", record["case_ref"])
        # A document outside the viewer's scope is counted, not named.
        self.assertEqual((["/storage/drives/finance/visits.txt"], 1),
                         (record["result"]["sources"], record["result"]["hidden_sources"]))
        # An analyst cannot ask for someone else's records.
        status, out = self.request("ui/querylog", {"user": "anna"})
        self.assertEqual({"lars"}, {r["user"] for r in out["records"]})
        # Viewing is logged too: the first view is now the newest own record.
        self.assertEqual(["querylog:view", "ask"], [r["kind"] for r in out["records"]])
        with patch.object(self.service, "LEADERS", frozenset({"vera"})):
            status, out = self.request("ui/querylog", {}, user="vera")
            self.assertEqual("leader", out["role"])
            self.assertEqual({"lars", "anna"}, {r["user"] for r in out["records"]})
            status, out = self.request("ui/querylog", {"user": "ANNA"}, user="vera")
            self.assertEqual(["2026/2"], [r["case_ref"] for r in out["records"]])
        status, out = self.request("ui/querylog", {"from": "2026-11-13", "to": "2026-01-01"})
        self.assertEqual(400, status)
        # Without a user name an analyst gets nothing, never everyone's records.
        status, out = self.request("ui/querylog", {}, user="")
        self.assertEqual(403, status, out)

    def test_later_pages_of_the_log_view_do_not_repeat_rows(self):
        for i in range(5):
            self.service.QUERY_LOG.record({"user": "lars", "kind": "ask", "case_ref": "2026/%d" % i})
        status, first = self.request("ui/querylog", {"limit": 2})
        status, second = self.request("ui/querylog", {"limit": 2, "offset": 2,
                                                      "before": first["as_of"]})
        cases = [r["case_ref"] for r in first["records"] + second["records"]]
        self.assertEqual(["2026/4", "2026/3", "2026/2", "2026/1"], cases)
        self.assertEqual(first["total"], second["total"])

    def test_a_leader_role_needs_a_directory_answer_without_error(self):
        import directory

        class Directory:
            def __init__(self, info):
                self.info = info

            def lookup(self, caller):
                return self.info
        self.service.QUERY_LOG.record({"user": "anna", "kind": "ask"})
        for info, role in ((directory.DirectoryInfo("token", (), ("Leader",)), "leader"),
                           (directory.DirectoryInfo("token", (), ("analyst",)), "analyst"),
                           (directory.DirectoryInfo("token", (), ("leader",), error="down"),
                            "analyst")):
            with patch.object(self.service, "DIRECTORY", Directory(info)):
                status, out = self.request("ui/querylog", {}, user="vera")
            self.assertEqual((200, role), (status, out["role"]), info.roles)
        with patch.object(self.service, "DIRECTORY", Directory(
                directory.DirectoryInfo("token", (), ("leader",)))), \
                patch.object(self.service, "LEADER_ROLES", frozenset()):
            self.assertEqual("analyst", self.request("ui/querylog", {}, user="vera")[1]["role"])
        self.assertEqual(frozenset({"leader"}), self.service._leader_roles(None))
        self.assertEqual(frozenset(), self.service._leader_roles(" OFF "))
        self.assertEqual(frozenset({"vezeto", "leader"}),
                         self.service._leader_roles("Vezeto, leader"))


if __name__ == "__main__":
    unittest.main()
