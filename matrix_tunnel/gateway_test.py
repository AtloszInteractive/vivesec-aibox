import base64
import hashlib
import io
import json
import os
import tempfile
import threading
import unittest
import unittest.mock
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import gateway

FORWARD_KEY = b"forward-key-0123456789abcdef0123456789"
SERVICE_KEY = b"service-key-0123456789abcdef0123456789"
TUNNEL_KEY = b"tunnel-key-0123456789abcdef01234567890"


def serve(handler):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


class GatewayTest(unittest.TestCase):
    def setUp(self):
        self.auth_calls = []
        self.adapter_calls = []
        self.adapter_status = 200
        test = self

        class Auth(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                test.auth_calls.append((dict(self.headers), body))
                keys_ok = (self.headers.get("X-Auth-Service-Key") == SERVICE_KEY.decode()
                           and self.headers.get("X-Auth-Tunnel-Key") == TUNNEL_KEY.decode())
                if not keys_ok:
                    status, answer = 401, {"ok": False}
                elif body["vvs_user"] == "28744CZP27222":
                    status, answer = 200, {"ok": True, "token": "tok-%d" % len(test.auth_calls),
                                           "expires_at": 4102444800,
                                           "user": {"username": "anna",
                                                    "drives": ["/storage/drives/közös/", "/storage/drives/b/"]}}
                else:
                    status, answer = 403, {"ok": False, "error": "tunnel_user_unknown"}
                data = json.dumps(answer).encode()
                self.send_response(status)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        class Adapter(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _reply(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length else b""
                test.adapter_calls.append((self.command, self.path, dict(self.headers), body))
                data = json.dumps({"ok": True, "path": self.path}).encode()
                self.send_response(test.adapter_status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("X-Adapter", "yes")
                self.end_headers()
                self.wfile.write(data)

            do_GET = do_POST = _reply

        self.auth = serve(Auth)
        self.adapter = serve(Adapter)
        for server in (self.auth, self.adapter):
            self.addCleanup(server.server_close)
            self.addCleanup(server.shutdown)
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.env = {"MATRIX_TUNNEL_PORT": "0",
                    "MATRIX_ADAPTER_URL": "http://127.0.0.1:%d" % self.adapter.server_address[1],
                    "MATRIX_AUTH_URL": "http://127.0.0.1:%d" % self.auth.server_address[1]}
        for name, key in (("MATRIX_TUNNEL_FORWARD_KEY_FILE", FORWARD_KEY),
                          ("AUTH_SERVICE_KEY_FILE", SERVICE_KEY), ("AUTH_TUNNEL_KEY_FILE", TUNNEL_KEY)):
            path = os.path.join(temp.name, name.lower())
            with open(path, "wb") as handle:
                handle.write(key + b"\n")
            self.env[name] = path
        self.gateway = gateway.Gateway(gateway.Settings(self.env))
        threading.Thread(target=self.gateway.serve_forever, daemon=True).start()
        self.addCleanup(self.gateway.server_close)
        self.addCleanup(self.gateway.shutdown)
        self.base = "http://127.0.0.1:%d" % self.gateway.server_address[1]

    def call(self, path, body=None, front=True, verify="SUCCESS", user="28744CZP27222", **extra):
        request = urllib.request.Request(self.base + path, method="POST" if body is not None else "GET",
                                         data=body)
        if front:
            request.add_header(gateway.FORWARD_KEY_HEADER, FORWARD_KEY.decode())
        if verify:
            request.add_header(gateway.CLIENT_VERIFY_HEADER, verify)
        if user:
            request.add_header("VVS-User", user)
        request.add_header("VVS-Session", "s1")
        for name, value in extra.items():
            request.add_header(name.replace("_", "-"), value)
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, json.loads(response.read()), dict(response.headers)
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read()), dict(e.headers)

    def test_only_the_front_with_a_verified_client_certificate(self):
        self.assertEqual(self.call("/api/v1/matrix/query", b"{}", front=False)[0], 403)
        self.assertEqual(self.call("/api/v1/matrix/query", b"{}", verify="FAILED:x")[0], 403)
        self.assertEqual(self.call("/api/v1/matrix/query", b"{}", verify=None)[0], 403)
        self.assertEqual((self.auth_calls, self.adapter_calls), ([], []))

    def test_only_matrix_paths_are_relayed(self):
        for path in ("/api/v1/ui/query", "/api/v1/matrix/", "/api/v1/matrix/../admin/acl",
                     "/api/v1/matrix/a//b", "/api/v1/matrix/%2e%2e/x", "/api/v1/matrixquery"):
            self.assertEqual(self.call(path)[0], 404, path)
        self.assertEqual(self.adapter_calls, [])

    def test_relays_with_the_boxs_own_identity(self):
        status, body, headers = self.call(
            "/api/v1/matrix/query?x=1", b'{"query": "hi"}', Content_Type="application/json",
            VVS_Identity="forged", VVS_Drive="L3N0b3JhZ2UvZHJpdmVzL3RoZWlycy8=", X_Demo_Drive="/storage/drives/b/",
            X_Matrix_Peer="192.168.0.20")
        self.assertEqual((status, body["path"], headers.get("X-Adapter")), (200, "/api/v1/ui/query?x=1", "yes"))
        method, path, sent, data = self.adapter_calls[0]
        self.assertEqual((method, data), ("POST", b'{"query": "hi"}'))
        self.assertEqual((sent["VVS-Identity"], sent["VVS-User"]), ("tok-1", "anna"))
        self.assertEqual(base64.urlsafe_b64decode(sent["VVS-Drive"]).decode(), "/storage/drives/közös/")
        self.assertEqual(sent["X-Forwarded-For"], "192.168.0.20")
        for dropped in ("X-Demo-Drive", "X-Matrix-Forward-Key", "VVS-Session"):
            self.assertNotIn(dropped, sent)
        self.assertEqual(self.auth_calls[0][1], {"vvs_user": "28744CZP27222", "vvs_session": "s1"})

    def test_tokens_are_cached_until_the_adapter_refuses_one(self):
        self.call("/api/v1/matrix/files")
        self.call("/api/v1/matrix/scope")
        self.assertEqual(len(self.auth_calls), 1)
        self.assertEqual(self.call("/api/v1/matrix/end")[:2], (200, {"ok": True}))
        self.call("/api/v1/matrix/files")
        self.assertEqual(len(self.auth_calls), 2)  # the session's token was dropped
        self.adapter_status = 401
        self.call("/api/v1/matrix/files")
        self.adapter_status = 200
        self.call("/api/v1/matrix/files")
        self.assertEqual(len(self.auth_calls), 3)
        self.assertEqual(self.adapter_calls[-1][2]["VVS-Identity"], "tok-3")

    def test_status_goes_to_the_adapter_status(self):
        self.assertEqual(self.call("/api/v1/matrix/status", b"{}")[0], 200)
        self.assertEqual(self.adapter_calls[-1][1], "/api/v1/status")
        # The ViVeSecBox presence poll carries no user: no identity asked, none passed on.
        self.assertEqual(self.call("/api/v1/matrix/status", b"{}", user=None)[0], 200)
        self.assertNotIn("VVS-Identity", self.adapter_calls[-1][2])
        self.assertEqual(self.call("/api/v1/matrix/status", b"{}", front=False, user=None)[0], 403)

    def test_app_files_without_identity_but_only_through_the_front(self):
        self.assertEqual(self.call("/api/v1/matrix/app/manifest", user=None)[0], 404)  # no app dir
        app = tempfile.TemporaryDirectory()
        self.addCleanup(app.cleanup)
        bundle = b'{"format": 1, "script": "x"}'
        digest = hashlib.sha256(bundle).hexdigest()
        with open(os.path.join(app.name, "bundle.json"), "wb") as handle:
            handle.write(bundle)
        with open(os.path.join(app.name, "manifest.json"), "w", encoding="utf-8") as handle:
            json.dump({"format": 1, "bundle": {"sha256": digest, "bytes": len(bundle)}}, handle)
        self.gateway.settings.app_dir = app.name
        status, body, headers = self.call("/api/v1/matrix/app/manifest", user=None)
        self.assertEqual((status, body["bundle"]["sha256"], headers["Cache-Control"]), (200, digest, "no-store"))
        status, body, headers = self.call("/api/v1/matrix/app/bundle/" + digest, user=None)
        self.assertEqual((status, body["script"]), (200, "x"))
        self.assertIn("immutable", headers["Cache-Control"])
        self.assertEqual(self.call("/api/v1/matrix/app/bundle/" + "0" * 64, user=None)[0], 404)
        self.assertEqual(self.call("/api/v1/matrix/app/manifest", front=False, user=None)[0], 403)
        self.assertEqual(self.call("/api/v1/matrix/app/manifest", b"{}", user=None)[0], 405)
        self.assertEqual((self.auth_calls, self.adapter_calls), ([], []))

    def test_init_and_unknown_identities(self):
        with unittest.mock.patch("sys.stderr", new_callable=io.StringIO) as err:
            status, body, _ = self.call("/api/v1/matrix/init", user="unknown")
            self.assertEqual((status, body["accepted"]), (400, False))
            self.assertEqual(self.call("/api/v1/matrix/query", b"{}", user="unknown")[0], 403)
        # The administrator finds the id to map in the journal, once per refusal.
        self.assertEqual(err.getvalue().count("identity not accepted: VVS-User=unknown"), 1)
        self.assertEqual(len(self.auth_calls), 1)  # the refusal is cached
        self.assertEqual(self.call("/api/v1/matrix/init", user=None)[0], 400)
        self.assertEqual(self.call("/api/v1/matrix/query", b"{}", user=None)[0], 401)
        self.assertEqual(self.call("/api/v1/matrix/init")[0], 200)
        self.assertEqual(self.adapter_calls[-1][1], "/api/v1/ui/init")

    def test_auth_service_down(self):
        self.auth.shutdown()
        self.auth.server_close()
        self.assertEqual(self.call("/api/v1/matrix/query", b"{}")[0], 503)
        self.assertEqual(self.adapter_calls, [])

    def test_settings_fail_closed(self):
        self.assertRaises(gateway.ConfigError, gateway.Settings, dict(self.env, MATRIX_TUNNEL_HOST="0.0.0.0"))
        self.assertRaises(gateway.ConfigError, gateway.Settings,
                          dict(self.env, MATRIX_ADAPTER_URL="http://10.0.0.1:8088"))
        self.assertRaises(gateway.ConfigError, gateway.Settings, dict(self.env, AUTH_TUNNEL_KEY_FILE=""))
        self.assertRaises(gateway.ConfigError, gateway.Settings,
                          dict(self.env, MATRIX_TUNNEL_FORWARD_KEY_FILE="/nonexistent/key"))


if __name__ == "__main__":
    unittest.main()
