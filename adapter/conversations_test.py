"""Unit tests for the persistent multi-thread conversation store (F1)."""
import json
import os
import tempfile
import unittest

import conversations
import session
from conversations import ConversationStore, DEFAULT_ID


class FakeClock:
    def __init__(self, start=1_000_000.0):
        self.now = start

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class ConversationStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.clock = FakeClock()
        self.store = self.make()

    def make(self, **kwargs):
        options = {"spill_dir": self.tmp.name, "idle_seconds": 900, "max_turns": 3,
                   "max_stored_turns": 5, "retention_days": 90, "max_per_scope": 4,
                   "clock": self.clock}
        options.update(kwargs)
        return ConversationStore(**options)

    # -- default thread == legacy session file --------------------------------
    def test_default_thread_is_the_legacy_session_file(self):
        self.store.record("u1", "scope-a", "Q1", "A1")
        legacy = os.path.join(self.tmp.name, session.session_key("u1", "scope-a") + ".json")
        self.assertTrue(os.path.exists(legacy))
        with open(legacy, encoding="utf-8") as handle:
            data = json.load(handle)
        self.assertEqual(data["user"], "u1")
        self.assertEqual(data["drive"], "scope-a")
        self.assertEqual([t["content"] for t in data["turns"]], ["Q1", "A1"])
        # No conversation_id -> the same thread, both for reading and writing.
        self.assertEqual(self.store.history("u1", "scope-a"),
                         self.store.history("u1", "scope-a", DEFAULT_ID))

    def test_legacy_session_file_is_read_as_default_thread(self):
        key = session.session_key("u1", "scope-a")
        with open(os.path.join(self.tmp.name, key + ".json"), "w", encoding="utf-8") as handle:
            json.dump({"user": "u1", "drive": "scope-a",
                       "turns": [{"role": "user", "content": "old Q"},
                                 {"role": "assistant", "content": "old A",
                                  "sources": [{"chunk_id": "c1", "corpus_id": "x"}]}]}, handle)
        history = self.store.history("u1", "scope-a")
        self.assertEqual([t["content"] for t in history], ["old Q", "old A"])
        self.assertEqual(history[1]["sources"][0]["chunk_id"], "c1")
        listed = self.store.list("u1", "scope-a")
        self.assertEqual([entry["id"] for entry in listed], [DEFAULT_ID])
        self.assertEqual(listed[0]["turn_count"], 1)

    def test_history_window_unchanged_while_storage_keeps_more(self):
        for i in range(6):
            self.store.record("u1", "scope-a", "q%d" % i, "a%d" % i)
        history = self.store.history("u1", "scope-a")
        # max_turns=3 exchanges hand the model 6 messages, as before F1 ...
        self.assertEqual(len(history), 6)
        self.assertEqual(history[-1]["content"], "a5")
        # ... while the thread itself keeps max_stored_turns=5 exchanges.
        self.assertEqual(self.store.get("u1", "scope-a", DEFAULT_ID)["turn_count"], 5)
        self.assertEqual([t["role"] for t in history], ["user", "assistant"] * 3)
        self.assertNotIn("_ts", history[0])

    def test_history_is_a_copy(self):
        sources = [{"chunk_id": "c1", "corpus_id": "x", "source_path": "/p"}]
        self.store.record("u1", "scope-a", "Q", "A [#1]", sources=sources)
        sources[0]["chunk_id"] = "changed"
        history = self.store.history("u1", "scope-a")
        history[-1]["sources"][0]["chunk_id"] = "changed again"
        self.assertEqual(self.store.history("u1", "scope-a")[-1]["sources"][0]["chunk_id"], "c1")

    # -- named threads ----------------------------------------------------------
    def test_two_threads_are_independent(self):
        a = self.store.create("u1", "scope-a")["id"]
        b = self.store.create("u1", "scope-a")["id"]
        self.store.record("u1", "scope-a", "about A", "answer A", conversation_id=a)
        self.store.record("u1", "scope-a", "about B", "answer B", conversation_id=b)
        self.store.record("u1", "scope-a", "default Q", "default A")
        self.assertEqual([t["content"] for t in self.store.history("u1", "scope-a", a)],
                         ["about A", "answer A"])
        self.assertEqual([t["content"] for t in self.store.history("u1", "scope-a", b)],
                         ["about B", "answer B"])
        self.assertEqual([t["content"] for t in self.store.history("u1", "scope-a")],
                         ["default Q", "default A"])
        ids = {entry["id"] for entry in self.store.list("u1", "scope-a")}
        self.assertEqual(ids, {a, b, DEFAULT_ID})

    def test_record_into_unknown_thread_is_refused(self):
        self.assertIsNone(self.store.record("u1", "scope-a", "Q", "A",
                                            conversation_id="deadbeefdeadbeef"))
        self.assertIsNone(self.store.record("u1", "scope-a", "Q", "A",
                                            conversation_id="../evil"))
        self.assertEqual(self.store.list("u1", "scope-a"), [])

    def test_thread_hidden_from_other_user_scope_and_profile(self):
        cid = self.store.create("u1", "scope-a", title="Mine")["id"]
        self.store.record("u1", "scope-a", "Q", "A", conversation_id=cid)
        self.assertFalse(self.store.exists("u2", "scope-a", cid))
        self.assertFalse(self.store.exists("u1", "scope-b", cid))
        self.assertIsNone(self.store.get("u2", "scope-a", cid))
        self.assertEqual(self.store.history("u1", "scope-b", cid), [])
        self.assertEqual(self.store.list("u2", "scope-a"), [])
        self.assertFalse(self.store.delete("u2", "scope-a", cid))
        self.assertIsNone(self.store.rename("u1", "scope-b", cid, "Stolen"))
        # The hybrid profile namespaces the scope (chat_policy.history_scope).
        self.assertFalse(self.store.exists("u1", "\x00chat:hybrid\x00scope-a", cid))
        self.assertTrue(self.store.exists("u1", "scope-a", cid))
        self.assertEqual(self.store.get("u1", "scope-a", cid)["title"], "Mine")

    def test_list_is_most_recent_first_with_counts(self):
        a = self.store.create("u1", "scope-a", title="A")["id"]
        self.clock.advance(10)
        b = self.store.create("u1", "scope-a", title="B")["id"]
        self.clock.advance(10)
        self.store.record("u1", "scope-a", "q", "a", conversation_id=a)
        listed = self.store.list("u1", "scope-a")
        self.assertEqual([e["id"] for e in listed], [a, b])
        self.assertEqual(listed[0]["turn_count"], 1)
        self.assertEqual(listed[1]["turn_count"], 0)
        self.assertEqual(listed[0]["title"], "A")
        # The empty default thread is not listed.
        self.assertNotIn(DEFAULT_ID, [e["id"] for e in listed])

    # -- titles -----------------------------------------------------------------------
    def test_fallback_title_then_auto_then_user(self):
        cid = self.store.create("u1", "scope-a")["id"]
        long_question = "What was the fleet availability in April 2026 compared to March and why did it drop"
        self.store.record("u1", "scope-a", long_question, "99.85%", conversation_id=cid)
        got = self.store.get("u1", "scope-a", cid)
        self.assertEqual(got["title_source"], "fallback")
        self.assertTrue(got["title"].endswith("…"))
        self.assertLessEqual(len(got["title"]), 61)
        self.assertTrue(long_question.startswith(got["title"][:-1]))
        self.store.set_title("u1", "scope-a", cid, "Fleet availability April 2026", source="auto")
        self.assertEqual(self.store.get("u1", "scope-a", cid)["title"], "Fleet availability April 2026")
        self.assertEqual(self.store.get("u1", "scope-a", cid)["title_source"], "auto")
        # A second automatic title does not override the first.
        self.store.set_title("u1", "scope-a", cid, "Something else", source="auto")
        self.assertEqual(self.store.get("u1", "scope-a", cid)["title"], "Fleet availability April 2026")
        # The user's rename wins over everything and is not replaced by auto.
        self.store.rename("u1", "scope-a", cid, "  My   thread ")
        self.assertEqual(self.store.get("u1", "scope-a", cid)["title"], "My thread")
        self.store.set_title("u1", "scope-a", cid, "Auto again", source="auto")
        self.assertEqual(self.store.get("u1", "scope-a", cid)["title"], "My thread")
        self.assertEqual(self.store.list("u1", "scope-a")[0]["title"], "My thread")

    def test_fallback_title_short_question_verbatim(self):
        self.assertEqual(conversations.fallback_title("  Why is\nthe sky blue? "), "Why is the sky blue?")
        self.assertEqual(conversations.fallback_title(""), "")

    # -- persistence -----------------------------------------------------------------
    def test_threads_survive_restart(self):
        cid = self.store.create("u1", "scope-a", title="Persist")["id"]
        self.store.record("u1", "scope-a", "Q", "A", conversation_id=cid)
        self.store.record("u1", "scope-a", "dQ", "dA")
        restored = self.make()
        self.assertEqual([t["content"] for t in restored.history("u1", "scope-a", cid)], ["Q", "A"])
        self.assertEqual([t["content"] for t in restored.history("u1", "scope-a")], ["dQ", "dA"])
        got = restored.get("u1", "scope-a", cid)
        self.assertEqual(got["title"], "Persist")
        self.assertEqual(got["turn_count"], 1)
        self.assertEqual({e["id"] for e in restored.list("u1", "scope-a")}, {cid, DEFAULT_ID})
        # No leftover temp files from the atomic writes.
        for root, _, names in os.walk(self.tmp.name):
            self.assertFalse([n for n in names if n.endswith(".tmp")], (root, names))

    def test_idle_sweep_evicts_memory_but_keeps_disk(self):
        cid = self.store.create("u1", "scope-a")["id"]
        self.store.record("u1", "scope-a", "Q", "A", conversation_id=cid)
        self.assertEqual(self.store.stats()["active"], 1)
        self.clock.advance(901)
        self.assertEqual(self.store.sweep(), 1)
        self.assertEqual(self.store.stats()["active"], 0)
        self.assertEqual(len(self.store.history("u1", "scope-a", cid)), 2)

    def test_delete_removes_file_and_index_entry(self):
        cid = self.store.create("u1", "scope-a")["id"]
        self.store.record("u1", "scope-a", "Q", "A", conversation_id=cid)
        path = os.path.join(self.tmp.name, "conversations",
                            session.session_key("u1", "scope-a"), cid + ".json")
        self.assertTrue(os.path.exists(path))
        self.assertTrue(self.store.delete("u1", "scope-a", cid))
        self.assertFalse(os.path.exists(path))
        self.assertFalse(self.store.exists("u1", "scope-a", cid))
        self.assertEqual(self.store.list("u1", "scope-a"), [])
        self.assertFalse(self.store.delete("u1", "scope-a", cid))
        # Deleting the default thread removes the legacy file too.
        self.store.record("u1", "scope-a", "Q", "A")
        self.assertTrue(self.store.delete("u1", "scope-a", DEFAULT_ID))
        self.assertEqual(self.store.history("u1", "scope-a"), [])
        self.assertFalse(self.store.delete("u1", "scope-a", DEFAULT_ID))

    def test_flush_and_purge(self):
        cid = self.store.create("u1", "scope-a")["id"]
        self.store.record("u1", "scope-a", "Q", "A", conversation_id=cid)
        self.store.record("u1", "scope-a", "dQ", "dA")
        self.assertEqual(self.store.flush_memory(), 2)
        self.assertEqual(self.store.stats()["active"], 0)
        self.assertEqual(len(self.store.history("u1", "scope-a", cid)), 2)
        self.assertGreaterEqual(self.store.purge(), 2)
        fresh = self.make()
        self.assertEqual(fresh.history("u1", "scope-a", cid), [])
        self.assertEqual(fresh.history("u1", "scope-a"), [])
        self.assertEqual(fresh.list("u1", "scope-a"), [])

    # -- retention -------------------------------------------------------------------
    def test_per_scope_cap_drops_least_recently_updated(self):
        ids = []
        for i in range(4):
            ids.append(self.store.create("u1", "scope-a", title="t%d" % i)["id"])
            self.clock.advance(1)
        self.store.record("u1", "scope-a", "dQ", "dA")  # default is exempt
        self.store.record("u1", "scope-a", "q", "a", conversation_id=ids[0])  # touch the oldest
        self.clock.advance(1)
        newest = self.store.create("u1", "scope-a", title="t4")["id"]
        listed = {e["id"] for e in self.store.list("u1", "scope-a")}
        self.assertEqual(len(listed - {DEFAULT_ID}), 4)
        self.assertIn(ids[0], listed)
        self.assertNotIn(ids[1], listed)
        self.assertIn(newest, listed)
        self.assertIn(DEFAULT_ID, listed)
        self.assertFalse(self.store.exists("u1", "scope-a", ids[1]))

    def test_retention_window_removes_stale_threads_only(self):
        old = self.store.create("u1", "scope-a", title="old")["id"]
        self.store.record("u1", "scope-a", "q", "a", conversation_id=old)
        self.store.record("u1", "scope-a", "dQ", "dA")
        self.clock.advance(91 * 86400)
        fresh = self.store.create("u1", "scope-a", title="fresh")["id"]
        self.store.flush_memory()
        self.assertEqual(self.store.retire(), 1)
        ids = {e["id"] for e in self.store.list("u1", "scope-a")}
        self.assertEqual(ids, {fresh, DEFAULT_ID})
        self.assertFalse(self.store.exists("u1", "scope-a", old))
        self.assertEqual(len(self.store.history("u1", "scope-a")), 2)

    # -- compatibility wrapper ---------------------------------------------------------------
    def test_session_manager_wrapper(self):
        manager = session.SessionManager(spill_dir=self.tmp.name, idle_seconds=900, max_turns=3)
        self.assertIsInstance(manager, ConversationStore)
        manager.record("u1", "/d/a/", "Q1", "A1")
        self.assertEqual([t["content"] for t in manager.history("u1", "/d/a/")], ["Q1", "A1"])
        self.assertEqual(manager.history("u1", "/d/b/"), [])
        stats = manager.stats()
        for key in ("active", "spill_dir", "idle_seconds", "max_turns"):
            self.assertIn(key, stats)
        from_env = session.SessionManager.from_env({"ADAPTER_SESSION_DIR": self.tmp.name,
                                                    "ADAPTER_SESSION_TURNS": "7",
                                                    "ADAPTER_CONVERSATIONS_PER_USER": "9"})
        self.assertEqual(from_env.max_turns, 7)
        self.assertEqual(from_env.max_per_scope, 9)
        self.assertEqual(from_env.conversations_dir, os.path.join(self.tmp.name, "conversations"))

    def test_in_memory_only_store(self):
        store = ConversationStore(spill_dir=None, clock=self.clock)
        cid = store.create("u1", "s")["id"]
        store.record("u1", "s", "Q", "A", conversation_id=cid)
        store.record("u1", "s", "dQ", "dA")
        self.assertEqual(len(store.history("u1", "s", cid)), 2)
        self.assertEqual({e["id"] for e in store.list("u1", "s")}, {cid, DEFAULT_ID})
        self.assertEqual(store.sweep(), 0)
        self.assertEqual(store.retire(), 0)
        self.assertTrue(store.delete("u1", "s", cid))
        self.assertEqual(store.purge(), 0)
        self.assertEqual(store.list("u1", "s"), [])


if __name__ == "__main__":
    unittest.main()
