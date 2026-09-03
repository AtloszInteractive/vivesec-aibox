"""A file that yields no text must stay visible, not vanish quietly.

A scanned PDF passes every pre-upload check -- supported type, non-zero size,
extractor present -- and then extracts to nothing. Before this it was stored as
a normal indexed document with zero chunks: counted in /stats, never returned by
any search, and with no record of why. These tests pin down that it is now
recorded with a reason instead.

    python rag_service/skipped_test.py
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "poc"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import store


class ScannedPdfTest(unittest.TestCase):
    """A supported file that extracts to nothing."""

    def setUp(self):
        self.store = store.RagStore()

    def _ingest_empty(self, path="/drive/scanned.txt"):
        # ingest_text with blank text reaches _index_document with no pages,
        # which is exactly what a scanned PDF produces after extraction.
        return self.store.ingest_text("drive_legal", "default", path, "scanned", "   ", {})

    def test_ingest_reports_the_condition(self):
        res = self._ingest_empty()
        self.assertEqual(0, res["chunks"])
        self.assertEqual(store.EMPTY_EXTRACTION_WARNING, res.get("warning"))

    def test_document_is_not_counted_as_indexed(self):
        self._ingest_empty()
        doc = next(iter(self.store._docs.values()))
        self.assertFalse(doc["indexed"])
        self.assertEqual(store.EMPTY_EXTRACTION_WARNING, doc["skip_reason"])

    def test_stats_counts_it_by_reason(self):
        self._ingest_empty()
        stats = self.store.stats()
        self.assertEqual({store.EMPTY_EXTRACTION_WARNING: 1}, stats["skipped"])
        # The file is still known -- it exists on the drive, we just cannot read it.
        self.assertEqual(1, stats["documents"])

    def test_listing_names_the_file_and_the_reason(self):
        self._ingest_empty("/drive/contracts/signed.pdf")
        listed = self.store.skipped_documents()
        self.assertEqual(1, len(listed))
        self.assertEqual("/drive/contracts/signed.pdf", listed[0]["source_path"])
        self.assertEqual(store.EMPTY_EXTRACTION_WARNING, listed[0]["reason"])

    def test_listing_can_be_scoped_to_one_corpus(self):
        self._ingest_empty("/drive/a.pdf")
        self.assertEqual(1, len(self.store.skipped_documents("drive_legal")))
        self.assertEqual([], self.store.skipped_documents("drive_hr"))

    def test_a_readable_document_is_not_listed(self):
        self.store.ingest_text("drive_legal", "default", "/drive/ok.txt", "ok",
                               "this document has actual words in it", {})
        self.assertEqual([], self.store.skipped_documents())
        self.assertEqual({}, self.store.stats()["skipped"])

    def test_re_ingest_with_text_clears_the_reason(self):
        self._ingest_empty("/drive/later_ocr.pdf")
        self.store.ingest_text("drive_legal", "default", "/drive/later_ocr.pdf",
                               "later", "now it has text", {})
        self.assertEqual([], self.store.skipped_documents())


class CheckSkipReasonTest(unittest.TestCase):
    """The pre-upload checks record why they skipped, not just that they did."""

    def setUp(self):
        self.store = store.RagStore()

    def test_unsupported_type_is_recorded(self):
        token, reason = self.store.check("drive_hr", "default", "/drive/a.exe", 10, 1, "")
        self.assertIsNone(token)
        self.assertEqual("unsupported_type", reason)
        listed = self.store.skipped_documents()
        self.assertEqual([("/drive/a.exe", "unsupported_type")],
                         [(d["source_path"], d["reason"]) for d in listed])

    def test_too_large_is_recorded(self):
        token, reason = self.store.check(
            "drive_hr", "default", "/drive/big.txt", store.MAX_CONTENT_BYTES + 1, 1, "")
        self.assertIsNone(token)
        self.assertEqual("too_large", reason)
        self.assertEqual("too_large", self.store.skipped_documents()[0]["reason"])

    def test_reasons_are_counted_separately(self):
        self.store.check("drive_hr", "default", "/drive/a.exe", 10, 1, "")
        self.store.check("drive_hr", "default", "/drive/b.txt", 0, 1, "")
        self.assertEqual({"unsupported_type": 1, "empty_content": 1},
                         self.store.stats()["skipped"])


if __name__ == "__main__":
    unittest.main()
