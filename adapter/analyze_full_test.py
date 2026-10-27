"""E10a: whole-document analysis of a file too large for one model call.

  Push-Location adapter; python -m unittest analyze_full_test; Pop-Location
"""
import json
import os
import tempfile
import unittest
from unittest.mock import patch

import analysis_checkpoint
import deep
import llm

DRIVE = "/storage/drives/finance"
PATH = DRIVE + "/contract.txt"


def chunks(n):
    return [{"chunk_id": "c%d" % i, "corpus_id": "k", "source_path": PATH, "page_number": i // 3 + 1,
             "text": "%d. pont: a bérlő a díjat havonta fizeti, késedelem esetén kamat jár." % i}
            for i in range(n)]


class AnalyzeFullTest(unittest.TestCase):
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
        self.service.SESSIONS = session.SessionManager()
        for target, value in (("GENERATE", "on"), ("TITLES", False)):
            guard = patch.object(llm, target, value)
            guard.start()
            self.addCleanup(guard.stop)
        self.calls = []
        chat = patch.object(llm, "_chat", side_effect=self.chat)
        chat.start()
        self.addCleanup(chat.stop)
        self.requests = []
        self.document = chunks(30)
        self.user = "tester"
        analysis = tempfile.TemporaryDirectory()
        self.addCleanup(analysis.cleanup)
        store = patch.object(self.service, "ANALYSIS_CHECKPOINTS",
                             analysis_checkpoint.CheckpointStore(analysis.name))
        store.start()
        self.addCleanup(store.stop)

    def chat(self, system, user, **opts):
        self.calls.append((system, opts))
        if system == deep.SECTION_SYSTEM:
            return json.dumps({"findings": [{"excerpt": 1, "quote": "a bérlő a díjat havonta fizeti",
                                             "note": "Havi díjfizetés."}]}), 0, 0
        return ("A szerződés havi díjfizetést ír elő, késedelem esetén kamattal [#2]. "
                "Ez a dokumentum teljes elemzése, minden része alapján készült."), 0, 0

    def rag(self, path, body):
        self.requests.append((path, body))
        budget = body["max_context_tokens"]
        kept = self.document[:budget] if budget < len(self.document) else self.document
        return {"contexts": kept,
                "document": {"source_path": PATH, "title": "contract.txt",
                             "chunks_total": len(self.document), "chunks_used": len(kept),
                             "truncated": len(kept) < len(self.document)}}

    def analyze(self):
        with patch.object(self.service, "rag_post_json", side_effect=self.rag):
            return self.service._answer(self.user, DRIVE, "Elemezd a szerződést", 5, "Hungarian",
                                        action="analyze", files=[PATH])

    def sections(self):
        return [opts for system, opts in self.calls if system == deep.SECTION_SYSTEM]

    def test_a_document_over_the_window_is_read_in_full_parts(self):
        with patch.object(self.service, "ANALYZE_MAX_CHARS", 600), \
                patch.object(deep, "DOC_BATCH_CHARS", 400):
            result = self.analyze()
        self.assertEqual(self.service.ANALYZE_DOC_MAX_TOKENS,
                         self.requests[0][1]["max_context_tokens"])
        sections = self.sections()
        self.assertEqual(result["deep"]["groups"], len(sections))
        self.assertGreater(len(sections), 3)
        self.assertTrue(all(o["num_ctx"] == self.service.ANALYZE_NUM_CTX for o in sections))
        final = self.calls[-1]
        self.assertNotEqual(deep.SECTION_SYSTEM, final[0])
        self.assertEqual(self.service.ANALYZE_NUM_CTX, final[1]["num_ctx"])
        self.assertEqual(("document", 30, False), (result["deep"]["kind"], result["deep"]["read"],
                                                   result["deep"]["partial"]))
        self.assertIn("Teljes lefedetts\u00e9g\u0171 elemz\u00e9s: a(z) 30 r\u00e9szletet "
                      "(contract.txt)", result["answer"])
        self.assertNotIn("nem f\u00e9rt", result["answer"])
        # The coverage note is no source: the footer lists the document only.
        sources = next(line for line in result["answer"].split("\n") if "(Sources)" in line)
        self.assertIn("contract.txt, p.", sources)
        self.assertNotIn("?", sources)
        self.assertTrue(all(c["path"] in (None, PATH) for c in result["citations"]))
        self.assertTrue(any(c["path"] == PATH for c in result["citations"]))
        # The passages come first; the coverage note is the last context.
        self.assertEqual(PATH, result["citations"][0]["path"])
        self.assertIsNone(result["citations"][-1]["path"])

    def test_an_interrupted_analysis_continues_where_it_stopped(self):
        down = {"after": 2}

        def chat(system, user, **opts):
            if system == deep.SECTION_SYSTEM:
                if down["after"] == 0:
                    raise OSError("model server gone")  # e.g. the adapter going down
                down["after"] -= 1
            return self.chat(system, user, **opts)

        with patch.object(self.service, "ANALYZE_MAX_CHARS", 600), \
                patch.object(deep, "DOC_BATCH_CHARS", 400), \
                patch.object(llm, "_chat", side_effect=chat):
            with self.assertRaises(OSError):
                self.analyze()
            self.calls.clear()
            down["after"] = -1
            result = self.analyze()
            read_again = len(self.sections())
            self.calls.clear()
            with patch.object(self, "user", "someone-else"):
                other = self.analyze()
        groups = result["deep"]["groups"]
        self.assertEqual((2, groups - 2), (result["deep"]["resumed"], read_again))
        self.assertFalse(result["deep"]["partial"])
        self.assertIn("2 r\u00e9szt a(z) %d k\u00f6z\u00fcl" % groups, result["answer"])
        self.assertEqual((0, groups), (other["deep"]["resumed"], len(self.sections())))

    def test_a_small_document_keeps_the_single_call(self):
        self.document = chunks(4)
        result = self.analyze()
        self.assertEqual([], self.sections())
        self.assertEqual(1, len(self.calls))
        self.assertNotIn("deep", result)
        self.assertNotIn("Teljes lefedetts\u00e9g\u0171", result["answer"])

    def test_switched_off_the_window_cuts_as_before(self):
        with patch.object(self.service, "ANALYZE_FULL", False), \
                patch.object(self.service, "ANALYZE_MAX_CHARS", 600):
            result = self.analyze()
        self.assertEqual(self.service.ANALYZE_MAX_CONTEXT_TOKENS,
                         self.requests[0][1]["max_context_tokens"])
        self.assertEqual([], self.sections())
        self.assertNotIn("deep", result)


if __name__ == "__main__":
    unittest.main()
