"""Unit tests for the heavyweight FIFO scheduler and queued cancellation."""
import os
import tempfile
import threading
import time
import unittest

import jobstore
import scheduler


class HeavySchedulerTest(unittest.TestCase):
    def test_a_slot_waits_its_turn_and_holds_the_worker(self):
        release = threading.Event()
        started = threading.Event()
        order = []

        def worker(job_id, **_kwargs):
            if job_id == "j1":
                started.set()
                release.wait(2)
            order.append(job_id)

        queue = scheduler.HeavyScheduler(worker)
        queue.submit("j1", "u", "/d", ("j1",))
        self.assertTrue(started.wait(1))
        holding = threading.Event()
        leave = threading.Event()

        def chat_turn():
            with queue.slot("chat", "u", "/d") as cancel:
                order.append("chat")
                self.assertFalse(cancel.is_set())
                holding.set()
                leave.wait(2)

        thread = threading.Thread(target=chat_turn)
        thread.start()
        deadline = time.time() + 1
        while queue.position("chat", "u", "/d") != 1 and time.time() < deadline:
            time.sleep(0.01)
        self.assertEqual(1, queue.position("chat", "u", "/d"))
        queue.submit("j2", "u", "/d", ("j2",))
        release.set()
        self.assertTrue(holding.wait(2))
        time.sleep(0.1)
        self.assertEqual(["j1", "chat"], order)  # j2 waits while the slot is held
        self.assertEqual(0, queue.position("chat", "u", "/d"))
        self.assertEqual("running", queue.cancel("chat", "u", "/d"))
        leave.set()
        thread.join(2)
        deadline = time.time() + 2
        while len(order) < 3 and time.time() < deadline:
            time.sleep(0.01)
        queue.stop()
        self.assertEqual(["j1", "chat", "j2"], order)

    def test_a_slot_cancelled_while_queued_never_runs(self):
        release = threading.Event()
        started = threading.Event()

        def worker(job_id, **_kwargs):
            started.set()
            release.wait(2)

        queue = scheduler.HeavyScheduler(worker)
        queue.submit("j1", "u", "/d", ("j1",))
        self.assertTrue(started.wait(1))
        outcome = []

        def chat_turn():
            try:
                with queue.slot("chat", "u", "/d"):
                    outcome.append("ran")
            except scheduler.QueueCancelled:
                outcome.append("cancelled")

        thread = threading.Thread(target=chat_turn)
        thread.start()
        deadline = time.time() + 1
        while queue.position("chat", "u", "/d") != 1 and time.time() < deadline:
            time.sleep(0.01)
        self.assertEqual("queued", queue.cancel("chat", "u", "/d"))
        thread.join(2)
        release.set()
        queue.stop()
        self.assertEqual(["cancelled"], outcome)

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