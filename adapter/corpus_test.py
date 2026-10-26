"""The ids the adapter derives must match the RAG's (rag_service/store.py)."""
import unittest

import corpus


class DocIdTest(unittest.TestCase):
    def test_doc_id_is_the_rags(self):
        # Values computed with rag_service/store.py `_doc_id`.
        self.assertEqual("022ef9201f8c", corpus.doc_id_of(
            "default", "jelentesek-1a2b3c4d",
            "/storage/drives/jelentesek/feljegyzesek/2025/Feljegyzes_00169.docx"))
        self.assertEqual("b4f2b4d3f6fc", corpus.doc_id_of("t1", "c", "/storage/drives/x/a b/ő.pdf/"))


if __name__ == "__main__":
    unittest.main()
