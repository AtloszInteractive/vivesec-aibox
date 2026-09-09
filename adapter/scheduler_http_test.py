"""HTTP integration test for heavyweight queueing and cancellation."""
import base64
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import jobstore
import scheduler
import service
import session


def vvs(path):
    return base64.urlsafe_b64encode(path.encode("utf-8")).decode("ascii").rstrip("=")


class SchedulerHttpTest(unittest.TestCase):
    def setUp(self):
        self.release = threading.Event()
        self.started = threading.Event()
        self.interactive_started = threading.Event()
        service.JOBS = jobstore.JobStore(tempfile.mkdtemp(prefix="vivesec-http-jobs-"))
        service.SESSIONS = session.SessionManager()

        def worker(job_id, user, drive, *_args, **_kwargs):
            service.JOBS.start(user, drive, job_id)
            self.started.set()
            self.release.wait(3)
            service.JOBS.finish(user, drive, job_id, 200,
                                {"ok": True, "answer": job_id})

        service.HEAVY_SCHEDULER = scheduler.HeavyScheduler(
            worker, max_queued_per_user=1)
        self.original_job_worker = service._job_worker

        def interactive_worker(job_id, user, drive, *_args):
            service.JOBS.start(user, drive, job_id)
            self.interactive_started.set()
            service.JOBS.finish(user, drive, job_id, 200,
                                {"ok": True, "answer": "interactive"})

        service._job_worker = interactive_worker
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), service.Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = "http://127.0.0.1:%d" % self.server.server_port
        self.headers = {"VVS-Drive": vvs("/storage/drives/finance/"),
                        "VVS-User": "queue-user"}

    def tearDown(self):
        self.release.set()
        service._job_worker = self.original_job_worker
        service.HEAVY_SCHEDULER.stop()
        self.server.shutdown()
        self.server.server_close()

    def request(self, path, body=None, method="POST"):
        data = None if body is None else json.dumps(body).encode("utf-8")
        request = urllib.request.Request(self.base + path, data=data, method=method)
        request.add_header("Content-Type", "application/json")
        for key, value in self.headers.items():
            request.add_header(key, value)
        try:
            with urllib.request.urlopen(request, timeout=2) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read().decode("utf-8"))

    def test_queue_limit_positions_and_cancel(self):
        service.SESSIONS.record("queue-user", "/storage/drives/finance/",
                                "earlier question", "earlier answer")
        code, first = self.request("/api/v1/ui/ask",
                                   {"action": "report", "query": "first",
                                    "origin": "background"})
        self.assertEqual(code, 202)
        self.assertTrue(self.started.wait(1))

        code, second = self.request("/api/v1/ui/ask",
                                    {"action": "report", "query": "second",
                                     "origin": "background"})
        self.assertEqual(code, 202)
        self.assertEqual(second["queue_position"], 1)
        service.SESSIONS.record("queue-user", "/storage/drives/finance/",
                                "later question", "later answer")
        stored = service.JOBS.get("queue-user", "/storage/drives/finance/",
                                  second["job_id"])
        history = stored["request"]["history"]
        self.assertEqual([turn["content"] for turn in history],
                         ["earlier question", "earlier answer"])

        code, interactive = self.request("/api/v1/ui/ask", {"query": "chat now"})
        self.assertEqual(code, 202)
        self.assertIsNone(interactive["queue_position"])
        self.assertTrue(self.interactive_started.wait(1))
        self.assertEqual(service.JOBS.get(
            "queue-user", "/storage/drives/finance/", interactive["job_id"]
        )["status"], "done")

        code, rejected = self.request("/api/v1/ui/ask",
                                      {"action": "report", "query": "third",
                                       "origin": "background"})
        self.assertEqual(code, 429)
        self.assertIn("too many queued", rejected["error"])

        code, listing = self.request("/api/v1/ui/jobs", method="GET")
        self.assertEqual(code, 200)
        queued = next(job for job in listing["jobs"]
                      if job["job_id"] == second["job_id"])
        self.assertEqual(queued["status"], "queued")
        self.assertEqual(queued["queue_position"], 1)
        self.assertNotIn(interactive["job_id"],
                         [job["job_id"] for job in listing["jobs"]])

        code, cancelled = self.request("/api/v1/ui/jobs/cancel",
                                       {"job_id": second["job_id"]})
        self.assertEqual((code, cancelled["status"]), (200, "cancelled"))
        code, polled = self.request("/api/v1/ui/poll",
                                    {"job_id": second["job_id"], "timeout": 0})
        self.assertEqual((code, polled["status"]), (200, "cancelled"))

        code, running = self.request("/api/v1/ui/jobs/cancel",
                                     {"job_id": first["job_id"]})
        self.assertEqual((code, running["status"], running.get("cancel_requested")),
                 (202, "running", True))


if __name__ == "__main__":
    unittest.main()