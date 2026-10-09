"""E02 over HTTP: the identity gate on user endpoints, the lifecycle endpoint,
the pairing-only plain-HTTP scope and job cancellation for a locked user."""
import base64
import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest.mock import patch

import directory
import identity
import jobstore
import lifecycle

DRIVE = "/storage/drives/finance/"
SECRET = b"k" * 40
NOW = 1_800_000_000


def b64(text):
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii")


class IdentityHttpTest(unittest.TestCase):
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
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        secret_file = os.path.join(self.dir.name, "secret")
        with open(secret_file, "wb") as handle:
            handle.write(SECRET)
        self.verifier = identity.Verifier(mode=identity.MODE_OFF, hs256_secret_file=secret_file,
                                          audience="aibox", clock=lambda: NOW)
        self.lifecycle = lifecycle.LifecycleStore(os.path.join(self.dir.name, "lifecycle.json"))
        self.jobs = jobstore.JobStore(os.path.join(self.dir.name, "jobs"))
        for name, value in (("IDENTITY", self.verifier), ("LIFECYCLE", self.lifecycle),
                            ("DIRECTORY", directory.TokenDirectory()), ("JOBS", self.jobs),
                            ("HTTP_SCOPE", self.service.HTTP_SCOPE_FULL)):
            patcher = patch.object(self.service, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        rag = patch.object(self.service, "rag_get", return_value={"ok": True, "stats": {}})
        rag.start()
        self.addCleanup(rag.stop)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self.service.Handler)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def remote_peer(self):
        patcher = patch.object(self.service.Handler, "_peer_is_loopback", return_value=False)
        patcher.start()
        self.addCleanup(patcher.stop)

    def call(self, path, body=None, method=None, headers=None):
        data = None if body is None else json.dumps(body).encode("utf-8")
        request = urllib.request.Request(
            "http://127.0.0.1:%d%s" % (self.server.server_port, path), data=data,
            method=method or ("POST" if data is not None else "GET"),
            headers=dict({"Content-Type": "application/json"}, **(headers or {})))
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                raw = response.read()
                status = response.status
        except urllib.error.HTTPError as e:
            raw, status = e.read(), e.code
        try:
            return status, json.loads(raw.decode("utf-8"))
        except ValueError:
            return status, raw.decode("utf-8", "replace")

    def user_headers(self, user="lars", token=None, **extra):
        headers = {"VVS-Drive": b64(DRIVE), "VVS-User": user}
        if token:
            headers["VVS-Identity"] = token
        headers.update(extra)
        return headers

    def token(self, **claims):
        base = {"sub": "lars", "upn": "lars@example.com", "aud": "aibox",
                "iat": NOW, "exp": NOW + 300, "groups": ["g-finance"]}
        base.update(claims)
        return identity.sign_hs256(base, SECRET)

    # -- identity gate ---------------------------------------------------------
    def test_off_mode_keeps_the_header_identity(self):
        status, body = self.call("/api/v1/ui/init", headers=self.user_headers("anna"))
        self.assertEqual(status, 200)
        self.assertEqual(body["user"], "anna")
        self.assertEqual(body["identity"]["source"], "header")

    def test_require_mode_rejects_requests_without_a_token(self):
        self.verifier.mode = identity.MODE_REQUIRE
        for path, body in (("/api/v1/ui/init", None), ("/api/v1/ui/scope", None),
                           ("/api/v1/ui/files", None), ("/api/v1/ui/jobs", None),
                           ("/api/v1/ui/query", {"query": "hello"}),
                           ("/api/v1/ui/ask", {"query": "hello"}),
                           ("/api/v1/ui/conversations/list", {}),
                           ("/api/v1/ui/tts", {"text": "hello"})):
            status, payload = self.call(path, body, headers=self.user_headers())
            self.assertEqual(status, 401, path)
            self.assertEqual(payload["error"], "identity not accepted")

    def test_valid_token_carries_identity_and_groups(self):
        self.verifier.mode = identity.MODE_REQUIRE
        status, body = self.call("/api/v1/ui/init",
                                 headers=self.user_headers(token=self.token()))
        self.assertEqual(status, 200)
        self.assertEqual(body["user"], "lars")
        self.assertEqual(body["identity"]["source"], "token")
        self.assertEqual(body["identity"]["upn"], "lars@example.com")
        self.assertEqual(body["identity"]["groups"], ["g-finance"])

    def test_forged_or_mismatched_identity_is_rejected(self):
        self.verifier.mode = identity.MODE_VERIFY
        forged = identity.sign_hs256({"sub": "lars", "aud": "aibox", "exp": NOW + 60},
                                     b"z" * 40)
        status, _ = self.call("/api/v1/ui/init", headers=self.user_headers(token=forged))
        self.assertEqual(status, 401)
        status, _ = self.call("/api/v1/ui/init",
                              headers=self.user_headers("admin", token=self.token()))
        self.assertEqual(status, 401)
        status, _ = self.call("/api/v1/ui/init", headers=self.user_headers(
            token=self.token(drive="/storage/drives/hr/")))
        self.assertEqual(status, 403)

    def test_token_drives_replace_the_unsigned_header(self):
        self.verifier.mode = identity.MODE_REQUIRE
        headers = self.user_headers(token=self.token(drives=["/storage/drives/hr/"]))
        headers["VVS-Other-Drives"] = b64("/storage/drives/board/")
        status, body = self.call("/api/v1/ui/scope", headers=headers)
        self.assertEqual(status, 200)
        self.assertEqual(body["source"], "token")
        self.assertEqual([d["path"] for d in body["drives"]],
                         ["/storage/drives/finance", "/storage/drives/hr"])
        status, _ = self.call("/api/v1/ui/conversations/list",
                              {"drives": ["/storage/drives/board/"]}, headers=headers)
        self.assertEqual(status, 403)

    # -- lifecycle -------------------------------------------------------------
    def test_lifecycle_lock_and_reinstate(self):
        status, body = self.call("/api/v1/identity/lifecycle",
                                 {"event": "suspended", "user": "lars"})
        self.assertEqual((status, body["state"]), (200, "suspended"))
        for path, payload in (("/api/v1/ui/init", None), ("/api/v1/ui/jobs", None),
                              ("/api/v1/ui/ask", {"query": "x"}),
                              ("/api/v1/ui/conversations/list", {}),
                              ("/api/v1/ui/files/download?name=a.txt", None),
                              ("/api/v1/ui/stt", {"audio_b64": "AA=="})):
            status, body = self.call(path, payload, headers=self.user_headers())
            self.assertEqual(status, 403, path)
            self.assertEqual(body["error"], "user access locked")
        self.assertEqual(self.call("/api/v1/ui/init", headers=self.user_headers("anna"))[0], 200)
        status, body = self.call("/api/v1/identity/lifecycle",
                                 {"event": "reinstated", "user": "lars"})
        self.assertEqual((status, body["state"]), (200, "active"))
        self.assertEqual(self.call("/api/v1/ui/init", headers=self.user_headers())[0], 200)

    def test_lifecycle_lock_matches_the_token_upn(self):
        self.verifier.mode = identity.MODE_REQUIRE
        self.call("/api/v1/identity/lifecycle",
                  {"event": "revoked", "user": "other-id", "upn": "lars@example.com"})
        status, _ = self.call("/api/v1/ui/init", headers=self.user_headers(token=self.token()))
        self.assertEqual(status, 403)

    def test_deleted_user_cannot_be_reinstated(self):
        self.call("/api/v1/identity/lifecycle", {"event": "deleted", "user": "lars"})
        status, _ = self.call("/api/v1/identity/lifecycle",
                              {"event": "reinstated", "user": "lars"})
        self.assertEqual(status, 409)

    def test_lifecycle_validation(self):
        self.assertEqual(self.call("/api/v1/identity/lifecycle",
                                   {"event": "archived", "user": "lars"})[0], 400)
        self.assertEqual(self.call("/api/v1/identity/lifecycle", {"event": "deleted"})[0], 400)
        self.assertEqual(self.call("/api/v1/identity/lifecycle", ["deleted"])[0], 400)

    def test_lifecycle_requires_a_trusted_channel(self):
        self.remote_peer()
        status, body = self.call("/api/v1/identity/lifecycle",
                                 {"event": "deleted", "user": "lars"})
        self.assertEqual(status, 403)
        self.assertIsNone(self.lifecycle.state_of(["lars"]))

    def test_lifecycle_relayed_by_a_local_proxy_is_refused(self):
        # The box-hosted UI relays browser calls over loopback; such a call
        # comes from the network and must not count as a local process.
        for header in ("X-Forwarded-Host", "X-Forwarded-For", "Forwarded", "X-Real-IP"):
            status, _ = self.call("/api/v1/identity/lifecycle",
                                  {"event": "deleted", "user": "lars"},
                                  headers={header: "box:8080"})
            self.assertEqual(status, 403, header)
        self.assertIsNone(self.lifecycle.state_of(["lars"]))
        with patch.object(self.service, "HTTP_SCOPE", self.service.HTTP_SCOPE_PAIRING):
            status, _ = self.call("/api/v1/ui/init", headers=self.user_headers(
                **{"X-Forwarded-Host": "box:8080"}))
        self.assertEqual(status, 403)

    def test_lock_cancels_the_users_jobs(self):
        self.jobs.create("job-1", "lars", DRIVE, {})
        self.jobs.create("job-2", "anna", DRIVE, {})
        with patch.object(self.service.HEAVY_SCHEDULER, "cancel_user", return_value=1) as heavy:
            status, body = self.call("/api/v1/identity/lifecycle",
                                     {"event": "suspended", "user": "lars",
                                      "upn": "lars@example.com"})
        self.assertEqual((status, body["cancelled_jobs"]), (200, 1))
        heavy.assert_called_once_with(["lars", "lars@example.com"])
        self.assertEqual(self.jobs.get("lars", DRIVE, "job-1")["status"], "cancelled")
        self.assertEqual(self.jobs.get("anna", DRIVE, "job-2")["status"], "queued")

    def test_queued_job_of_a_locked_user_never_runs(self):
        self.jobs.create("job-3", "lars", DRIVE, {"scope_drives": [DRIVE]})
        self.lifecycle.apply("suspended", "lars")
        with patch.object(self.service, "_answer") as answer:
            self.service._job_worker("job-3", "lars", DRIVE, "q", 4, None)
        answer.assert_not_called()
        self.assertEqual(self.jobs.get("lars", DRIVE, "job-3")["status"], "cancelled")

    def test_unreadable_lifecycle_state_refuses_users(self):
        path = os.path.join(self.dir.name, "broken.json")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("{broken")
        with patch.object(self.service, "LIFECYCLE", lifecycle.LifecycleStore(path)):
            status, _ = self.call("/api/v1/ui/init", headers=self.user_headers())
        self.assertEqual(status, 503)

    # -- plain HTTP scope ------------------------------------------------------
    def test_pairing_scope_limits_network_peers(self):
        self.remote_peer()
        with patch.object(self.service, "HTTP_SCOPE", self.service.HTTP_SCOPE_PAIRING):
            self.assertEqual(self.call("/api/v1/status", {})[0], 200)
            self.assertEqual(self.call("/api/v1/status")[0], 200)
            self.assertEqual(self.call("/api/v1/version")[0], 200)
            self.assertEqual(self.call("/api/v1/ui-version")[0], 200)
            self.assertEqual(self.call("/")[0], 200)
            for path, body in (("/api/v1/ui/init", None), ("/api/v1/ui/query", {"query": "x"}),
                               ("/api/v1/index/get", {"path": DRIVE}),
                               ("/api/v1/index/upsert/file/content/tok", {}),
                               ("/api/v1/storage/unlock", {"storage_key": "x"}),
                               ("/api/v1/ws/fs", None)):
                status, payload = self.call(path, body, headers=self.user_headers())
                self.assertEqual(status, 403, path)
                self.assertEqual(payload["error"], "endpoint available over mutual TLS only")

    def test_pairing_scope_keeps_loopback_and_full_scope_is_open(self):
        with patch.object(self.service, "HTTP_SCOPE", self.service.HTTP_SCOPE_PAIRING):
            self.assertEqual(self.call("/api/v1/ui/init", headers=self.user_headers())[0], 200)
        self.remote_peer()
        self.assertEqual(self.call("/api/v1/ui/init", headers=self.user_headers())[0], 200)

    def test_status_reports_the_identity_configuration(self):
        status, body = self.call("/api/v1/status", {})
        self.assertEqual(status, 200)
        self.assertEqual(body["identity"]["mode"], "off")
        self.assertEqual(body["identity"]["http_scope"], "full")
        self.assertEqual(body["identity"]["directory"]["source"], "token")
        self.assertIn("locked", body["identity"]["lifecycle"])


class ChannelHelpersTest(unittest.TestCase):
    def test_http_scope_parsing_fails_closed(self):
        import service
        self.assertEqual(service._parse_http_scope(None), "full")
        self.assertEqual(service._parse_http_scope("Pairing"), "pairing")
        self.assertEqual(service._parse_http_scope("strict"), "pairing")

    def test_loopback_detection(self):
        import service
        handler = object.__new__(service.Handler)
        for address, expected in (("127.0.0.1", True), ("127.8.0.1", True), ("::1", True),
                                  ("::ffff:127.0.0.1", True), ("192.168.0.181", False),
                                  ("::ffff:10.0.0.1", False), ("", False), ("bogus", False)):
            handler.client_address = (address, 1234)
            self.assertEqual(handler._peer_is_loopback(), expected, address)


if __name__ == "__main__":
    unittest.main()
