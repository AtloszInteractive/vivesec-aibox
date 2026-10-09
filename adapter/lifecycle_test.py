"""User lifecycle events (E02): lock states, persistence and the fail-closed
handling of an unreadable state file; job cancellation for a locked user."""
import os
import tempfile
import threading
import time
import unittest

import jobstore
import lifecycle
import scheduler


class LifecycleStoreTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = os.path.join(self.temp.name, "adapter", "lifecycle.json")
        self.store = lifecycle.LifecycleStore(self.path, clock=lambda: 42.0)

    def test_suspension_and_reinstatement(self):
        result = self.store.apply("suspended", "lars", upn="lars@example.com", reason="HR")
        self.assertEqual(result["state"], "suspended")
        self.assertEqual(self.store.state_of(["lars"]), "suspended")
        self.assertEqual(self.store.state_of(["other", "lars@example.com"]), "suspended")
        self.assertEqual(self.store.apply("reinstated", "lars", upn="lars@example.com")["state"],
                         "active")
        self.assertIsNone(self.store.state_of(["lars", "lars@example.com"]))

    def test_revocation_locks(self):
        self.store.apply("REVOKED", "anna")
        self.assertEqual(self.store.state_of(["anna"]), "revoked")

    def test_deletion_is_terminal(self):
        self.store.apply("deleted", "lars")
        with self.assertRaises(lifecycle.LifecycleError) as caught:
            self.store.apply("reinstated", "lars")
        self.assertEqual(caught.exception.status, 409)
        self.assertEqual(self.store.apply("suspended", "lars")["state"], "deleted")
        self.assertEqual(self.store.state_of(["lars"]), "deleted")

    def test_state_survives_a_restart(self):
        self.store.apply("suspended", "lars", event_id="evt-1")
        reloaded = lifecycle.LifecycleStore(self.path)
        self.assertEqual(reloaded.state_of(["lars"]), "suspended")
        self.assertEqual(reloaded.stats()["locked"], {"suspended": 1})

    def test_unreadable_state_refuses_everyone(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        for content in ("{broken", '{"users": {"x": {"state": "weird"}}}', "[]"):
            with open(self.path, "w", encoding="utf-8") as handle:
                handle.write(content)
            store = lifecycle.LifecycleStore(self.path)
            with self.assertRaises(lifecycle.LifecycleUnavailable):
                store.state_of(["anyone"])
            with self.assertRaises(lifecycle.LifecycleUnavailable):
                store.apply("suspended", "anyone")
            self.assertTrue(store.stats()["unavailable"])

    def test_invalid_events(self):
        for event, user in (("archived", "lars"), (None, "lars"), ("suspended", ""),
                            ("suspended", None), ("suspended", 7),
                            ("suspended", "x" * (lifecycle.MAX_ID_LENGTH + 1))):
            with self.assertRaises(lifecycle.LifecycleError):
                self.store.apply(event, user)
        self.assertFalse(os.path.exists(self.path))

    def test_in_memory_store(self):
        store = lifecycle.LifecycleStore(None)
        store.apply("suspended", "lars")
        self.assertEqual(store.state_of(["lars"]), "suspended")
        self.assertFalse(store.stats()["persistent"])

    def test_stats_count_users_not_keys(self):
        self.store.apply("suspended", "lars", upn="lars@example.com")
        self.store.apply("deleted", "anna")
        self.assertEqual(self.store.stats()["locked"], {"suspended": 1, "deleted": 1})

    def test_purge(self):
        self.store.apply("deleted", "lars")
        self.store.purge()
        self.assertIsNone(self.store.state_of(["lars"]))
        self.assertFalse(os.path.exists(self.path))

    def test_failed_persist_still_locks(self):
        self.store.apply("suspended", "anna")
        blocker = os.path.join(self.temp.name, "file")
        with open(blocker, "w", encoding="utf-8") as handle:
            handle.write("x")
        store = lifecycle.LifecycleStore(os.path.join(blocker, "lifecycle.json"))
        with self.assertRaises(OSError):
            store.apply("suspended", "lars")
        self.assertEqual(store.state_of(["lars"]), "suspended")


class CancellationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.jobs = jobstore.JobStore(self.temp.name)

    def test_job_store_cancels_every_active_job_of_the_user(self):
        self.jobs.create("q1", "lars", "/storage/drives/a/", {})
        self.jobs.create("q2", "lars", "/storage/drives/b/", {})
        self.jobs.create("r1", "lars", "/storage/drives/a/", {})
        self.jobs.start("lars", "/storage/drives/a/", "r1")
        self.jobs.create("d1", "lars", "/storage/drives/a/", {})
        self.jobs.finish("lars", "/storage/drives/a/", "d1", 200, {"ok": True})
        self.jobs.create("o1", "anna", "/storage/drives/a/", {})
        self.assertEqual(self.jobs.cancel_user(["lars", None]), 3)
        self.assertEqual(self.jobs.get("lars", "/storage/drives/a/", "q1")["status"], "cancelled")
        self.assertEqual(self.jobs.get("lars", "/storage/drives/b/", "q2")["status"], "cancelled")
        running = self.jobs.get("lars", "/storage/drives/a/", "r1")
        self.assertEqual(running["status"], "running")
        self.assertTrue(running["cancel_requested"])
        self.assertEqual(self.jobs.get("lars", "/storage/drives/a/", "d1")["status"], "done")
        self.assertEqual(self.jobs.get("anna", "/storage/drives/a/", "o1")["status"], "queued")

    def test_scheduler_drops_queued_and_interrupts_running(self):
        started, release = threading.Event(), threading.Event()
        seen = []

        def worker(job_id, cancel_event=None):
            seen.append(job_id)
            started.set()
            release.wait(5)
            seen.append((job_id, cancel_event.is_set()))

        heavy = scheduler.HeavyScheduler(worker)
        self.addCleanup(heavy.stop)
        heavy.submit("run", "lars", "d", ("run",))
        self.assertTrue(started.wait(5))
        heavy.submit("q-lars", "lars", "d", ("q-lars",))
        heavy.submit("q-anna", "anna", "d", ("q-anna",))
        self.assertEqual(heavy.cancel_user(["lars"]), 2)
        release.set()
        deadline = time.time() + 5
        while ("q-anna", True) not in seen and ("q-anna", False) not in seen \
                and time.time() < deadline:
            time.sleep(0.02)
        self.assertIn(("run", True), seen)
        self.assertNotIn("q-lars", seen)
        self.assertIn("q-anna", seen)


if __name__ == "__main__":
    unittest.main()
