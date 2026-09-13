import os
import base64
import json
import tempfile
import threading
import urllib.error
import urllib.request
import unittest
from http.server import ThreadingHTTPServer
from unittest.mock import patch

import chat_policy
import llm


class ChatPolicyTest(unittest.TestCase):
    def test_defaults_and_switching(self):
        for policy, default, switch in (
            ("locked_grounded", "grounded", False),
            ("locked_hybrid", "hybrid", False),
            ("selectable_grounded", "grounded", True),
            ("selectable_hybrid", "hybrid", True),
            ("invalid", "grounded", False),
        ):
            with self.subTest(policy=policy), patch.dict(os.environ, {"ADAPTER_CHAT_POLICY": policy}):
                self.assertEqual(chat_policy.resolve(), default)
                self.assertEqual(chat_policy.settings()["allow_switch"], switch)
                for profile in ("grounded", "hybrid"):
                    if switch or profile == default:
                        self.assertEqual(chat_policy.resolve(profile), profile)
                    else:
                        with self.assertRaises(PermissionError):
                            chat_policy.resolve(profile)
                for action in ("summary", "report", "tracking", "memo", "presentation", "search", "analyze"):
                    self.assertEqual(chat_policy.resolve("hybrid", action), "grounded")

    def test_absent_policy_defaults_to_locked_hybrid(self):
        with patch.dict(os.environ):
            os.environ.pop("ADAPTER_CHAT_POLICY", None)
            self.assertEqual(chat_policy.settings(), {
                "policy": "locked_hybrid", "default_profile": "hybrid", "allow_switch": False,
            })
            self.assertEqual(chat_policy.resolve(), "hybrid")
            self.assertEqual(chat_policy.resolve("grounded", "tracking"), "grounded")

    def test_invalid_profile(self):
        with self.assertRaises(ValueError):
            chat_policy.resolve("unknown")

    def test_history_isolation_preserves_legacy(self):
        self.assertEqual(chat_policy.history_scope("drive", "grounded"), "drive")
        self.assertNotEqual(chat_policy.history_scope("drive", "hybrid"), "drive")
        self.assertNotEqual(chat_policy.history_scope("drive", "hybrid"),
                            chat_policy.history_scope("other", "hybrid"))

    def test_hybrid_without_documents_calls_model(self):
        with patch.object(llm, "GENERATE", "on"), patch.object(
            llm, "_chat", return_value=("A general explanation with 42 examples.", 0, 0)
        ) as generate:
            answer, backend = llm.generate("Explain a concept", [], profile="hybrid")
            self.assertIn("42", answer)
            self.assertIn("hybrid", backend)
            self.assertIn("NOT verified company evidence", generate.call_args.args[0])

    def test_hybrid_conversation_guidance_preserves_evidence_boundaries(self):
        history = [{"role": "user", "content": "The meeting is tomorrow; keep it brief."}]
        for contexts in ([], [{"text": "Project review: owner unassigned.", "source_path": "review.txt"}]):
            with self.subTest(has_documents=bool(contexts)), patch.object(llm, "GENERATE", "on"), patch.object(
                llm, "_chat", return_value=("A useful answer.", 0, 0)
            ) as generate:
                llm.generate("Help me prepare for the meeting", contexts, history=history,
                             lang="Hungarian", profile="hybrid")
                system = generate.call_args.args[0]
                for rule in (
                    "Answer clear requests first",
                    "one or two concrete next steps or options",
                    "Ask one focused follow-up question only",
                    "Use details already provided",
                    "Present recommendations as proposals",
                    "Do not invent company circumstances",
                    "Do not force a suggestion or question into every response",
                    "Respect requests for a short answer or no follow-up questions",
                    "Only company-specific facts",
                    "NOT verified company evidence",
                    "Do not invent citations",
                    "cannot perform external actions",
                    "Always answer in Hungarian",
                ):
                    self.assertIn(rule, system)
                self.assertEqual(generate.call_args.kwargs["history"], history)

    def test_hybrid_general_question_after_refusal_with_unrelated_documents(self):
        history = [
            {"role": "user", "content": "What is our company's revenue?"},
            {"role": "assistant", "content": "There is no data for this in the documents."},
        ]
        contexts = [{"text": "Internal expense policy.", "source_path": "policy.txt"}]
        with patch.object(llm, "GENERATE", "on"), patch.object(
            llm, "_chat", return_value=("Sunlight is scattered by the atmosphere.", 0, 0)
        ) as generate:
            answer, backend = llm.generate("Why is the sky blue?", contexts,
                                           history=history, profile="hybrid", chat_only=True)
            system, request = generate.call_args.args
            self.assertIn("general knowledge by default", system)
            self.assertIn("Only company-specific facts", system)
            self.assertIn("A previous company question or refusal", system)
            self.assertIn("NOT disproof", system)
            self.assertIn("historical job title", system)
            self.assertTrue(request.startswith("CURRENT REQUEST: Why is the sky blue?"))
            self.assertIn("Internal expense policy.", request)
            self.assertEqual(generate.call_args.kwargs["history"], history)
            self.assertIn("Sunlight", answer)
            self.assertIn("hybrid", backend)

    def test_condense_cannot_drop_explicit_filename(self):
        question = "Check Szeredy in IFUA_VVT_adatelemzes_szerzodes_20200615.docx"
        history = [{"role": "user", "content": "Who is the director?"}]
        with patch.object(llm, "CHAT", True), patch.object(llm, "GENERATE", "on"), patch.object(
            llm, "_chat", return_value=("Where does Szeredy work?", 0, 0)
        ):
            self.assertEqual(llm.condense(question, history), (question, False))
        self.assertIn("disputed claim, never CHAT_ONLY", llm._CONDENSE_INSTRUCTION)

    def test_source_files_preserves_quoted_spaces_and_full_paths(self):
        self.assertEqual(llm.source_files('Check "annual report.docx" and /storage/drives/hr/person.pdf.'),
                         ["annual report.docx", "/storage/drives/hr/person.pdf"])

    def test_source_history_survives_reload_without_aliasing(self):
        import session
        with tempfile.TemporaryDirectory() as directory:
            manager = session.SessionManager(directory)
            sources = [{"chunk_id": "chunk-a", "corpus_id": "finance"}]
            manager.record("test", "scope", "Who?", "A person [#1]", sources=sources)
            sources[0]["chunk_id"] = "changed"
            restored = session.SessionManager(directory)
            history = restored.history("test", "scope")
            self.assertEqual(history[-1]["sources"][0]["chunk_id"], "chunk-a")
            self.assertEqual(session.followup_evidence("Earlier you said otherwise", history, ["finance"]),
                             ["chunk-a"])
            self.assertEqual(session.followup_evidence("Earlier you said otherwise", history, ["hr"]), [])
            self.assertEqual(session.followup_evidence("Why is the sky blue?", history, ["finance"]), [])
            history[-1]["sources"][0]["chunk_id"] = "changed again"
            self.assertEqual(restored.history("test", "scope")[-1]["sources"][0]["chunk_id"], "chunk-a")

    def test_grounded_without_documents_never_calls_model(self):
        with patch.object(llm, "GENERATE", "on"), patch.object(llm, "_chat") as generate:
            _, backend = llm.generate("Company revenue?", [])
            self.assertEqual(backend, "no-context")
            generate.assert_not_called()

    def test_task_cannot_use_hybrid_generation(self):
        with patch.object(llm, "GENERATE", "on"), patch.object(llm, "_chat") as generate:
            _, backend = llm.generate("Write report", [], task="report", profile="hybrid")
            self.assertEqual(backend, "no-context")
            generate.assert_not_called()


class ProfilePipelineTest(unittest.TestCase):
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
        import jobstore
        self.service.SESSIONS = session.SessionManager()
        job_dir = tempfile.TemporaryDirectory(dir=self.temp.name)
        self.addCleanup(job_dir.cleanup)
        with patch.dict(os.environ, {"ADAPTER_JOBS_DIR": job_dir.name}):
            self.service.JOBS = jobstore.JobStore.from_env()
        self.contexts = [{"text": "The company revenue was 12 million.",
                          "source_path": "/storage/drives/finance/report.txt",
                          "page_number": 1, "score": 0.7}]
        self.model = patch.object(llm, "_chat", return_value=("General advice: 42 examples.", 0, 0))
        self.generate = self.model.start()
        self.addCleanup(self.model.stop)
        for target, value in (("GENERATE", "on"),):
            guard = patch.object(llm, target, value)
            guard.start()
            self.addCleanup(guard.stop)
        condense = patch.object(llm, "condense", side_effect=lambda query, history, lang: (query, False))
        condense.start()
        self.addCleanup(condense.stop)
        rag = patch.object(self.service, "rag_post_json", side_effect=lambda path, body: {"contexts": self.contexts})
        self.rag = rag.start()
        self.addCleanup(rag.stop)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self.service.Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def request(self, path, body):
        headers = {"Content-Type": "application/json", "VVS-User": "profile-test",
                   "VVS-Drive": base64.urlsafe_b64encode(b"/storage/drives/finance").decode("ascii")}
        request = urllib.request.Request(
            "http://127.0.0.1:%d/api/v1/ui/%s" % (self.server.server_port, path),
            data=json.dumps(body).encode("utf-8"), headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as error:
            return error.code, json.load(error)

    def test_enforcement_on_both_endpoints(self):
        with patch.dict(os.environ, {"ADAPTER_CHAT_POLICY": "locked_grounded"}):
            for path in ("query", "ask"):
                with self.subTest(path=path):
                    self.assertEqual(self.request(path, {"query": "Hello", "profile": "hybrid"})[0], 403)
                    self.assertEqual(self.request(path, {"query": "Hello", "profile": "bad"})[0], 400)
            self.generate.assert_not_called()

    def test_locked_hybrid_defaults_and_action_exception(self):
        with patch.dict(os.environ, {"ADAPTER_CHAT_POLICY": "locked_hybrid"}):
            for path in ("query", "ask"):
                with self.subTest(path=path):
                    self.assertEqual(self.request(path, {"query": "Hello", "profile": "grounded"})[0], 403)
            code, result = self.request("query", {"query": "Explain a concept"})
            self.assertEqual(code, 200)
            self.assertEqual(result["profile"], "hybrid")
            self.assertIsNone(result["confidence"])
            code, result = self.request("query", {"query": "/tracking test project", "profile": "hybrid"})
            self.assertEqual(code, 200)
            self.assertEqual(result["profile"], "grounded")
            self.assertEqual(self.generate.call_args.kwargs["history"], [])

    def test_hybrid_numbers_audit_and_history_isolation(self):
        with patch.dict(os.environ, {"ADAPTER_CHAT_POLICY": "selectable_hybrid"}):
            code, result = self.request("query", {"query": "Explain a concept"})
            self.assertEqual(code, 200)
            self.assertEqual(result["profile"], "hybrid")
            self.assertIsNone(result["confidence"])
            self.assertIn("42", result["answer"])
            self.assertIn("Profile: hybrid", result["answer"])
            self.assertTrue(result["audit_id"])
            self.assertEqual(result["citations"], [])
            self.request("query", {"query": "Give advice"})
            self.assertIn("42", self.generate.call_args.kwargs["history"][-1]["content"])
            self.request("query", {"query": "Company facts", "profile": "grounded"})
            self.assertEqual(self.generate.call_args.kwargs["history"], [])
            self.request("query", {"query": "Write report", "action": "report", "profile": "hybrid"})
            history = self.generate.call_args.kwargs["history"]
            self.assertNotIn("General advice", json.dumps(history))

    def test_scoped_retrieval_and_citation_identity(self):
        self.contexts.append({**self.contexts[0], "text": "Another fact", "page_number": 2})
        self.generate.return_value = ("Document fact [#2], general advice [#99].", 0, 0)
        with patch.dict(os.environ, {"ADAPTER_CHAT_POLICY": "selectable_grounded"}):
            code, result = self.request("query", {"query": "Mixed question", "profile": "hybrid"})
            self.assertEqual(code, 200)
            self.assertEqual([citation["ref"] for citation in result["citations"]], [2])
            self.assertNotIn("[#99]", result["answer"])
            self.assertEqual(len(self.rag.call_args.args[1]["corpus_ids"]), 1)
            self.assertEqual(self.request("query", {"query": "Secret", "profile": "hybrid",
                                                   "drives": ["/storage/drives/hr"]})[0], 403)

    def test_async_profile_and_history_snapshot(self):
        with patch.dict(os.environ, {"ADAPTER_CHAT_POLICY": "selectable_hybrid"}):
            code, submitted = self.request("ask", {"query": "Hello"})
            self.assertEqual(code, 202, submitted)
            job_id = submitted["job_id"]
            job = self.service.JOBS.wait("profile-test", "/storage/drives/finance", job_id, 5)
            self.assertEqual(job["status"], "done")
            self.assertEqual(job["request"]["profile"], "hybrid")
            self.assertEqual(job["request"]["history"], [])
            self.assertEqual(job["result"]["profile"], "hybrid")
        with patch.dict(os.environ, {"ADAPTER_CHAT_POLICY": "locked_grounded"}):
            code, polled = self.request("poll", {"job_id": job_id, "timeout": 0})
            self.assertEqual(code, 200)
            self.assertEqual(polled["profile"], "hybrid")

    def test_explicit_filename_is_sent_before_retrieval(self):
        self.contexts = []
        code, result = self.request("query", {"query": 'Check "annual report.docx"'})
        self.assertEqual(code, 200)
        self.assertEqual(self.rag.call_args.args[1]["source_paths"], ["annual report.docx"])
        self.assertEqual(result["citations"], [])

    def test_cited_evidence_reaches_async_snapshot_but_not_new_topic(self):
        import scope
        corpus_id = scope.resolve("/storage/drives/finance").corpus_id
        self.contexts[0].update(chunk_id="evidence-a", corpus_id=corpus_id)
        self.generate.return_value = ("The document supports this fact [#1].", 0, 0)
        with patch.dict(os.environ, {"ADAPTER_CHAT_POLICY": "locked_hybrid"}):
            self.assertEqual(self.request("query", {"query": "Who is the director?"})[0], 200)
            code, submitted = self.request("ask", {"query": "Earlier you said otherwise. Why?"})
            self.assertEqual(code, 202)
            job = self.service.JOBS.wait("profile-test", "/storage/drives/finance", submitted["job_id"], 5)
            self.assertEqual(job["status"], "done")
            self.assertEqual(job["request"]["history"][-1]["sources"][0]["chunk_id"], "evidence-a")
            self.assertEqual(self.rag.call_args.args[1]["evidence_chunk_ids"], ["evidence-a"])
            self.assertEqual(self.request("query", {"query": "Why is the sky blue?"})[0], 200)
            self.assertEqual(self.rag.call_args.args[1]["evidence_chunk_ids"], [])

    def test_retrieval_failure_is_not_general_knowledge_fallback(self):
        self.rag.side_effect = self.service.RagError(503, {"ok": False, "error": "retrieval down"})
        with patch.dict(os.environ, {"ADAPTER_CHAT_POLICY": "selectable_hybrid"}):
            self.assertEqual(self.request("query", {"query": "Company facts"})[0], 503)
            self.generate.assert_not_called()


if __name__ == "__main__":
    unittest.main()