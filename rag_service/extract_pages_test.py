"""Unit tests for extract.extract_pages -- pagination, no service needed.

Runs anywhere (no MarkItDown required) because it exercises the text path and
injects form feeds directly:

    python rag_service/extract_pages_test.py
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import extract


class ExtractPagesTest(unittest.TestCase):
    def test_unpaginated_text_is_one_page(self):
        pages = extract.extract_pages(b"just some notes", "/drive/a.txt")
        self.assertEqual([(1, "root", "just some notes")], pages)

    def test_form_feeds_become_numbered_pages(self):
        raw = "first page\fsecond page\fthird page".encode("utf-8")
        pages = extract.extract_pages(raw, "/drive/a.txt")
        self.assertEqual([1, 2, 3], [p[0] for p in pages])
        self.assertEqual(["first page", "second page", "third page"],
                         [p[2] for p in pages])

    def test_blank_page_does_not_shift_later_page_numbers(self):
        # A scanned page yields no text; page 3 must still be numbered 3.
        raw = "one\f   \fthree".encode("utf-8")
        pages = extract.extract_pages(raw, "/drive/a.txt")
        self.assertEqual([(1, "root", "one"), (3, "root", "three")], pages)

    def test_empty_document_yields_no_pages(self):
        self.assertEqual([], extract.extract_pages(b"   ", "/drive/a.txt"))

    def test_document_of_only_form_feeds_yields_no_pages(self):
        # Form feeds are whitespace, so this strips to nothing.
        self.assertEqual([], extract.extract_pages("\f\f".encode("utf-8"), "/drive/a.txt"))

    def test_unsupported_extension_raises(self):
        with self.assertRaises(extract.ExtractionError):
            extract.extract_pages(b"x", "/drive/a.exe")


class PdfPathTest(unittest.TestCase):
    """PDFs must go through pdfminer, which keeps the page separators that
    MarkItDown normalises away."""

    def setUp(self):
        self._saved = (extract._PDFMINER, extract._PDFMINER_TRIED)
        extract._PDFMINER_TRIED = True
        self.calls = []

    def tearDown(self):
        extract._PDFMINER, extract._PDFMINER_TRIED = self._saved

    def test_pdf_uses_pdfminer_and_keeps_pages(self):
        extract._PDFMINER = lambda stream: "p one\fp two\fp three"
        pages = extract.extract_pages(b"%PDF-1.4", "/drive/a.pdf")
        self.assertEqual([1, 2, 3], [p[0] for p in pages])

    def test_pdf_falls_back_to_markitdown_when_pdfminer_is_empty(self):
        extract._PDFMINER = lambda stream: "   "
        with mock.patch.object(extract, "_extract_markitdown",
                               return_value="fallback text") as fallback:
            pages = extract.extract_pages(b"%PDF-1.4", "/drive/a.pdf")
        fallback.assert_called_once()
        self.assertEqual([(1, "root", "fallback text")], pages)

    def test_pdf_falls_back_when_pdfminer_raises(self):
        def boom(stream):
            raise ValueError("malformed xref")

        extract._PDFMINER = boom
        with mock.patch.object(extract, "_extract_markitdown",
                               return_value="fallback text"):
            pages = extract.extract_pages(b"%PDF-1.4", "/drive/a.pdf")
        self.assertEqual([(1, "root", "fallback text")], pages)

    def test_other_markitdown_formats_are_untouched(self):
        extract._PDFMINER = lambda stream: self.calls.append(stream)
        with mock.patch.object(extract, "_extract_markitdown",
                               return_value="docx text") as md:
            extract.extract_pages(b"PK", "/drive/a.docx")
        md.assert_called_once()
        self.assertEqual([], self.calls)


if __name__ == "__main__":
    unittest.main()
