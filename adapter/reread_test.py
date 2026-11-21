"""The reread queue (reread.py): a failing file does not stop the queue, and a
full queue refuses instead of growing."""
import unittest

import reread


class RereaderTest(unittest.TestCase):
    def test_a_failure_is_recorded_and_the_next_file_still_runs(self):
        done = []

        def index(path):
            if path == "bad":
                raise RuntimeError("storage locked")
            done.append(path)

        r = reread.Rereader(index, autostart=False)
        self.assertEqual((["bad", "ok"], []), r.submit(["bad", "ok"]))
        while r.run_once():
            pass
        self.assertEqual(["ok"], done)
        self.assertEqual(("failed", "storage locked"),
                         (r.status("bad")["state"], r.status("bad")["error"]))
        # A failed file may be queued again.
        self.assertEqual((["bad"], []), r.submit(["bad"]))

    def test_a_full_queue_refuses(self):
        r = reread.Rereader(lambda path: None, max_queued=1, autostart=False)
        self.assertEqual((["a"], ["b"]), r.submit(["a", "b"]))

    def test_the_background_worker_drains_the_queue(self):
        r = reread.Rereader(lambda path: None)
        r.submit(["a", "b"])
        self.assertTrue(r.wait_idle(5))
        self.assertEqual("done", r.status("b")["state"])


if __name__ == "__main__":
    unittest.main()
