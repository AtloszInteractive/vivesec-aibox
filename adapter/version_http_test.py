"""Release identity (E01): build_info loading, the status version block and the
side-effect-free /api/v1/version endpoint used by the fleet overview."""
import json
import os
import tempfile
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from unittest.mock import patch

import build_info

STAMP = {"schema": 1, "version": "26.10.1", "commit": "a" * 40, "dirty": False,
         "tagged": True, "label": "26.10.1", "built_at": "2026-10-09T09:00:00Z"}


class BuildInfoLoadTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.stamp = os.path.join(self.temp.name, "build_info.json")
        self.version = os.path.join(self.temp.name, "VERSION")

    def test_stamped_record_is_reported(self):
        with open(self.stamp, "w", encoding="utf-8") as handle:
            json.dump(STAMP, handle)
        info = build_info.load(self.stamp, self.version)
        self.assertEqual(info["label"], "26.10.1")
        self.assertEqual(info["commit"], "a" * 40)
        self.assertTrue(info["tagged"])

    def test_unstamped_checkout_reports_a_dev_build(self):
        with open(self.version, "w", encoding="utf-8") as handle:
            handle.write("26.10.1\n")
        info = build_info.load(self.stamp, self.version)
        self.assertEqual(info["version"], "26.10.1")
        self.assertEqual(info["label"], "26.10.1-dev")
        self.assertIsNone(info["commit"])

    def test_malformed_stamp_never_raises(self):
        with open(self.stamp, "w", encoding="utf-8") as handle:
            handle.write("{not json")
        info = build_info.load(self.stamp, self.version)
        self.assertEqual(info["label"], "unknown")
        self.assertIsNone(info["version"])


class VersionEndpointTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        with patch.dict(os.environ, {
            "ADAPTER_PKI_DIR": cls.temp.name + "/pki",
            "ADAPTER_FILES_DIR": cls.temp.name + "/files",
            "ADAPTER_FEEDBACK_DIR": cls.temp.name + "/feedback",
            "ADAPTER_JOBS_DIR": cls.temp.name + "/jobs",
            "ADAPTER_SESSION_DIR": cls.temp.name + "/sessions",
            "ADAPTER_STORAGE_MODE": "off",
            "ADAPTER_META_PATH": "",
        }):
            import service
        cls.service = service

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def setUp(self):
        self.rag_build = dict(STAMP)
        build = patch.object(build_info, "BUILD", dict(STAMP))
        build.start()
        self.addCleanup(build.stop)
        rag = patch.object(self.service, "rag_get", side_effect=self.fake_rag_get)
        self.rag = rag.start()
        self.addCleanup(rag.stop)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self.service.Handler)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def fake_rag_get(self, path, timeout=600):
        if self.rag_build is None:
            raise OSError("rag down")
        if path == "/stats":
            return {"ok": True, "stats": {"documents": 0}, "version": self.rag_build}
        return {"ok": True, "status": "ok", "version": self.rag_build}

    def call(self, path, method="GET"):
        request = urllib.request.Request(
            "http://127.0.0.1:%d%s" % (self.server.server_port, path),
            data=b"{}" if method == "POST" else None, method=method,
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=5) as response:
            return json.load(response)

    def test_status_carries_the_release_identity(self):
        status = self.call("/api/v1/status", "POST")
        version = status["version"]
        self.assertEqual(version["release"], "26.10.1")
        self.assertEqual(version["commit"], "a" * 40)
        self.assertTrue(version["consistent"])
        self.assertEqual(version["components"]["rag"]["label"], "26.10.1")
        self.assertEqual(version["ui_version"], self.service.UI_VERSION)
        self.assertIs(status["paired"], False)

    def test_partial_redeploy_is_reported_inconsistent(self):
        self.rag_build = dict(STAMP, commit="b" * 40, label="26.10.1-dev+bbbbbbb")
        version = self.call("/api/v1/version")["version"]
        self.assertFalse(version["consistent"])
        self.assertEqual(version["components"]["rag"]["commit"], "b" * 40)

    def test_unreachable_rag_is_not_consistent(self):
        self.rag_build = None
        payload = self.call("/api/v1/version")
        self.assertTrue(payload["ok"])
        self.assertIsNone(payload["version"]["components"]["rag"])
        self.assertFalse(payload["version"]["consistent"])

    def test_version_poll_is_not_a_presence_poll(self):
        # The fleet overview polls this endpoint; it must never keep the
        # presence watchdog alive in place of the ViVeSecBox.
        self.service._LAST_STATUS_TS[0] = 0.0
        armed = self.service._WD_ARMED[0]
        for method in ("GET", "POST"):
            payload = self.call("/api/v1/version", method)
            self.assertEqual(payload["version"]["release"], "26.10.1")
            self.assertIn("paired", payload)
            self.assertIn("connected", payload["ws_fs"])
        self.assertEqual(self.service._LAST_STATUS_TS[0], 0.0)
        self.assertEqual(self.service._WD_ARMED[0], armed)
        self.assertEqual([c.args[0] for c in self.rag.call_args_list], ["/health", "/health"])


if __name__ == "__main__":
    unittest.main()
