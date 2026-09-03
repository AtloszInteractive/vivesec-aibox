"""Analysing a document must see the WHOLE document.

The "Analyze with AI" action names one file and asks what is in it. Routing that
through similarity search returns only the passages that happen to match the
question, so anything the question does not mention is analysed as if it were
not there. document_context is the answer: no ranking, every chunk, in reading
order. These tests pin that down -- including the truncation report, because a
silently partial analysis is worse than none.

    VIVESEC_BACKEND=fallback python rag_service/document_context_test.py
"""
import os
import sys
import unittest

os.environ.setdefault("VIVESEC_BACKEND", "fallback")

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "poc"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import store  # noqa: E402

CORPUS = "drive_engineering"
PATH = "/storage/drives/engineering/specs/voltstack2.txt"

# Six pages that share no vocabulary, so a question about one of them cannot
# retrieve the others -- that is what makes the search/full-document gap visible.
PAGES = [
    (1, "root", "The VoltStack2 battery enclosure uses an aluminium frame."),
    (2, "root", "Thermal management relies on a liquid coolant loop."),
    (3, "root", "Firmware updates are signed and delivered over the air."),
    (4, "root", "Warranty covers eight years of normal operation."),
    (5, "root", "Recycling returns ninety percent of the cell mass."),
    (6, "root", "Decommissioning requires a certified disposal partner."),
]


class WholeDocumentRetrievalTest(unittest.TestCase):
    def setUp(self):
        self.store = store.RagStore()
        self.store._index_document(
            corpus_id=CORPUS, tenant_id="default", source_path=PATH,
            title="voltstack2.txt", pages=PAGES, mtime=None, size=None)

    def _texts(self, contexts):
        return [c["text"] for c in contexts]

    # -- the reason the endpoint exists ------------------------------------
    def test_search_misses_pages_the_question_does_not_mention(self):
        contexts, _ = self.store.search_context(
            CORPUS, "aluminium frame enclosure", top_k=2)
        joined = " ".join(self._texts(contexts))
        self.assertNotIn("disposal partner", joined,
                         "search returned the whole document; the gap this "
                         "endpoint closes would not be real")

    def test_document_context_returns_every_chunk(self):
        contexts, doc = self.store.document_context(CORPUS, PATH)
        self.assertTrue(doc["found"])
        self.assertFalse(doc["truncated"])
        self.assertEqual(doc["chunks_total"], doc["chunks_used"])
        joined = " ".join(self._texts(contexts))
        for _, _, text in PAGES:
            self.assertIn(text, joined)

    def test_chunks_come_back_in_reading_order(self):
        contexts, _ = self.store.document_context(CORPUS, PATH)
        pages = [c["page_number"] for c in contexts]
        self.assertEqual(sorted(pages), pages)

    # -- honesty about the bound -------------------------------------------
    def test_a_truncated_read_reports_itself(self):
        contexts, doc = self.store.document_context(
            CORPUS, PATH, max_context_tokens=1)
        self.assertTrue(doc["found"])
        self.assertTrue(doc["truncated"])
        self.assertEqual(1, len(contexts), "the budget must still yield context")
        self.assertLess(doc["chunks_used"], doc["chunks_total"])

    # -- resolution + isolation ---------------------------------------------
    def test_a_bare_filename_resolves_to_the_document(self):
        contexts, doc = self.store.document_context(CORPUS, "voltstack2.txt")
        self.assertTrue(doc["found"])
        self.assertEqual(PATH, doc["source_path"])
        self.assertTrue(contexts)

    def test_another_corpus_cannot_be_reached(self):
        other = "/storage/drives/finance/specs/voltstack2.txt"
        self.store._index_document(
            corpus_id="drive_finance", tenant_id="default", source_path=other,
            title="voltstack2.txt", pages=[(1, "root", "Finance copy only.")],
            mtime=None, size=None)
        contexts, doc = self.store.document_context("drive_finance", "voltstack2.txt")
        self.assertEqual(other, doc["source_path"])
        self.assertIn("Finance copy only.", " ".join(self._texts(contexts)))

    def test_an_unknown_file_is_reported_not_guessed(self):
        contexts, doc = self.store.document_context(CORPUS, "no_such_file.txt")
        self.assertEqual([], contexts)
        self.assertFalse(doc["found"])

    def test_the_context_shape_matches_search_context(self):
        search, _ = self.store.search_context(CORPUS, "aluminium frame", top_k=1)
        document, _ = self.store.document_context(CORPUS, PATH)
        self.assertEqual(set(search[0]), set(document[0]),
                         "the adapter feeds both into the same generator")


if __name__ == "__main__":
    unittest.main(verbosity=2)
