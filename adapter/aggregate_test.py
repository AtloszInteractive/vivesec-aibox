"""Counts and totals from a few retrieved passages are said to be partial.

  Push-Location adapter; python -m unittest aggregate_test; Pop-Location
"""
import os
import tempfile
import unittest
from unittest.mock import patch

import aggregate
import llm

DRIVE = "/storage/drives/finance"


def passage(i, path):
    return {"chunk_id": "c%d" % i, "corpus_id": "k", "source_path": path, "page_number": 1,
            "score": 0.9, "text": "Hívásnapló 2026-07-0%d: 4 hívás." % (i + 1),
            "metadata": {}}


class CuesTest(unittest.TestCase):
    def test_counts_totals_and_all_are_recognised_in_every_language(self):
        for question in ("Hány hívás volt 2026 júliusában?", "Mennyi a nyitott rendelések összege?",
                         "Sorold fel az összes PX-200 rendelést", "How many calls were there?",
                         "What is the total quantity?", "Hvor mange opkald var der i alt?",
                         "Wie viele Anrufe gab es insgesamt?", "Mi az átlagos selejtarány?"):
            self.assertTrue(aggregate.asks_for_count(question), question)

    def test_other_questions_are_left_alone(self):
        for question in ("Mi történt az L2 gyártósoron 2026-08-14-én?", "Who signed the contract?",
                         "Hanyagság miatt mi történt?", "Account manager of Kiss Anna?"):
            self.assertFalse(aggregate.asks_for_count(question), question)

    def test_only_a_graph_count_with_facts_counts_as_complete(self):
        self.assertTrue(aggregate.counted_by_graph({"calls": [{"tool": "count", "facts": 3}]}))
        self.assertFalse(aggregate.counted_by_graph({"calls": [{"tool": "count", "facts": 0}]}))
        self.assertFalse(aggregate.counted_by_graph({"calls": [{"tool": "relations", "facts": 9}]}))
        self.assertFalse(aggregate.counted_by_graph(None))

    def test_the_note_counts_retrieved_passages_and_their_documents_only(self):
        contexts = [passage(0, "/a.csv"), passage(1, "/a.csv"), passage(2, "/b.csv"),
                    {"chunk_id": None, "source_path": "/c.txt", "text": "graph fact"}]
        note = aggregate.partial_note(contexts, "Hungarian")
        self.assertIn("3 visszakeresett r\u00e9szleten alapul (2 dokumentumb\u00f3l)", note)
        self.assertIsNone(aggregate.partial_note([{"chunk_id": None, "text": "x"}], "English"))


class AnswerTest(unittest.TestCase):
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
        patches = [patch.object(llm, "GENERATE", "on"), patch.object(llm, "TITLES", False),
                   patch.object(llm, "_chat", return_value=(
                       "J\u00falius\u00e1ban \u00f6sszesen 4 h\u00edv\u00e1s volt [#1].", 0, 0))]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def ask(self, question):
        contexts = [passage(0, DRIVE + "/calls/2026-07-01.csv"),
                    passage(1, DRIVE + "/calls/2026-07-02.csv")]
        with patch.object(self.service, "rag_post_json",
                          return_value={"contexts": contexts}):
            return self.service._answer("lars", DRIVE, question, 5, "Hungarian")

    def test_a_count_from_a_search_is_said_to_be_partial_and_not_green(self):
        result = self.ask("H\u00e1ny h\u00edv\u00e1s volt 2026 j\u00falius\u00e1ban?")
        self.assertIn("> Sz\u00e1mol\u00e1si megjegyz\u00e9s: ez a v\u00e1lasz 2 "
                      "visszakeresett r\u00e9szleten alapul (2 dokumentumb\u00f3l)",
                      result["answer"])
        self.assertLessEqual(result["confidence"]["score"], 84)
        self.assertNotEqual("green", result["confidence"]["band"])
        self.assertIn("partial_count", result["confidence"]["degraded"])

    def test_a_question_that_counts_nothing_gets_no_note(self):
        result = self.ask("Mi t\u00f6rt\u00e9nt 2026-07-01-j\u00e9n?")
        self.assertNotIn("Sz\u00e1mol\u00e1si megjegyz\u00e9s", result["answer"])
        self.assertNotIn("partial_count", result["confidence"]["degraded"])


if __name__ == "__main__":
    unittest.main()
