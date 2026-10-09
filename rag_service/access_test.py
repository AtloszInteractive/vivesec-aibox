"""E03 in the store: the access filter is part of the queries, so a hidden
document contributes no hit, no count, no metadata and no vocabulary.

    VIVESEC_BACKEND=fallback python rag_service/access_test.py
"""
import os
import sys
import tempfile
import unittest

os.environ.setdefault("VIVESEC_BACKEND", "fallback")

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "poc"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import access  # noqa: E402
import store  # noqa: E402

FINANCE = "finance-1111"
HR = "hr-2222"
ROOT = "/storage/drives/finance"

PUBLIC = "The travel policy grants a daily allowance for every trip."
SECRET = "The layoff plan names Zebulon Quartz as the first redundancy."
BOARD = "The board approved the merger with Quillfeather Holdings."

HR_ONLY = {"restricted": True, "allow": ["group:hr"], "deny": [], "classification": 1}
NO_INTERNS = {"restricted": False, "allow": [], "deny": ["group:interns"], "classification": 1}
STRICT = {"restricted": False, "allow": [], "deny": [], "classification": 3}


def caller(*principals, clearance=1, known=True):
    return {"principals": ["everyone"] + list(principals), "clearance": clearance,
            "membership_known": known, "default_classification": 1}


class AccessParsingTest(unittest.TestCase):
    def test_parse_access(self):
        self.assertIsNone(access.parse_access(None))
        parsed = access.parse_access(caller("User:Lars"))
        self.assertEqual(parsed["principals"], ["everyone", "user:lars"])
        for bad in ("x", {"principals": "a", "clearance": 1, "membership_known": True,
                          "default_classification": 1},
                    dict(caller(), clearance=9), dict(caller(), membership_known="yes"),
                    dict(caller(), clearance=True)):
            with self.assertRaises(ValueError):
                access.parse_access(bad)

    def test_parse_doc_acl(self):
        self.assertIsNone(access.parse_doc_acl(None))
        self.assertEqual(access.parse_doc_acl(HR_ONLY)["allow"], ["group:hr"])
        with self.assertRaises(ValueError):
            access.parse_doc_acl({"restricted": "no", "classification": 1})
        with self.assertRaises(ValueError):
            access.parse_doc_acl({"restricted": False, "classification": 7})

    def test_predicate(self):
        self.assertTrue(access.doc_allowed(HR_ONLY, None))
        self.assertTrue(access.doc_allowed(None, caller()))
        self.assertFalse(access.doc_allowed(HR_ONLY, caller("user:lars")))
        self.assertTrue(access.doc_allowed(HR_ONLY, caller("group:hr")))
        self.assertFalse(access.doc_allowed(NO_INTERNS, caller("group:interns")))
        self.assertFalse(access.doc_allowed(NO_INTERNS, caller(known=False)))
        self.assertTrue(access.doc_allowed(NO_INTERNS, caller(known=True)))
        self.assertFalse(access.doc_allowed(STRICT, caller(clearance=2)))
        self.assertTrue(access.doc_allowed(STRICT, caller(clearance=3)))
        self.assertFalse(access.doc_allowed(None, dict(caller(), default_classification=2)))


class JsonStoreAccessTest(unittest.TestCase):
    def make_store(self):
        return store.RagStore()

    def setUp(self):
        self.store = self.make_store()
        self.index(FINANCE, ROOT + "/travel.txt", PUBLIC)
        self.index(FINANCE, ROOT + "/hr/layoffs.txt", SECRET, HR_ONLY)
        self.index(FINANCE, ROOT + "/board/merger.txt", BOARD, STRICT)

    def index(self, corpus_id, path, text, acl=None):
        self.store._index_document(
            corpus_id=corpus_id, tenant_id="default", source_path=path,
            title=path.rsplit("/", 1)[-1], pages=[(1, "root", text)],
            mtime=None, size=None, acl=acl)

    def paths(self, contexts):
        return {c["source_path"] for c in contexts}

    def search(self, question, who, **kw):
        return self.store.search_context(FINANCE, question, top_k=10, access=who, **kw)

    def test_hidden_documents_never_reach_the_results(self):
        contexts, debug = self.search("layoff plan Zebulon Quartz redundancy", caller("user:lars"))
        self.assertNotIn(ROOT + "/hr/layoffs.txt", self.paths(contexts))
        self.assertNotIn(ROOT + "/board/merger.txt", self.paths(contexts))
        self.assertTrue(all("Zebulon" not in c["text"] for c in contexts))

    def test_counts_cover_only_visible_documents(self):
        _, open_debug = self.search("travel", caller("group:hr", clearance=3))
        _, lars_debug = self.search("travel", caller("user:lars"))
        self.assertEqual(open_debug["chunk_hits_count"], 3)
        self.assertEqual(lars_debug["chunk_hits_count"], 1)
        self.assertEqual(self.store.scope_stats([FINANCE], caller("user:lars"))["documents"], 1)
        self.assertEqual(self.store.scope_stats([FINANCE], None)["documents"], 3)

    def test_allowed_principal_sees_the_document(self):
        contexts, _ = self.search("layoff plan Zebulon Quartz", caller("group:hr"))
        self.assertIn(ROOT + "/hr/layoffs.txt", self.paths(contexts))

    def test_naming_a_hidden_file_finds_nothing(self):
        contexts, _ = self.search("summarize", caller("user:lars"), source_paths=["layoffs.txt"])
        self.assertEqual(contexts, [])
        contexts, debug = self.store.document_context(
            FINANCE, ROOT + "/hr/layoffs.txt", access=caller("user:lars"))
        self.assertEqual(contexts, [])
        self.assertIsNone(debug.get("source_path"))
        missing, missing_debug = self.store.document_context(
            FINANCE, ROOT + "/hr/none.txt", access=caller("user:lars"))
        self.assertEqual(debug, missing_debug)

    def test_evidence_of_a_hidden_chunk_is_not_reloaded(self):
        hr_view, _ = self.search("layoff plan Zebulon", caller("group:hr"))
        chunk = next(c["chunk_id"] for c in hr_view if c["source_path"].endswith("layoffs.txt"))
        contexts, debug = self.search("travel", caller("user:lars"), evidence_chunk_ids=[chunk])
        self.assertNotIn(chunk, [c["chunk_id"] for c in contexts])
        self.assertEqual(debug["evidence_revalidated"], 0)

    def test_acl_update_and_reupload_keep_or_change_visibility(self):
        self.store.update_acl(FINANCE, "default", [{"path": ROOT + "/hr/layoffs.txt",
                                                    "acl": dict(HR_ONLY, allow=["user:lars"])}])
        contexts, _ = self.search("layoff plan Zebulon", caller("user:lars"))
        self.assertIn(ROOT + "/hr/layoffs.txt", self.paths(contexts))
        self.index(FINANCE, ROOT + "/hr/layoffs.txt", SECRET + " Updated.")
        contexts, _ = self.search("layoff plan Zebulon", caller("user:anna"))
        self.assertNotIn(ROOT + "/hr/layoffs.txt", self.paths(contexts))
        result = self.store.update_acl(FINANCE, "default", [{"path": ROOT + "/nope.txt",
                                                             "acl": HR_ONLY}])
        self.assertEqual(result, {"updated": 0, "missing": 1})

    def test_meta_only_and_directory_rows_carry_the_acl(self):
        token, reason = self.store.check(FINANCE, "default", ROOT + "/hr/x.bin", 10, 1, None,
                                         acl=HR_ONLY)
        self.assertIsNone(token)
        self.assertEqual(self.store.scope_stats([FINANCE], caller("user:lars"))["skipped"], 0)
        self.assertEqual(self.store.scope_stats([FINANCE], caller("group:hr"))["skipped"], 1)

    def test_no_access_means_no_filter(self):
        contexts, debug = self.store.search_context(FINANCE, "layoff Zebulon", top_k=10)
        self.assertIn(ROOT + "/hr/layoffs.txt", self.paths(contexts))


class SqliteStoreAccessTest(JsonStoreAccessTest):
    def make_store(self):
        import sqlite_store
        result = sqlite_store.SqliteVecStore()
        self.addCleanup(result._conn.close)
        return result

    def test_unfiltered_corpora_keep_the_knn_path(self):
        self.index(HR, "/storage/drives/hr/open.txt", PUBLIC)
        self.assertFalse(self.store._excluded(HR, *access.sql_clause(caller("user:lars"))))
        self.assertTrue(self.store._excluded(FINANCE, *access.sql_clause(caller("user:lars"))))

    def test_lexical_match_on_a_hidden_document_is_dropped(self):
        contexts, debug = self.search("Quillfeather", caller("user:lars"))
        self.assertEqual(debug["lexical_candidates"], 0)
        self.assertNotIn(ROOT + "/board/merger.txt", self.paths(contexts))

    def test_drop_tree_removes_acl_rows(self):
        self.store.drop_tree(FINANCE, ROOT + "/hr", False)
        rows = self.store._conn.execute("SELECT COUNT(*) FROM doc_acl").fetchone()[0]
        self.assertEqual(rows, 0)

    def test_existing_index_is_upgraded_in_place(self):
        import sqlite3
        import sqlite_store
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "old.db")
            conn = sqlite3.connect(path)
            conn.executescript(
                "CREATE TABLE documents (doc_id TEXT PRIMARY KEY, corpus_id TEXT, tenant_id TEXT,"
                " source_path TEXT, title TEXT, file INTEGER, mtime INTEGER, size INTEGER,"
                " pages INTEGER, chunks INTEGER, indexed INTEGER);")
            conn.close()
            upgraded = sqlite_store.SqliteVecStore(persist_path=path)
            columns = {row[1] for row in upgraded._conn.execute("PRAGMA table_info(documents)")}
            upgraded._conn.close()
        self.assertTrue({"acl_restricted", "classification", "skip_reason"} <= columns)


if __name__ == "__main__":
    unittest.main()
