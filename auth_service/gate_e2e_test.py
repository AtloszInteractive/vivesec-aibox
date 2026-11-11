"""End-to-end test of the standalone login gate (E27 S4): the real
authentication service, the built web UI server (.output/server/index.mjs) and
a stub adapter that verifies the identity token with the adapter's own
verifier and reports the headers it received.

Needs Node.js and `npm run build`, so it only runs on request:

    $env:AIBOX_GATE_E2E = "1"
    Push-Location auth_service; python -m unittest gate_e2e_test; Pop-Location
"""
import http.client
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import totp

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SERVER_ENTRY = os.path.join(ROOT, ".output", "server", "index.mjs")
sys.path.append(os.path.join(ROOT, "adapter"))
import identity  # noqa: E402  the adapter's verifier: the real contract

ENABLED = os.environ.get("AIBOX_GATE_E2E") == "1"
FINANCE = "/storage/drives/finance/"
HR = "/storage/drives/hr/"
TEMP_PW = "Ideiglenes-jelszo-2026"
NEW_PW = "Uj-Biztos-Jelszo-2026"
BOB_PW = "Bob-sajat-jelszava-2026"
CLIENT_IP = "198.51.100.7"


def free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def wait_for(port, timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return
        except OSError:
            time.sleep(0.2)
    raise RuntimeError("nothing listens on %d" % port)


class StubAdapter(BaseHTTPRequestHandler):
    """Answers every call with what it saw; the identity is verified like the
    adapter does (ADAPTER_IDENTITY_MODE=require)."""

    def log_message(self, *args):
        pass

    def _answer(self):
        if "chunked" in (self.headers.get("Transfer-Encoding") or "").lower():
            body = b""
            while True:
                size = int(self.rfile.readline().split(b";")[0].strip(), 16)
                if size == 0:
                    self.rfile.readline()
                    break
                body += self.rfile.read(size)
                self.rfile.read(2)
        else:
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else b""
        seen = {name.lower(): value for name, value in self.headers.items()}
        try:
            caller = self.server.verifier.resolve(self.headers)
            verified = {"user": caller.user, "drives": list(caller.drives or [])}
        except identity.IdentityError as e:
            verified = {"error": e.reason}
        self.server.calls.append({"path": self.path, "method": self.command, "headers": seen,
                                  "verified": verified, "body": body.decode("utf-8", "replace"),
                                  "raw": body})
        data = json.dumps({"ok": True, "path": self.path}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Set-Cookie", "adapter=leak; Path=/")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    do_GET = do_POST = _answer


class Browser:
    """A minimal browser: keeps cookies, never follows redirects."""

    def __init__(self, port, host="aibox.test"):
        self.port = port
        self.host = host
        self.cookies = {}
        self.last_set_cookies = []

    def request(self, method, path, body=None, headers=None, csrf=False, origin=True):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=60)
        sent = {"Host": self.host, "X-Forwarded-For": "203.0.113.9, " + CLIENT_IP}
        if self.cookies:
            sent["Cookie"] = "; ".join("%s=%s" % item for item in self.cookies.items())
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            sent["Content-Type"] = "application/json"
        if method not in ("GET", "HEAD") and origin:
            sent["Origin"] = "https://" + self.host
            sent["Sec-Fetch-Site"] = "same-origin"
        if csrf:
            sent["X-AIBox-CSRF"] = self.cookies.get("__Host-aibox_csrf", "")
        sent.update(headers or {})
        conn.request(method, path, body=data, headers=sent)
        response = conn.getresponse()
        raw = response.read()
        self.last_set_cookies = response.headers.get_all("Set-Cookie") or []
        for header in self.last_set_cookies:
            name, _, rest = header.partition("=")
            value = rest.split(";", 1)[0]
            if "Max-Age=0" in header:
                self.cookies.pop(name, None)
            else:
                self.cookies[name] = value
        conn.close()
        try:
            payload = json.loads(raw.decode("utf-8")) if raw else None
        except ValueError:
            payload = raw.decode("utf-8", "replace")
        return response.status, response.headers, payload

    def cookie_flags(self, name):
        for header in self.last_set_cookies:
            if header.startswith(name + "="):
                return [part.strip() for part in header.split(";")[1:]]
        return None


@unittest.skipUnless(ENABLED, "set AIBOX_GATE_E2E=1 (needs node and npm run build)")
class LoginGateEndToEndTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        node = shutil.which("node")
        if not node or not os.path.isfile(SERVER_ENTRY):
            raise unittest.SkipTest("node or .output/server/index.mjs missing (npm run build)")
        cls.temp = tempfile.TemporaryDirectory()
        data = os.path.join(cls.temp.name, "auth")
        os.makedirs(data)
        cls.auth_port, cls.ui_port = free_port(), free_port()
        env = dict(os.environ, AUTH_DATA_DIR=data, AUTH_PORT=str(cls.auth_port),
                   PYTHONIOENCODING="utf-8")
        manage = [sys.executable, os.path.join(HERE, "manage.py")]
        subprocess.run(manage + ["init"], env=env, check=True, capture_output=True)
        subprocess.run(manage + ["create-user", "anna", "--display-name", "Anna Teszt",
                                 "--drive", FINANCE, "--drive", HR, "--temporary",
                                 "--password-stdin"],
                       env=env, check=True, capture_output=True, input=TEMP_PW + "\n", text=True)
        subprocess.run(manage + ["create-user", "bob", "--password-stdin"],
                       env=env, check=True, capture_output=True, input=BOB_PW + "\n", text=True)
        cls.manage, cls.env = manage, env
        cls.auth_log = open(os.path.join(cls.temp.name, "auth.log"), "wb")
        cls.auth = subprocess.Popen([sys.executable, os.path.join(HERE, "service.py")], env=env,
                                    stdout=cls.auth_log, stderr=subprocess.STDOUT)
        cls.adapter = ThreadingHTTPServer(("127.0.0.1", 0), StubAdapter)
        cls.adapter.calls = []
        cls.adapter.verifier = identity.Verifier(
            mode=identity.MODE_REQUIRE, algorithms=("HS256",),
            hs256_secret_file=os.path.join(data, "identity-hs256.key"),
            issuer="vivesec-aibox-auth", audience="vivesec-aibox-adapter")
        threading.Thread(target=cls.adapter.serve_forever, daemon=True).start()
        ui_env = {key: value for key, value in os.environ.items()
                  if not key.startswith(("ADAPTER_", "AUTH_"))}
        ui_env.update(PORT=str(cls.ui_port), HOST="127.0.0.1", NITRO_HOST="127.0.0.1",
                      ADAPTER_INTEGRATION="standalone",
                      ADAPTER_URL="http://127.0.0.1:%d" % cls.adapter.server_address[1],
                      AUTH_URL="http://127.0.0.1:%d" % cls.auth_port,
                      AUTH_SERVICE_KEY_FILE=os.path.join(data, "service.key"))
        cls.ui_log = open(os.path.join(cls.temp.name, "ui.log"), "wb")
        cls.ui = subprocess.Popen([node, SERVER_ENTRY], env=ui_env, cwd=ROOT,
                                  stdout=cls.ui_log, stderr=subprocess.STDOUT)
        try:
            wait_for(cls.auth_port)
            wait_for(cls.ui_port)
        except RuntimeError:
            cls.tearDownClass()
            raise

    @classmethod
    def tearDownClass(cls):
        for process in (cls.ui, cls.auth):
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
        cls.adapter.shutdown()
        cls.adapter.server_close()
        cls.ui_log.close()
        cls.auth_log.close()
        cls.temp.cleanup()

    def setUp(self):
        self.adapter.calls.clear()

    def browser(self):
        return Browser(self.ui_port)

    def sign_in(self, browser, username, password):
        """Password + code; enrols on the first login. Returns the login answer."""
        status, _, session = browser.request("GET", "/auth/session")
        self.assertEqual((status, session["authenticated"]), (200, False))
        status, _, first = browser.request("POST", "/auth/login",
                                           {"username": username, "password": password}, csrf=True)
        self.assertEqual(status, 200, first)
        secret = first.get("secret") or self.secrets[username]
        self.secrets[username] = secret
        code = totp.code_at(secret, time.time() + 30 * self.steps_used.get(username, 0))
        self.steps_used[username] = self.steps_used.get(username, 0) + 1
        status, _, second = browser.request("POST", "/auth/login/totp", {"code": code}, csrf=True)
        self.assertEqual(status, 200, second)
        return first, second

    secrets = {}
    steps_used = {}

    # -------------------------------------------------------------------------
    def test_everything_needs_a_session(self):
        browser = self.browser()
        status, headers, _ = browser.request("GET", "/")
        self.assertEqual((status, headers["Location"]), (302, "/login"))
        status, _, page = browser.request("GET", "/login")
        self.assertEqual(status, 200)
        self.assertIn("<html", page)
        for path in ("/api/v1/ui/init", "/api/v1/status", "/api/v1/ui/demo-config"):
            status, headers, body = browser.request("GET", path)
            self.assertEqual((status, body["error"]), (401, "login_required"), path)
            self.assertEqual(headers["X-AIBox-Auth"], "login")
        status, _, _ = browser.request("GET", "/password")
        self.assertEqual(status, 302)
        status, _, body = browser.request("POST", "/_serverFn/anything", {})
        self.assertEqual(status, 401)
        self.assertEqual(self.adapter.calls, [])
        # Public assets stay reachable for the login page.
        status, _, _ = browser.request("GET", "/manifest.webmanifest")
        self.assertEqual(status, 200)
        # The router decodes and resolves paths after the gate: a public prefix
        # must not smuggle an app path past the session check.
        for path in ("/assets/.%01./_serverFn/abc", "/assets/.%09./", "/icons/.%1f./password",
                     "/assets/%2e%2e/password", "/assets/x%2f..%2f..%2fpassword",
                     "/assets/..%5c..%5cpassword", "/assets/.%0a./_serverFn/abc"):
            status, headers, _ = browser.request("GET", path)
            # Refused, or resolved to an app path that needs the session.
            self.assertTrue(status in (400, 401) or (status == 302 and headers["Location"] == "/login"),
                            (path, status))
        for path in ("/assets/no-such-file.js", "/icons/missing.png"):
            status, _, _ = browser.request("GET", path)
            self.assertNotEqual(status, 302, path)
        status, _, _ = browser.request("GET", "/%61pi/v1/ui/init")
        self.assertIn(status, (302, 401))
        self.assertEqual(self.adapter.calls, [])

    def test_csrf_and_origin_are_enforced(self):
        browser = self.browser()
        status, _, body = browser.request("POST", "/auth/login", {"username": "x", "password": "y"})
        self.assertEqual((status, body["error"]), (403, "csrf"))
        browser.request("GET", "/auth/session")
        flags = browser.cookie_flags("__Host-aibox_csrf")
        self.assertIn("Secure", flags)
        self.assertIn("SameSite=Strict", flags)
        self.assertNotIn("HttpOnly", flags)
        status, _, body = browser.request("POST", "/auth/login", {"username": "x", "password": "y"},
                                          csrf=True, headers={"Origin": "https://evil.example"})
        self.assertEqual((status, body["error"]), (403, "csrf"))
        status, _, body = browser.request("POST", "/auth/login", {"username": "x", "password": "y"},
                                          csrf=True, headers={"Sec-Fetch-Site": "cross-site"})
        self.assertEqual((status, body["error"]), (403, "csrf"))
        status, _, body = browser.request("POST", "/auth/login", {"username": "x", "password": "y"},
                                          headers={"X-AIBox-CSRF": "a" * 43})
        self.assertEqual((status, body["error"]), (403, "csrf"))
        # Same length in characters, longer in bytes: refused, not a server error.
        token = browser.cookies["__Host-aibox_csrf"]
        status, _, body = browser.request("POST", "/auth/login", {"username": "x", "password": "y"},
                                          headers={"X-AIBox-CSRF": "\u00e9" * len(token)})
        self.assertEqual((status, body["error"]), (403, "csrf"))
        status, _, body = browser.request("POST", "/auth/login",
                                          {"username": "x", "password": "y" * 20000}, csrf=True)
        self.assertEqual((status, body["error"]), (400, "bad_request"))
        status, _, body = browser.request("POST", "/auth/login",
                                          {"username": "nobody", "password": "wrong-password"},
                                          csrf=True)
        self.assertEqual((status, body["error"]), (401, "invalid_credentials"))

    def test_first_login_enrols_changes_password_and_relays_the_token(self):
        browser = self.browser()
        browser.request("GET", "/auth/session")
        status, _, body = browser.request("POST", "/auth/login",
                                          {"username": "anna", "password": "wrong-password"},
                                          csrf=True)
        self.assertEqual((status, body["error"]), (401, "invalid_credentials"))
        status, _, first = browser.request("POST", "/auth/login",
                                           {"username": " Anna ", "password": TEMP_PW}, csrf=True)
        self.assertEqual((status, first["step"]), (200, "enroll"))
        self.assertNotIn("challenge", first)
        self.assertNotIn("otpauth_uri", first)
        self.assertEqual((first["account"], first["issuer"]), ("anna", "ViVeSec AI Box"))
        rows = first["qr"]
        self.assertGreaterEqual(len(rows), 21)
        self.assertTrue(all(len(row) == len(rows) and set(row) <= {"0", "1"} for row in rows))
        flags = browser.cookie_flags("__Host-aibox_login")
        self.assertTrue({"HttpOnly", "Secure", "SameSite=Strict", "Path=/"} <= set(flags), flags)
        secret = first["secret"]
        self.secrets["anna"] = secret

        status, _, body = browser.request("POST", "/auth/login/totp", {"code": "000000"}, csrf=True)
        wrong_is_right = totp.match(secret, "000000", time.time()) is not None
        if not wrong_is_right:
            self.assertEqual((status, body["error"]), (401, "invalid_code"))
            self.assertIn("__Host-aibox_login", browser.cookies)
        csrf_before = browser.cookies["__Host-aibox_csrf"]
        status, _, second = browser.request("POST", "/auth/login/totp",
                                            {"code": totp.code_at(secret, time.time())}, csrf=True)
        self.steps_used["anna"] = 1
        self.assertEqual(status, 200, second)
        self.assertTrue(second["password_change_required"])
        self.assertNotIn("session", second)
        self.assertNotIn("__Host-aibox_login", browser.cookies)
        self.assertNotEqual(browser.cookies["__Host-aibox_csrf"], csrf_before)
        session_flags = browser.cookie_flags("__Host-aibox_session")
        self.assertTrue({"HttpOnly", "Secure", "SameSite=Strict", "Path=/"} <= set(session_flags))

        # Temporary password: only the password page works.
        status, headers, _ = browser.request("GET", "/")
        self.assertEqual((status, headers["Location"]), (302, "/password"))
        status, headers, body = browser.request("GET", "/api/v1/ui/init")
        self.assertEqual((status, body["error"], headers["X-AIBox-Auth"]),
                         (403, "password_change_required", "password"))
        status, _, _ = browser.request("GET", "/password")
        self.assertEqual(status, 200)
        status, _, me = browser.request("GET", "/auth/session")
        self.assertTrue(me["password_change_required"])
        self.assertEqual(me["password_min_length"], 12)
        self.assertEqual(me["user"]["drives"], [FINANCE, HR])
        status, _, body = browser.request("POST", "/auth/password",
                                          {"current_password": TEMP_PW, "new_password": "short"},
                                          csrf=True)
        self.assertEqual((status, body["error"]), (400, "password_too_short"))
        status, _, body = browser.request("POST", "/auth/password",
                                          {"current_password": TEMP_PW, "new_password": NEW_PW},
                                          csrf=True)
        self.assertEqual(status, 200, body)

        status, _, page = browser.request("GET", "/")
        self.assertEqual(status, 200)
        status, _, body = browser.request("GET", "/login")
        self.assertEqual(status, 302)

        # The adapter gets the token identity, never the browser's.
        status, _, _ = browser.request("GET", "/api/v1/ui/init", headers={
            "VVS-User": "admin", "VVS-Other-Drives": "L3N0b3JhZ2UvZHJpdmVzL2JvYXJkLw",
            "VVS-Identity": "forged", "Authorization": "Bearer forged"})
        self.assertEqual(status, 200)
        call = self.adapter.calls[-1]
        self.assertEqual(call["verified"], {"user": "anna", "drives": [FINANCE, HR]})
        headers = call["headers"]
        self.assertEqual(headers["vvs-user"], "anna")
        self.assertEqual(identity.b64url_decode(headers["vvs-drive"]).decode("utf-8"), FINANCE)
        for leaked in ("vvs-other-drives", "cookie", "authorization", "x-aibox-csrf"):
            self.assertNotIn(leaked, headers)
        self.assertNotEqual(headers["vvs-identity"], "forged")

        for hint, expected in ((HR, HR), ("/storage/drives/hr", HR),
                               ("/storage/drives/board/", FINANCE)):
            browser.request("GET", "/api/v1/ui/scope", headers={"X-Demo-Drive": hint})
            drive = identity.b64url_decode(self.adapter.calls[-1]["headers"]["vvs-drive"])
            self.assertEqual(drive.decode("utf-8"), expected, hint)

        status, _, config = browser.request("GET", "/api/v1/ui/demo-config")
        self.assertEqual(config, {"drive": FINANCE, "driveRoot": "/storage/drives",
                                  "picker": True, "drives": [FINANCE, HR]})

        status, _, body = browser.request("POST", "/api/v1/ui/ask", {"query": "x"})
        self.assertEqual((status, body["error"]), (403, "csrf"))
        status, _, body = browser.request("POST", "/api/v1/ui/ask", {"query": "x"}, csrf=True,
                                          headers={"Origin": "https://evil.example"})
        self.assertEqual(status, 403)
        calls = len(self.adapter.calls)
        status, _, body = browser.request("POST", "/api/v1/ui/ask", {"query": "x"}, csrf=True)
        self.assertEqual(status, 200)
        self.assertEqual(len(self.adapter.calls), calls + 1)
        self.assertEqual(self.adapter.calls[-1]["body"], '{"query": "x"}')
        self.assertNotIn("adapter", browser.cookies)

        for path in ("/api/v1/index/get/children", "/api/v1/init/prepare",
                     "/api/v1/storage/unlock", "/api/v2/anything"):
            status, _, _ = browser.request("POST", path, {}, csrf=True)
            self.assertEqual(status, 404, path)
        self.assertEqual(len(self.adapter.calls), calls + 1)

        # Logout ends the session for good, also for a copied cookie.
        stolen = dict(browser.cookies)
        status, _, _ = browser.request("POST", "/auth/logout", {}, csrf=True)
        self.assertEqual(status, 200)
        self.assertNotIn("__Host-aibox_session", browser.cookies)
        replay = self.browser()
        replay.cookies = stolen
        status, _, body = replay.request("GET", "/api/v1/ui/init")
        self.assertEqual((status, body["error"]), (401, "login_required"))
        status, headers, _ = replay.request("GET", "/")
        self.assertEqual((status, headers["Location"]), (302, "/login"))

        # The second login asks for the code, and the client address reached the log.
        _, _ = self.sign_in(self.browser(), "anna", NEW_PW)
        events = subprocess.run(self.manage + ["events", "--user", "anna", "--limit", "20"],
                                env=self.env, check=True, capture_output=True, text=True).stdout
        self.assertIn(CLIENT_IP, events)
        self.assertNotIn("203.0.113.9", events)

    def test_user_without_rooms_sends_no_drive(self):
        browser = self.browser()
        first, second = self.sign_in(browser, "bob", BOB_PW)
        self.assertEqual(first["step"], "enroll")
        self.assertFalse(second["password_change_required"])
        status, _, config = browser.request("GET", "/api/v1/ui/demo-config")
        self.assertEqual(config, {"drive": "", "driveRoot": "/storage/drives",
                                  "picker": False, "drives": []})
        browser.request("GET", "/api/v1/ui/init", headers={"X-Demo-Drive": FINANCE,
                                                           "VVS-Drive": "L3N0b3JhZ2UvZHJpdmVzL2ZpbmFuY2Uv"})
        headers = self.adapter.calls[-1]["headers"]
        self.assertNotIn("vvs-drive", headers)
        self.assertEqual(self.adapter.calls[-1]["verified"], {"user": "bob", "drives": []})
        # The same session (one login per user: the TOTP window is short):
        # an upload is refused without CSRF, and streams through with it.
        self.adapter.calls.clear()
        status, _, body = browser.request("POST", "/api/v1/ui/store/upload", {"x": 1})
        self.assertEqual((status, body["error"]), (403, "csrf"))
        self.assertEqual(self.adapter.calls, [])
        self.check_streamed_upload(browser)

    def check_streamed_upload(self, browser):
        payload = bytes(range(256)) * 4096
        conn = http.client.HTTPConnection("127.0.0.1", self.ui_port, timeout=60)
        conn.request("POST", "/api/v1/ui/store/upload", body=payload, headers={
            "Host": browser.host, "Origin": "https://" + browser.host,
            "Sec-Fetch-Site": "same-origin", "Content-Type": "application/octet-stream",
            "Cookie": "; ".join("%s=%s" % item for item in browser.cookies.items()),
            "X-AIBox-CSRF": browser.cookies.get("__Host-aibox_csrf", ""),
            "X-Store-Path": "%2Fstorage%2Fdrives%2Fx%2Fa%20b.pdf", "VVS-User": "mallory"})
        response = conn.getresponse()
        response.read()
        conn.close()
        self.assertEqual(response.status, 200)
        call = self.adapter.calls[-1]
        self.assertEqual(call["path"], "/api/v1/ui/store/upload")
        self.assertEqual(call["verified"], {"user": "bob", "drives": []})
        self.assertEqual(call["headers"]["x-store-path"], "%2Fstorage%2Fdrives%2Fx%2Fa%20b.pdf")
        self.assertEqual(call["headers"]["vvs-user"], "bob")
        self.assertNotIn("cookie", call["headers"])
        # Streamed, not buffered: the relay sends the body chunked.
        self.assertEqual(call["headers"].get("transfer-encoding"), "chunked")
        self.assertEqual(call["raw"], payload)


if __name__ == "__main__":
    unittest.main()
