"""Retrieval across several corpora -- and the isolation that must survive it.

The corpus filter IS the ACL in this system, so widening a search from one
corpus to a list is a security change as much as a feature. These tests pin
both directions: a scope reaches every corpus it lists, and it reaches nothing
else, even when the question targets the excluded corpus directly.

    VIVESEC_BACKEND=fallback python rag_service/multi_corpus_test.py
"""
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

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


class SqliteHybridSearchTest(MultiCorpusSearchTest):
    def setUp(self):
        import sqlite_store
        self.store = sqlite_store.SqliteVecStore()
        self.addCleanup(self.store._conn.close)
        self._index(FINANCE, "/storage/drives/finance/travel.txt", SHARED)
        self._index(HR, "/storage/drives/hr/travel.txt", SHARED)
        self._index(HR, "/storage/drives/hr/council.txt", HR_ONLY)
        self._index(LEGAL, "/storage/drives/legal/supply.txt", LEGAL_ONLY)

    def test_name_lookup_survives_unhelpful_vectors(self):
        target = "/storage/drives/hr/director.txt"
        with patch.object(self.store, "_embed", side_effect=lambda texts: [[1.0, 0.0] for text in texts]):
            self.store = self._fresh_store()
            with patch.object(self.store, "_embed", side_effect=lambda texts: [[1.0, 0.0] for text in texts]):
                for index in range(300):
                    self._index(FINANCE, "/storage/drives/finance/staff%d.txt" % index,
                                "Other staff work for another company.")
                self._index(HR, target, "Szeredy Csaba represents ViVeTech Zrt as CEO.")
                self._index(LEGAL, "/storage/drives/legal/secret.txt", "Szeredy Csaba private legal record.")
                contexts, debug = self.store.search_context(
                    [FINANCE, HR], "szeredy csaba melyik cegnel dolgozik?", top_k=1)
                self.assertEqual([target], self._paths(contexts))
                self.assertGreater(debug["lexical_candidates"], 0)
                self.assertLessEqual(debug["dense_candidates"], 40)
                self.assertLessEqual(debug["lexical_candidates"], 40)

    def _fresh_store(self):
        import sqlite_store
        result = sqlite_store.SqliteVecStore()
        self.addCleanup(result._conn.close)
        return result

    def test_explicit_file_is_a_prefilter_across_drives(self):
        contexts, _ = self.store.search_context(
            [FINANCE, HR], "salary bands", top_k=5, source_paths=["travel.txt"])
        self.assertEqual({FINANCE, HR}, {context["corpus_id"] for context in contexts})
        self.assertTrue(all(context["source_path"].endswith("/travel.txt") for context in contexts))
        contexts, _ = self.store.search_context(
            [FINANCE, HR], "supply", source_paths=["/storage/drives/legal/supply.txt"])
        self.assertEqual(contexts, [])
        contexts, _ = self.store.search_context(FINANCE, "travel", source_paths=["missing.txt"])
        self.assertEqual(contexts, [])

    def test_evidence_is_reloaded_only_inside_current_scope(self):
        old, _ = self.store.search_context(HR, "works council", top_k=1)
        reference = old[0]["chunk_id"]
        contexts, debug = self.store.search_context(
            FINANCE, "travel", evidence_chunk_ids=[reference])
        self.assertEqual(debug["evidence_revalidated"], 0)
        self.assertNotIn(reference, [context["chunk_id"] for context in contexts])
        contexts, debug = self.store.search_context(
            [FINANCE, HR], "travel", evidence_chunk_ids=[reference])
        self.assertEqual(contexts[0]["chunk_id"], reference)
        self.assertEqual(debug["evidence_revalidated"], 1)

    def test_fts_tracks_replacement_and_deletion(self):
        path = "/storage/drives/hr/person.txt"
        self._index(HR, path, "Uniquename person record")
        self._index(HR, path, "Replacement text")
        self.assertEqual(self.store._conn.execute(
            "SELECT count(*) FROM chunk_text_fts WHERE chunk_text_fts MATCH 'Uniquename'").fetchone()[0], 0)
        self.store.drop_tree(HR, path, False)
        self.assertEqual(self.store._conn.execute(
            "SELECT count(*) FROM chunk_text_fts WHERE chunk_text_fts MATCH 'Replacement'").fetchone()[0], 0)

    def test_existing_database_backfills_without_embedding(self):
        import sqlite_store
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "index.sqlite")
            original = sqlite_store.SqliteVecStore(path)
            original._index_document(HR, "default", "/hr/person.txt", "person.txt",
                                     [(1, "root", "Uniquename employment record")], None, None)
            original._conn.executescript("""
                DROP TRIGGER chunks_fts_insert;
                DROP TRIGGER chunks_fts_delete;
                DROP TRIGGER chunks_fts_update;
                DROP TABLE chunk_text_fts;
                DELETE FROM meta WHERE key='fts_ready';
            """)
            original._conn.close()
            with patch.object(sqlite_store.SqliteVecStore, "_embed", side_effect=AssertionError("reembedding")):
                migrated = sqlite_store.SqliteVecStore(path)
            try:
                self.assertEqual(migrated._conn.execute(
                    "SELECT count(*) FROM chunk_text_fts WHERE chunk_text_fts MATCH 'Uniquename'").fetchone()[0], 1)
                migrated._conn.execute("DELETE FROM meta WHERE key='fts_ready'")
                migrated._conn.execute("INSERT INTO chunk_text_fts(chunk_text_fts) VALUES('delete-all')")
                migrated._conn.commit()
            finally:
                migrated._conn.close()
            restarted = sqlite_store.SqliteVecStore(path)
            try:
                self.assertEqual(restarted._conn.execute(
                    "SELECT count(*) FROM chunk_text_fts WHERE chunk_text_fts MATCH 'Uniquename'").fetchone()[0], 1)
                restarted._conn.execute("INSERT INTO chunk_text_fts(chunk_text_fts, rank) VALUES('integrity-check', 1)")
            finally:
                restarted._conn.close()


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
