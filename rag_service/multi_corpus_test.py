"""Retrieval across several corpora -- and the isolation that must survive it.

The corpus filter IS the ACL in this system, so widening a search from one
corpus to a list is a security change as much as a feature. These tests pin
both directions: a scope reaches every corpus it lists, and it reaches nothing
else, even when the question targets the excluded corpus directly.

    VIVESEC_BACKEND=fallback python rag_service/multi_corpus_test.py
"""
import os
import sys
import unittest

os.environ.setdefault("VIVESEC_BACKEND", "fallback")

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "poc"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import store  # noqa: E402

FINANCE = "finance-1111"
HR = "hr-2222"
LEGAL = "legal-3333"

SHARED = "The travel policy grants a daily allowance for every trip."
HR_ONLY = "The works council reviews the salary bands each spring."
LEGAL_ONLY = "The supply agreement caps liquidated damages at ten percent."


class MultiCorpusSearchTest(unittest.TestCase):
    def setUp(self):
        self.store = store.RagStore()
        self._index(FINANCE, "/storage/drives/finance/travel.txt", SHARED)
        self._index(HR, "/storage/drives/hr/travel.txt", SHARED)
        self._index(HR, "/storage/drives/hr/council.txt", HR_ONLY)
        self._index(LEGAL, "/storage/drives/legal/supply.txt", LEGAL_ONLY)

    def _index(self, corpus_id, path, text):
        self.store._index_document(
            corpus_id=corpus_id, tenant_id="default", source_path=path,
            title=path.rsplit("/", 1)[-1], pages=[(1, "root", text)],
            mtime=None, size=None)

    def _paths(self, contexts):
        return [c["source_path"] for c in contexts]

    # -- the feature --------------------------------------------------------
    def test_scope_reaches_every_listed_corpus(self):
        contexts, _ = self.store.search_context(
            None, "travel policy daily allowance", top_k=2,
            corpus_ids=[FINANCE, HR])
        self.assertEqual(len(contexts), 2)
        self.assertEqual({c["corpus_id"] for c in contexts}, {FINANCE, HR})

    def test_each_context_reports_its_own_corpus(self):
        # The UI labels citations by drive; a hit carrying the requesting
        # corpus instead of its owner would mislabel every cross-drive answer.
        contexts, _ = self.store.search_context(
            None, "works council salary bands", top_k=3,
            corpus_ids=[FINANCE, HR])
        owners = {c["source_path"]: c["corpus_id"] for c in contexts}
        self.assertEqual(owners["/storage/drives/hr/council.txt"], HR)

    # -- the isolation that must not regress --------------------------------
    def test_a_corpus_outside_the_scope_is_never_returned(self):
        contexts, _ = self.store.search_context(
            None, "supply agreement liquidated damages", top_k=5,
            corpus_ids=[FINANCE, HR])
        self.assertNotIn("/storage/drives/legal/supply.txt", self._paths(contexts))
        self.assertNotIn(LEGAL, {c["corpus_id"] for c in contexts})

    def test_single_corpus_scope_stays_isolated(self):
        contexts, _ = self.store.search_context(
            None, "works council salary bands", top_k=5, corpus_ids=[FINANCE])
        self.assertTrue(contexts)
        self.assertEqual({c["corpus_id"] for c in contexts}, {FINANCE})

    # -- v1 contract compatibility ------------------------------------------
    def test_a_plain_corpus_id_still_works(self):
        contexts, _ = self.store.search_context(FINANCE, "travel policy", top_k=5)
        self.assertTrue(contexts)
        self.assertEqual({c["corpus_id"] for c in contexts}, {FINANCE})

    def test_empty_scope_returns_nothing(self):
        contexts, debug = self.store.search_context(None, "travel policy", top_k=5)
        self.assertEqual(contexts, [])
        self.assertEqual(debug["chunk_hits_count"], 0)


class NormalizeCorpusIdsTest(unittest.TestCase):
    def test_prefers_the_list_and_keeps_order(self):
        self.assertEqual(
            store.normalize_corpus_ids("ignored", [HR, FINANCE]), [HR, FINANCE])

    def test_falls_back_to_the_single_id(self):
        self.assertEqual(store.normalize_corpus_ids(FINANCE, None), [FINANCE])
        self.assertEqual(store.normalize_corpus_ids(FINANCE, []), [FINANCE])

    def test_drops_duplicates_and_blanks(self):
        self.assertEqual(
            store.normalize_corpus_ids(None, [FINANCE, " ", FINANCE, HR, None]),
            [FINANCE, HR])

    def test_nothing_in_nothing_out(self):
        self.assertEqual(store.normalize_corpus_ids(None, None), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
