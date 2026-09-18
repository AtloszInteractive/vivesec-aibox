"""HTTP tests for the persistent conversation threads (F1): the five
/api/v1/ui/conversations/* endpoints and `conversation_id` on query/ask."""
import base64
import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest.mock import patch

import llm


def vvs(path):
    return base64.urlsafe_b64encode(path.encode("utf-8")).decode("ascii")


class ConversationsHttpTest(unittest.TestCase):
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
        import jobstore
        import session
        self.session_dir = tempfile.TemporaryDirectory(dir=self.temp.name)
        self.addCleanup(self.session_dir.cleanup)
        self.service.SESSIONS = session.SessionManager(spill_dir=self.session_dir.name)
        job_dir = tempfile.TemporaryDirectory(dir=self.temp.name)
        self.addCleanup(job_dir.cleanup)
        with patch.dict(os.environ, {"ADAPTER_JOBS_DIR": job_dir.name}):
            self.service.JOBS = jobstore.JobStore.from_env()
        self.contexts = [{"text": "The company revenue was 12 million.",
                          "source_path": "/storage/drives/finance/report.txt",
                          "chunk_id": "chunk-1", "corpus_id": "finance",
                          "page_number": 1, "score": 0.7}]
        self.titles = []

        def fake_chat(system, user, **kwargs):
            if "title of 3 to 7 words" in system:
                self.titles.append(user)
                return ("Revenue overview", 0, 0)
            return ("The revenue was 12 million [#1].", 0, 0)

        for target, value in (("GENERATE", "on"), ("TITLES", True)):
            guard = patch.object(llm, target, value)
            guard.start()
            self.addCleanup(guard.stop)
        model = patch.object(llm, "_chat", side_effect=fake_chat)
        self.chat = model.start()
        self.addCleanup(model.stop)
        condense = patch.object(llm, "condense", side_effect=lambda query, history, lang: (query, False))
        condense.start()
        self.addCleanup(condense.stop)
        rag = patch.object(self.service, "rag_post_json",
                           side_effect=lambda path, body: {"contexts": self.contexts})
        rag.start()
        self.addCleanup(rag.stop)
        policy = patch.dict(os.environ, {"ADAPTER_CHAT_POLICY": "selectable_hybrid"})
        policy.start()
        self.addCleanup(policy.stop)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self.service.Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def request(self, path, body, user="thread-user", drive="/storage/drives/finance", method="POST"):
        headers = {"Content-Type": "application/json", "VVS-User": user, "VVS-Drive": vvs(drive)}
        url = "http://127.0.0.1:%d%s" % (self.server.server_port,
                                          path if path.startswith("/") else "/api/v1/ui/" + path)
        request = urllib.request.Request(
            url,
            data=json.dumps(body).encode("utf-8") if method == "POST" else None,
            headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as error:
            return error.code, json.load(error)

    def wait_title(self):
        for _ in range(50):
            if self.titles:
                for _ in range(50):
                    listed = self.request("conversations/list", {})[1]["conversations"]
                    if any(entry.get("title_source") == "auto" for entry in listed):
                        return
                    threading.Event().wait(0.02)
            threading.Event().wait(0.02)

    # -- CRUD ---------------------------------------------------------------------------
    def test_create_list_get_rename_delete(self):
        code, body = self.request("conversations/list", {})
        self.assertEqual((code, body["conversations"]), (200, []))
        code, created = self.request("conversations/create", {"title": "Budget talk"})
        self.assertEqual(code, 200)
        cid = created["conversation"]["id"]
        self.assertEqual(created["conversation"]["title"], "Budget talk")
        self.assertEqual(created["conversation"]["turns"], [])
        code, listed = self.request("conversations/list", {})
        self.assertEqual([c["id"] for c in listed["conversations"]], [cid])
        code, got = self.request("conversations/get", {"conversation_id": cid})
        self.assertEqual((code, got["conversation"]["id"]), (200, cid))
        code, renamed = self.request("conversations/rename", {"conversation_id": cid, "title": "Q2 budget"})
        self.assertEqual((code, renamed["title"]), (200, "Q2 budget"))
        self.assertEqual(self.request("conversations/rename", {"conversation_id": cid, "title": " "})[0], 400)
        code, deleted = self.request("conversations/delete", {"conversation_id": cid})
        self.assertEqual((code, deleted["conversation_id"]), (200, cid))
        self.assertEqual(self.request("conversations/get", {"conversation_id": cid})[0], 404)
        self.assertEqual(self.request("conversations/delete", {"conversation_id": cid})[0], 404)
        self.assertEqual(self.request("conversations/list", {})[1]["conversations"], [])

    def test_endpoints_need_vvs_drive_and_are_locked_guarded(self):
        request = urllib.request.Request(
            "http://127.0.0.1:%d/api/v1/ui/conversations/list" % self.server.server_port,
            data=b"{}", headers={"Content-Type": "application/json"}, method="POST")
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(request, timeout=5)
        self.assertEqual(caught.exception.code, 400)
        self.assertEqual(self.request("conversations/get", {})[0], 404)
        self.assertEqual(self.request("conversations/get", {"conversation_id": "../x"})[0], 404)

    # -- ACL: other user / scope / profile see nothing ---------------------------------------
    def test_threads_invisible_across_user_scope_and_profile(self):
        cid = self.request("conversations/create", {"title": "Private"})[1]["conversation"]["id"]
        self.request("query", {"query": "How much revenue?", "conversation_id": cid})
        for kwargs in ({"user": "someone-else"}, {"drive": "/storage/drives/hr"}):
            with self.subTest(**kwargs):
                self.assertEqual(self.request("conversations/list", {}, **kwargs)[1]["conversations"], [])
                self.assertEqual(self.request("conversations/get", {"conversation_id": cid}, **kwargs)[0], 404)
                self.assertEqual(self.request("conversations/rename", {"conversation_id": cid, "title": "x"}, **kwargs)[0], 404)
                self.assertEqual(self.request("conversations/delete", {"conversation_id": cid}, **kwargs)[0], 404)
                self.assertEqual(self.request("query", {"query": "Hi", "conversation_id": cid}, **kwargs)[0], 404)
                self.assertEqual(self.request("ask", {"query": "Hi", "conversation_id": cid}, **kwargs)[0], 404)
        # Created under the default (hybrid) profile -> not a grounded thread.
        self.assertEqual(self.request("conversations/list", {"profile": "grounded"})[1]["conversations"], [])
        self.assertEqual(self.request("conversations/get", {"conversation_id": cid, "profile": "grounded"})[0], 404)
        self.assertEqual(self.request("query", {"query": "Hi", "conversation_id": cid, "profile": "grounded"})[0], 404)
        # A quick action is always grounded, so it cannot address a hybrid thread either.
        self.assertEqual(self.request("query", {"query": "/report status", "conversation_id": cid})[0], 404)
        # The owner still sees it, with the exchange recorded.
        code, got = self.request("conversations/get", {"conversation_id": cid})
        self.assertEqual(code, 200)
        self.assertEqual([t["content"] for t in got["conversation"]["turns"]][:1], ["How much revenue?"])

    # -- query / ask address a thread ---------------------------------------------------------
    def test_query_records_into_named_thread_not_default(self):
        cid = self.request("conversations/create", {})[1]["conversation"]["id"]
        code, result = self.request("query", {"query": "How much revenue?", "conversation_id": cid})
        self.assertEqual(code, 200)
        self.assertEqual(result["conversation_id"], cid)
        self.assertIn("12 million", result["answer"])
        code, got = self.request("conversations/get", {"conversation_id": cid})
        turns = got["conversation"]["turns"]
        self.assertEqual([t["role"] for t in turns], ["user", "assistant"])
        self.assertEqual(turns[0]["content"], "How much revenue?")
        self.assertEqual(turns[1]["sources"][0]["chunk_id"], "chunk-1")
        self.assertNotIn("Audit ID", turns[1]["content"])
        # The default thread is untouched ...
        self.assertEqual(self.request("conversations/get", {"conversation_id": "default"})[0], 404)
        # ... and a follow-up in the thread gets the thread's history.
        self.wait_title()
        self.request("query", {"query": "And the plan?", "conversation_id": cid})
        history = self.chat.call_args.kwargs["history"]
        self.assertEqual([t["content"] for t in history][:1], ["How much revenue?"])
        code, listed = self.request("conversations/list", {})
        self.assertEqual(listed["conversations"][0]["turn_count"], 2)

    def test_default_thread_behaviour_unchanged_without_id(self):
        code, result = self.request("query", {"query": "How much revenue?"})
        self.assertEqual(code, 200)
        self.assertNotIn("conversation_id", result)
        self.wait_title()
        code, again = self.request("query", {"query": "How much revenue?", "conversation_id": "default"})
        self.assertEqual(code, 200)
        self.assertNotIn("conversation_id", again)
        history = self.chat.call_args.kwargs["history"]
        self.assertEqual(len(history), 2)
        code, listed = self.request("conversations/list", {})
        self.assertEqual([c["id"] for c in listed["conversations"]], ["default"])
        self.assertEqual(listed["conversations"][0]["turn_count"], 2)
        # An unknown id is rejected before any model call.
        calls = self.chat.call_count
        self.assertEqual(self.request("query", {"query": "x", "conversation_id": "0123456789abcdef"})[0], 404)
        self.assertEqual(self.chat.call_count, calls)

    def test_ask_job_carries_the_thread(self):
        cid = self.request("conversations/create", {})[1]["conversation"]["id"]
        code, submitted = self.request("ask", {"query": "How much revenue?", "conversation_id": cid})
        self.assertEqual(code, 202, submitted)
        self.assertEqual(submitted["conversation_id"], cid)
        job = self.service.JOBS.wait("thread-user", "/storage/drives/finance", submitted["job_id"], 5)
        self.assertEqual(job["status"], "done")
        self.assertEqual(job["request"]["conversation_id"], cid)
        self.assertEqual(job["result"]["conversation_id"], cid)
        code, polled = self.request("poll", {"job_id": submitted["job_id"], "timeout": 0})
        self.assertEqual((code, polled["conversation_id"]), (200, cid))
        got = self.request("conversations/get", {"conversation_id": cid})[1]["conversation"]
        self.assertEqual(got["turn_count"], 1)

    # -- titles ---------------------------------------------------------------------------------
    def test_first_exchange_titles_the_thread(self):
        cid = self.request("conversations/create", {})[1]["conversation"]["id"]
        self.request("query", {"query": "How much revenue did we make in Q2?", "conversation_id": cid})
        self.wait_title()
        got = self.request("conversations/get", {"conversation_id": cid})[1]["conversation"]
        self.assertEqual(got["title"], "Revenue overview")
        self.assertEqual(got["title_source"], "auto")
        self.assertEqual(len(self.titles), 1)
        self.assertIn("How much revenue", self.titles[0])
        # Later exchanges do not re-title.
        self.request("query", {"query": "And Q3?", "conversation_id": cid})
        self.assertEqual(len(self.titles), 1)

    def test_title_falls_back_when_model_is_useless(self):
        self.chat.side_effect = lambda system, user, **kwargs: (
            ("", 0, 0) if "title of 3 to 7 words" in system else ("Answer [#1].", 0, 0))
        cid = self.request("conversations/create", {})[1]["conversation"]["id"]
        self.request("query", {"query": "What is the covenant headroom?", "conversation_id": cid})
        threading.Event().wait(0.1)
        got = self.request("conversations/get", {"conversation_id": cid})[1]["conversation"]
        self.assertEqual(got["title"], "What is the covenant headroom?")
        self.assertEqual(got["title_source"], "fallback")

    def test_status_reports_thread_store(self):
        code, status = self.request("/api/v1/status", {})
        self.assertEqual(code, 200)
        self.assertIn("max_per_scope", status["sessions"])
        self.assertIn("conversations_dir", status["sessions"])


if __name__ == "__main__":
    unittest.main()
