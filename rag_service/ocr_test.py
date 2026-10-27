"""E10b OCR: the engine with a faked Tesseract, and the RAG store's background
reading of scanned pages (queue, supersession, deletion, restart).

    VIVESEC_BACKEND=fallback python -m unittest ocr_test
"""
import io
import os
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

os.environ.setdefault("VIVESEC_BACKEND", "fallback")
sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "poc"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import extract  # noqa: E402
import ocr  # noqa: E402
import store  # noqa: E402,F401 -- before sqlite_store puts poc/ first on the path

try:
    from PIL import Image
    import pypdfium2  # noqa: F401
except ImportError:  # pragma: no cover
    Image = None

HEADER = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext"


def tsv(*words):
    """words: (block, par, line, conf, text)"""
    rows = [HEADER, "1\t1\t0\t0\t0\t0\t0\t0\t10\t10\t-1\t"]
    rows += ["5\t1\t%d\t%d\t%d\t1\t0\t0\t1\t1\t%s\t%s" % w for w in words]
    return "\n".join(rows) + "\n"


def scanned_pdf(pages=2):
    images = [Image.new("L", (200, 280), 255) for _ in range(pages)]
    buffer = io.BytesIO()
    images[0].save(buffer, "PDF", save_all=True, append_images=images[1:])
    return buffer.getvalue()


class EngineTest(unittest.TestCase):
    def test_tsv_keeps_lines_and_paragraphs_and_weighs_confidence(self):
        text, quality = ocr.parse_tsv(tsv((1, 1, 1, 90, "Kovács"), (1, 1, 1, 90, "János"),
                                          (1, 1, 2, 60, "zárka"), (1, 2, 1, 30, "a")))
        self.assertEqual("Kovács János\nzárka\n\na", text)
        self.assertEqual(round((90 * 6 + 90 * 5 + 60 * 5 + 30) / 17), quality)
        self.assertEqual(("", None), ocr.parse_tsv(HEADER + "\n"))

    def test_page_language(self):
        self.assertEqual("hu", ocr.detect_language("A látogató és a fogvatartott nem volt ott."))
        self.assertEqual("en", ocr.detect_language("The visitor and the inmate were in the cell."))
        self.assertEqual("de", ocr.detect_language("Der Besucher und die Insassin ist nicht mit"))
        self.assertEqual("da", ocr.detect_language("Besøget og det er til på af med"))
        self.assertEqual("hu", ocr.detect_language("Befogadási adatlap Név: MÉSZÁROS DÁVID Kőszeg"))
        self.assertIsNone(ocr.detect_language("12345 ABC"))

    def test_osd_rotation_needs_confidence(self):
        with patch.object(ocr, "_tesseract", return_value="Rotate: 90\nOrientation confidence: 7.5"):
            self.assertEqual(90, ocr.rotation(b"png"))
        with patch.object(ocr, "_tesseract", return_value="Rotate: 90\nOrientation confidence: 0.4"):
            self.assertEqual(0, ocr.rotation(b"png"))
        with patch.object(ocr, "_tesseract", return_value="Too few characters. Skipping."):
            self.assertEqual(0, ocr.rotation(b"png"))

    @unittest.skipIf(Image is None, "PIL/pypdfium2 not installed")
    def test_a_turned_page_is_set_upright_and_rated(self):
        calls = []

        def fake(png, *args):
            calls.append(args)
            if "osd" in args:
                return "Rotate: 90\nOrientation confidence: 9"
            return tsv((1, 1, 1, 91, "Fazekas"), (1, 1, 1, 89, "Péter"))

        with patch.object(ocr, "_tesseract", side_effect=fake), \
                patch.object(ocr, "languages", return_value="hun+eng"):
            read = ocr.read_image(Image.new("L", (100, 300), 255))
        self.assertEqual(("ok", 90, 90, "Fazekas Péter"),
                         (read["status"], read["rotation"], read["quality"], read["text"]))
        self.assertIn("hun+eng", calls[1])
        with patch.object(ocr, "_tesseract", side_effect=lambda png, *a: "" if "osd" in a
                          else tsv((1, 1, 1, 20, "x7#"))):
            low = ocr.read_image(Image.new("L", (10, 10), 255))
        self.assertEqual(("low_quality", "", 20), (low["status"], low["text"], low["quality"]))

    @unittest.skipIf(Image is None, "PIL/pypdfium2 not installed")
    def test_which_pages_need_reading(self):
        pdf = scanned_pdf(3)
        self.assertEqual([1, 2, 3], ocr.pages_to_read(pdf, "/d/scan.pdf", []))
        self.assertEqual([2, 3], ocr.pages_to_read(pdf, "/d/scan.pdf", [(1, "root", "x")]))
        # Without exact page breaks a PDF with some text is left alone.
        self.assertEqual([], ocr.pages_to_read(pdf, "/d/scan.pdf", [(1, "root", "x")], False))
        frames = [Image.new("L", (20, 20), 255) for _ in range(2)]
        buffer = io.BytesIO()
        frames[0].save(buffer, "TIFF", save_all=True, append_images=frames[1:])
        self.assertEqual([1, 2], ocr.pages_to_read(buffer.getvalue(), "/d/fax.tif", []))
        self.assertEqual([], ocr.pages_to_read(b"text", "/d/a.txt", []))
        self.assertEqual([], ocr.pages_to_read(b"not a pdf", "/d/broken.pdf", []))

    def test_images_are_supported_only_where_ocr_runs(self):
        self.assertTrue(extract.is_supported("/d/scan.JPG"))
        self.assertEqual([], extract.extract_pages(b"\x89PNG....", "/d/scan.png"))
        with patch.object(ocr, "available", return_value=False):
            self.assertFalse(extract.extractor_available("/d/scan.png"))
        with patch.object(ocr, "available", return_value=True):
            self.assertTrue(extract.extractor_available("/d/scan.png"))

    def test_off_or_missing_tesseract_means_no_ocr(self):
        with patch.object(ocr, "MODE", "off"):
            self.assertFalse(ocr.available())
        with patch.object(ocr, "TESSERACT", ""):
            self.assertFalse(ocr.available())
            self.assertFalse(ocr.status()["enabled"])


def fake_pages(raw, path, numbers, still_wanted=None):
    out = {}
    for n in numbers:
        if n == 2:
            out[n] = {"text": "", "quality": 22, "lang": None, "rotation": 0,
                      "status": "low_quality"}
        else:
            out[n] = {"text": "Fazekas Péter meglátogatta Bogdán Balázst.", "quality": 87,
                      "lang": "hu", "rotation": 90, "status": "ok"}
    return out


@unittest.skipIf(Image is None, "PIL/pypdfium2 not installed")
class StoreTest(unittest.TestCase):
    CORPUS = "reports-1111"
    PATH = "/storage/drives/reports/scan.pdf"

    def setUp(self):
        import sqlite_store
        self.sqlite_store = sqlite_store
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = os.path.join(self.temp.name, "index.db")
        self.store = sqlite_store.SqliteVecStore(self.db)
        self.addCleanup(self.store._conn.close)
        # Runs first: the background reader must be done before the close.
        self.addCleanup(self.store.ocr_idle, 10)
        for target, value in (("available", lambda: True), ("read_pages", fake_pages)):
            guard = patch.object(ocr, target, value)
            guard.start()
            self.addCleanup(guard.stop)
        self.pdf = scanned_pdf(2)

    def ingest(self, raw=None, path=None, acl=None):
        return self.store.ingest_content(self.CORPUS, "default", path or self.PATH, None,
                                         raw or self.pdf, {"mtime": 5, "size": 10, "acl": acl})

    def document(self):
        return self.store._conn.execute(
            "SELECT chunks, skip_reason, ocr_state FROM documents WHERE source_path=?",
            (self.PATH,)).fetchone()

    def test_scanned_pages_are_read_in_the_background_and_rated(self):
        first = self.ingest()
        self.assertEqual({"state": "pending", "pages": 2}, first["ocr"])
        self.assertEqual("ocr_pending", first["warning"])
        self.assertTrue(self.store.ocr_idle(10))
        self.assertEqual((1, None, "done"), self.document())
        chunks, _ = self.store.match_chunks([self.CORPUS], ["Bogdán Balázs"])
        self.assertEqual([{"quality": 87, "lang": "hu"}], [c["ocr"] for c in chunks])
        contexts, _ = self.store.document_context(self.CORPUS, self.PATH)
        self.assertEqual({"quality": 87, "lang": "hu"}, contexts[0]["ocr"])
        skipped = self.store.skipped_documents(self.CORPUS)
        self.assertEqual([{"source_path": self.PATH, "corpus_id": self.CORPUS, "size": 10,
                           "reason": "ocr_pages_skipped", "ocr_state": "done",
                           "skipped_pages": [{"page": 2, "reason": "low_quality",
                                              "quality": 22}]}], skipped)
        stats = self.store.scope_stats([self.CORPUS])
        self.assertEqual((0, 0, 2), (stats["skipped"], stats["ocr_pending"], stats["pages"]))

    def test_waiting_and_unreadable_documents_are_listed(self):
        gate = threading.Event()

        def slow(raw, path, numbers, still_wanted=None):
            gate.wait(10)
            return {n: {"text": "", "quality": None, "lang": None, "rotation": 0,
                        "status": "no_text"} for n in numbers}

        with patch.object(ocr, "read_pages", slow):
            self.ingest()
            self.assertEqual((0, "ocr_pending", "pending"), self.document())
            self.assertEqual(1, self.store.scope_stats([self.CORPUS])["ocr_pending"])
            self.assertEqual("ocr_pending", self.store.skipped_documents()[0]["reason"])
            gate.set()
            self.assertTrue(self.store.ocr_idle(10))
        self.assertEqual((0, "ocr_no_text", "done"), self.document())
        self.assertEqual(2, len(self.store.skipped_documents()[0]["skipped_pages"]))

    def test_a_newer_upload_or_a_deletion_wins_over_a_late_ocr(self):
        for replace in ("text", "delete"):
            gate = threading.Event()

            def slow(raw, path, numbers, still_wanted=None):
                gate.wait(10)
                return fake_pages(raw, path, numbers)

            with patch.object(ocr, "read_pages", slow):
                self.ingest()
                if replace == "text":
                    self.store.ingest_text(self.CORPUS, "default", self.PATH, None,
                                           "Új, szöveges változat.", {})
                else:
                    self.store.drop_tree(self.CORPUS, self.PATH, keep_exact=False)
                gate.set()
                self.assertTrue(self.store.ocr_idle(10))
            row = self.document()
            if replace == "text":
                self.assertEqual((1, None, None), row)
                chunks, _ = self.store.match_chunks([self.CORPUS], ["Bogdán"])
                self.assertEqual([], chunks)
            else:
                self.assertIsNone(row)

    def test_a_restart_marks_waiting_documents_for_a_resync(self):
        gate = threading.Event()
        self.addCleanup(gate.set)
        with patch.object(ocr, "read_pages", lambda *a, **k: gate.wait(10) and {}):
            self.ingest()
            restarted = self.sqlite_store.SqliteVecStore(self.db)
            self.addCleanup(restarted._conn.close)
        row = restarted._conn.execute(
            "SELECT skip_reason, ocr_state FROM documents WHERE source_path=?",
            (self.PATH,)).fetchone()
        self.assertEqual(("ocr_interrupted", "interrupted"), row)
        self.assertEqual("ocr_interrupted", restarted.skipped_documents()[0]["reason"])

    def test_without_ocr_a_scan_stays_empty_and_a_queue_overflow_is_deferred(self):
        with patch.object(ocr, "available", lambda: False):
            result = self.ingest()
        self.assertNotIn("ocr", result)
        self.assertEqual((0, "extraction_empty", None), self.document())
        with patch.object(self.sqlite_store, "OCR_QUEUE_BYTES", 10):
            result = self.ingest()
        self.assertEqual("deferred", result["ocr"]["state"])
        self.assertEqual((0, "ocr_deferred", "deferred"), self.document())

    def test_the_skipped_list_is_a_users_view(self):
        hr_only = {"restricted": True, "allow": ["group:hr"], "deny": [], "classification": 1}
        lars = {"principals": ["everyone", "user:lars"], "clearance": 1, "membership_known": True,
                "default_classification": 1}
        hanna = dict(lars, principals=["everyone", "user:hanna", "group:hr"])
        with patch.object(ocr, "available", lambda: False):
            self.ingest(acl=hr_only)
        listed = self.store.skipped_documents(corpus_ids=[self.CORPUS], access=lars)
        self.assertEqual([], listed)
        listed = self.store.skipped_documents(corpus_ids=[self.CORPUS], access=hanna)
        self.assertEqual([self.PATH], [d["source_path"] for d in listed])
        self.assertEqual([], self.store.skipped_documents(corpus_ids=["other"], access=hanna))
        json_store = store.RagStore()
        json_store.check(self.CORPUS, "default", "/d/x.vsd", 10, 1, "", acl=hr_only)
        self.assertEqual([], json_store.skipped_documents(corpus_ids=[self.CORPUS], access=lars))
        self.assertEqual(1, len(json_store.skipped_documents(corpus_ids=[self.CORPUS],
                                                             access=hanna)))

    def test_an_image_file_is_one_page(self):
        buffer = io.BytesIO()
        Image.new("L", (40, 40), 255).save(buffer, "PNG")
        path = "/storage/drives/reports/photo.png"
        result = self.ingest(buffer.getvalue(), path)
        self.assertEqual({"state": "pending", "pages": 1}, result["ocr"])
        self.assertTrue(self.store.ocr_idle(10))
        chunks, _ = self.store.match_chunks([self.CORPUS], ["Fazekas"])
        self.assertEqual([path], [c["source_path"] for c in chunks])


if __name__ == "__main__":
    unittest.main()
