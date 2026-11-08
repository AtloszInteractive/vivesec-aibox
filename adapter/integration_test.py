"""E27 integration mode: the ADAPTER_INTEGRATION switch, the standalone start-up
check, and the ViVeSecBox surfaces (pairing, index sync, ws-fs, SSDP, mutual
TLS) that standalone mode turns off."""
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest.mock import MagicMock, patch

import integration

HERE = os.path.dirname(os.path.abspath(__file__))
STANDALONE_ENV = {"ADAPTER_INTEGRATION": "standalone",
                  "ADAPTER_IDENTITY_MODE": "require",
                  "ADAPTER_DIRECTORY": "token"}


class ParseModeTest(unittest.TestCase):
    def test_unset_or_empty_is_vivesecbox(self):
        for raw in (None, "", "   "):
            self.assertEqual(integration.parse_mode(raw), integration.MODE_VIVESECBOX)

    def test_known_values_are_case_and_space_insensitive(self):
        self.assertEqual(integration.parse_mode(" Standalone "), integration.MODE_STANDALONE)
        self.assertEqual(integration.parse_mode("VIVESECBOX"), integration.MODE_VIVESECBOX)

    def test_unknown_value_never_falls_back(self):
        for raw in ("offline", "standalon", "on", "off", "vivesec"):
            with self.assertRaises(integration.ConfigError) as ctx:
                integration.parse_mode(raw)
            self.assertIn("ADAPTER_INTEGRATION", str(ctx.exception))


class FromEnvTest(unittest.TestCase):
    def test_vivesecbox_mode_keeps_todays_defaults(self):
        self.assertEqual(integration.from_env({}), integration.MODE_VIVESECBOX)
        self.assertEqual(integration.from_env({"ADAPTER_IDENTITY_MODE": "off",
                                               "ADAPTER_DIRECTORY": "off",
                                               "ADAPTER_SCOPE_ALL_DRIVES": "on"}),
                         integration.MODE_VIVESECBOX)

    def test_standalone_with_its_preconditions_starts(self):
        self.assertEqual(integration.from_env(STANDALONE_ENV), integration.MODE_STANDALONE)
        self.assertEqual(integration.from_env(dict(STANDALONE_ENV,
                                                   ADAPTER_IDENTITY_MODE=" REQUIRE ",
                                                   ADAPTER_DIRECTORY="Token",
                                                   ADAPTER_SCOPE_ALL_DRIVES="off")),
                         integration.MODE_STANDALONE)

    def test_standalone_requires_identity_mode_require(self):
        # An unknown identity mode falls back to require inside identity.py, but
        # standalone mode wants the setting spelled out.
        for value in (None, "", "off", "verify", "requir"):
            env = dict(STANDALONE_ENV)
            if value is None:
                del env["ADAPTER_IDENTITY_MODE"]
            else:
                env["ADAPTER_IDENTITY_MODE"] = value
            with self.assertRaises(integration.ConfigError) as ctx:
                integration.from_env(env)
            self.assertIn("ADAPTER_IDENTITY_MODE", str(ctx.exception))

    def test_standalone_requires_token_directory(self):
        for value in (None, "", "off", "entra", "tokens"):
            env = dict(STANDALONE_ENV)
            if value is None:
                del env["ADAPTER_DIRECTORY"]
            else:
                env["ADAPTER_DIRECTORY"] = value
            with self.assertRaises(integration.ConfigError) as ctx:
                integration.from_env(env)
            self.assertIn("ADAPTER_DIRECTORY", str(ctx.exception))

    def test_standalone_refuses_the_all_drives_demo_switch(self):
        with self.assertRaises(integration.ConfigError) as ctx:
            integration.from_env(dict(STANDALONE_ENV, ADAPTER_SCOPE_ALL_DRIVES="yes"))
        self.assertIn("ADAPTER_SCOPE_ALL_DRIVES", str(ctx.exception))

    def test_every_problem_is_reported_at_once(self):
        with self.assertRaises(integration.ConfigError) as ctx:
            integration.from_env({"ADAPTER_INTEGRATION": "standalone",
                                  "ADAPTER_SCOPE_ALL_DRIVES": "1"})
        message = str(ctx.exception)
        for name in ("ADAPTER_IDENTITY_MODE", "ADAPTER_DIRECTORY", "ADAPTER_SCOPE_ALL_DRIVES"):
            self.assertIn(name, message)


class VivesecboxPathTest(unittest.TestCase):
    def test_pairing_sync_and_wsfs_are_vivesecbox_surfaces(self):
        for path in ("/api/v1/init/prepare", "/api/v1/init/commit", "/api/v1/init/commit/",
                     "/api/v1/init", "/api/v1/init/other",
                     "/api/v1/ws/fs", "/api/v1/ws/fs/",
                     "/api/v1/index", "/api/v1/index/get", "/api/v1/index/get/children",
                     "/api/v1/index/upsert/directory", "/api/v1/index/upsert/file/check",
                     "/api/v1/index/upsert/file/content/abc123",
                     "/api/v1/index/upsert/acl", "/api/v1/index/drop/tree"):
            self.assertTrue(integration.is_vivesecbox_path(path), path)

    def test_box_endpoints_are_not(self):
        for path in ("", "/", "/api/v1/status", "/api/v1/version", "/api/v1/ui-version",
                     "/api/v1/ui/init", "/api/v1/ui/query", "/api/v1/ui/index/skipped",
                     "/api/v1/storage/unlock", "/api/v1/identity/lifecycle",
                     "/api/v1/admin/acl/effective", "/api/v1/indexer", "/api/v1/ws/fsx"):
            self.assertFalse(integration.is_vivesecbox_path(path), path)

    def test_surfaces_follow_the_mode(self):
        self.assertTrue(integration.vivesecbox_surfaces(integration.MODE_VIVESECBOX))
        self.assertFalse(integration.vivesecbox_surfaces(integration.MODE_STANDALONE))


class StartupRefusalTest(unittest.TestCase):
    """The real entry point: a bad configuration exits before anything binds."""

    def run_service(self, extra):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        env = {key: value for key, value in os.environ.items()
               if not key.startswith(("ADAPTER_", "RAG_", "GRAPH_"))}
        env.update({"ADAPTER_PKI_DIR": os.path.join(temp.name, "pki"),
                    "ADAPTER_FILES_DIR": os.path.join(temp.name, "files"),
                    "ADAPTER_FEEDBACK_DIR": os.path.join(temp.name, "feedback"),
                    "ADAPTER_JOBS_DIR": os.path.join(temp.name, "jobs"),
                    "ADAPTER_SESSION_DIR": os.path.join(temp.name, "sessions"),
                    "ADAPTER_STORAGE_MODE": "off", "ADAPTER_DISCOVERY": "off",
                    "ADAPTER_PORT": "1", "PYTHONIOENCODING": "utf-8"})
        env.update(extra)
        return subprocess.run([sys.executable, os.path.join(HERE, "service.py")],
                              cwd=HERE, env=env, capture_output=True, text=True,
                              encoding="utf-8", timeout=120)

    def test_unknown_mode_refuses_to_start(self):
        result = self.run_service({"ADAPTER_INTEGRATION": "offline"})
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("refusing to start", result.stderr)
        self.assertIn("ADAPTER_INTEGRATION", result.stderr)
        self.assertNotIn("adapter on http", result.stdout)

    def test_standalone_without_its_preconditions_refuses_to_start(self):
        result = self.run_service({"ADAPTER_INTEGRATION": "standalone",
                                   "ADAPTER_IDENTITY_MODE": "off",
                                   "ADAPTER_DIRECTORY": "token"})
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("ADAPTER_IDENTITY_MODE", result.stderr)
        self.assertNotIn("adapter on http", result.stdout)


class ServiceModeTest(unittest.TestCase):
    """Route gating and start-up wiring of service.py in both modes."""

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

    def use_mode(self, mode):
        patcher = patch.object(self.service, "INTEGRATION", mode)
        patcher.start()
        self.addCleanup(patcher.stop)

    def start_server(self):
        for name, value in (("HTTP_SCOPE", self.service.HTTP_SCOPE_FULL),):
            patcher = patch.object(self.service, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        rag = patch.object(self.service, "rag_get", return_value={"ok": True, "stats": {}})
        rag.start()
        self.addCleanup(rag.stop)
        server = ThreadingHTTPServer(("127.0.0.1", 0), self.service.Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.base = "http://127.0.0.1:%d" % server.server_address[1]

    def call(self, path, body=None, method=None, headers=None):
        data = None if body is None else json.dumps(body).encode("utf-8")
        request = urllib.request.Request(self.base + path, data=data, method=method,
                                         headers=dict(headers or {}))
        if data is not None:
            request.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8")
            return e.code, (json.loads(raw) if raw else {})

    # -- HTTP --------------------------------------------------------------------
    def test_default_mode_is_vivesecbox(self):
        self.assertEqual(self.service.INTEGRATION, integration.MODE_VIVESECBOX)

    def test_standalone_refuses_every_vivesecbox_endpoint(self):
        self.use_mode(integration.MODE_STANDALONE)
        self.start_server()
        refused = [("GET", "/api/v1/init/prepare", None),
                   ("POST", "/api/v1/init/commit", {"server_crt": "", "ca_crt": ""}),
                   ("GET", "/api/v1/ws/fs", None),
                   ("POST", "/api/v1/index/get", {"path": "/storage/drives/a/x.txt"}),
                   ("POST", "/api/v1/index/get/children", {"path": "/storage/drives/a/"}),
                   ("POST", "/api/v1/index/upsert/directory", {"path": "/storage/drives/a/"}),
                   ("POST", "/api/v1/index/upsert/file/check", {"path": "/storage/drives/a/x"}),
                   ("POST", "/api/v1/index/upsert/file/content/tok", {}),
                   ("POST", "/api/v1/index/upsert/acl", {"path": "/storage/drives/a/"}),
                   ("POST", "/api/v1/index/drop/tree", {"path": "/storage/drives/a/"}),
                   ("POST", "/api/v1/index/drop/tree/", {"path": "/storage/drives/a/"})]
        with patch.object(self.service.PROVISIONER, "prepare") as prepare, \
                patch.object(self.service.PROVISIONER, "commit") as commit, \
                patch.object(self.service, "rag_post_json") as rag_post:
            for method, path, body in refused:
                status, payload = self.call(path, body, method=method,
                                            headers={"Upgrade": "websocket",
                                                     "Sec-WebSocket-Key": "x" * 24})
                self.assertEqual(status, 404, path)
                self.assertEqual(payload.get("error"), "not available in standalone mode", path)
            prepare.assert_not_called()
            commit.assert_not_called()
            rag_post.assert_not_called()

    def test_standalone_keeps_box_endpoints_and_reports_the_mode(self):
        self.use_mode(integration.MODE_STANDALONE)
        self.start_server()
        status, payload = self.call("/api/v1/status")
        self.assertEqual(status, 200)
        self.assertEqual(payload["integration"], "standalone")
        status, payload = self.call("/api/v1/version")
        self.assertEqual(status, 200)
        self.assertEqual(payload["integration"], "standalone")
        status, _payload = self.call("/api/v1/ui/query", {"query": "x"})
        self.assertNotEqual(status, 404)

    def test_vivesecbox_mode_still_serves_the_sync(self):
        self.use_mode(integration.MODE_VIVESECBOX)
        self.start_server()
        status, payload = self.call("/api/v1/index/get", {"path": "/storage/drives/none/x.txt"})
        self.assertEqual(status, 200)
        self.assertIsNone(payload)
        status, payload = self.call("/api/v1/status")
        self.assertEqual(payload["integration"], "vivesecbox")

    # -- start-up wiring ---------------------------------------------------------
    def test_tls_listener_is_never_built_in_standalone(self):
        self.use_mode(integration.MODE_STANDALONE)
        with patch.object(self.service, "ThreadingHTTPServer") as server_cls:
            self.assertIsNone(self.service._ensure_tls_listener())
        server_cls.assert_not_called()

    def run_main(self):
        """main() with nothing bound or started: the HTTP server is a stub whose
        serve_forever returns at once, threads are recorded, not run."""
        started = []

        def fake_thread(target=None, **_kwargs):
            started.append(target)
            return MagicMock()

        server = MagicMock()
        server.serve_forever.side_effect = KeyboardInterrupt
        responder = MagicMock(st="urn:test", current_location=lambda: "http://x")
        with patch.object(self.service, "ThreadingHTTPServer", return_value=server), \
                patch.object(self.service.threading, "Thread", side_effect=fake_thread), \
                patch.object(self.service.discovery, "from_env",
                             return_value=responder) as discovery_env, \
                patch.object(self.service, "TLS_ENABLED", True), \
                patch.object(self.service.PROVISIONER, "is_initialized", return_value=True), \
                patch.object(self.service, "_ensure_tls_listener") as tls, \
                patch("builtins.print"):
            self.service.main()
        return discovery_env, tls, started, responder

    def test_main_skips_ssdp_and_tls_in_standalone(self):
        self.use_mode(integration.MODE_STANDALONE)
        discovery_env, tls, started, responder = self.run_main()
        discovery_env.assert_not_called()
        tls.assert_not_called()
        self.assertNotIn(responder.run, started)

    def test_main_keeps_ssdp_and_tls_in_vivesecbox_mode(self):
        self.use_mode(integration.MODE_VIVESECBOX)
        discovery_env, tls, started, responder = self.run_main()
        discovery_env.assert_called_once()
        tls.assert_called_once()
        self.assertIn(responder.run, started)


if __name__ == "__main__":
    unittest.main()
