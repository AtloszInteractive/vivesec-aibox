"""Release identity (E01): /health and /stats report the stamped build."""
import json
import os
import tempfile
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from unittest.mock import patch

import build_info


class RagVersionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        with patch.dict(os.environ, {"RAG_STORE_BACKEND": "json",
                                     "RAG_INDEX_PATH": os.path.join(cls.temp.name, "index.json"),
                                     "RAG_API_KEY": ""}):
            import service
        cls.service = service

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def setUp(self):
        stamp = {"version": "26.10.1", "commit": "c" * 40, "dirty": False, "tagged": True,
                 "label": "26.10.1", "built_at": "2026-10-09T09:00:00Z"}
        guard = patch.object(build_info, "BUILD", stamp)
        guard.start()
        self.addCleanup(guard.stop)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self.service.Handler)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def get(self, path):
        url = "http://127.0.0.1:%d%s" % (self.server.server_port, path)
        with urllib.request.urlopen(url, timeout=5) as response:
            return json.load(response)

    def test_health_and_stats_report_the_build(self):
        for path in ("/health", "/stats"):
            payload = self.get(path)
            self.assertEqual(payload["version"]["label"], "26.10.1", path)
            self.assertEqual(payload["version"]["commit"], "c" * 40, path)
        self.assertIn("stats", self.get("/stats"))


if __name__ == "__main__":
    unittest.main()
