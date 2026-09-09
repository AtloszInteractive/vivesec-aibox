"""Unit tests for the heavyweight FIFO scheduler and queued cancellation."""
import os
import tempfile
import threading
import time
import unittest

import jobstore
import scheduler


class HeavySchedulerTest(unittest.TestCase):
    def test_fifo_serializes_and_reports_positions(self):
        release = threading.Event()
        first_started = threading.Event()
        completed = []

        def worker(job_id, **_kwargs):
            if job_id == "j1":
                first_started.set()
                release.wait(2)
            completed.append(job_id)

        queue = scheduler.HeavyScheduler(worker, max_queued_per_user=3)
        queue.submit("j1", "u", "/d", ("j1",))
        self.assertTrue(first_started.wait(1))
        self.assertEqual(queue.submit("j2", "u", "/d", ("j2",)), 1)
        self.assertEqual(queue.submit("j3", "u", "/d", ("j3",)), 2)
        self.assertEqual(queue.position("j1", "u", "/d"), 0)
        self.assertEqual(queue.position("j2", "u", "/d"), 1)
        release.set()
        deadline = time.time() + 2
        while len(completed) < 3 and time.time() < deadline:
            threading.Event().wait(0.01)
        queue.stop()
        self.assertEqual(completed, ["j1", "j2", "j3"])

    def test_cancel_and_per_user_limit(self):
        release = threading.Event()
        started = threading.Event()

        def worker(_job_id, **_kwargs):
            started.set()
            release.wait(2)

        queue = scheduler.HeavyScheduler(worker, max_queued_per_user=1)
        queue.submit("running", "u1", "/d", ("running",))
        self.assertTrue(started.wait(1))
        queue.submit("waiting", "u1", "/d", ("waiting",))
        with self.assertRaises(scheduler.QueueFull):
            queue.submit("overflow", "u1", "/d", ("overflow",))
        self.assertEqual(queue.submit("other-user", "u2", "/d", ("other-user",)), 2)
        self.assertEqual(queue.cancel("waiting", "u1", "/d"), "queued")
        self.assertEqual(queue.cancel("running", "u1", "/d"), "running")
        self.assertEqual(queue.cancel("missing", "u1", "/d"), "missing")
        release.set()
        queue.stop()


class JobCancellationTest(unittest.TestCase):
    def test_only_queued_jobs_can_transition_to_cancelled(self):
        root = tempfile.mkdtemp(prefix="vivesec-job-cancel-")
        store = jobstore.JobStore(root)
        store.create("queued", "u", "/d", {"query": "q"})
        cancelled = store.cancel_queued("u", "/d", "queued")
        self.assertEqual(cancelled["status"], "cancelled")
        self.assertIsNotNone(cancelled["finished"])

        store.create("running", "u", "/d", {"query": "q"})
        store.start("u", "/d", "running")
        unchanged = store.cancel_queued("u", "/d", "running")
        self.assertEqual(unchanged["status"], "running")
        self.assertIsNone(store.cancel_queued("other", "/d", "running"))


if __name__ == "__main__":
    unittest.main()