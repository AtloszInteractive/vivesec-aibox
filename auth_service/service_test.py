import io
import json
import os
import socket
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

import auth
import config
import manage
import passwords
import service
import store as store_module
import tokens
import totp

SERVICE_KEY = b"service-key-0123456789abcdef0123456789"
JWT_KEY = b"jwt-key-0123456789abcdef0123456789abcd"
PASSWORD = "Admin-jelszo-2026"


class ConfigTest(unittest.TestCase):
    def test_defaults(self):
        settings = config.Settings.from_env({})
        self.assertEqual((settings.host, settings.port, settings.token_ttl_seconds), ("127.0.0.1", 8097, 300))
        self.assertEqual(settings.db_path, os.path.join("/data/auth", "auth.db"))
        self.assertEqual(settings.jwt_secret_file, os.path.join("/data/auth", "identity-hs256.key"))
        self.assertEqual((settings.lockout_threshold, settings.password_min_length), (5, 12))

    def test_invalid_numbers_fall_back_to_the_default(self):
        warnings = []
        settings = config.Settings.from_env({"AUTH_TOKEN_TTL_SECONDS": "86400",
                                             "AUTH_LOCKOUT_THRESHOLD": "many",
                                             "AUTH_PASSWORD_MIN_LENGTH": "4",
                                             "AUTH_SESSION_IDLE_SECONDS": "7200",
                                             "AUTH_SESSION_MAX_SECONDS": "3600"}, warnings.append)
        self.assertEqual((settings.token_ttl_seconds, settings.lockout_threshold,
                          settings.password_min_length), (300, 5, 12))
        self.assertEqual((settings.session_idle_seconds, settings.session_max_seconds), (3600, 3600))
        self.assertEqual(len(warnings), 3)

    def test_only_loopback(self):
        for host in ("127.0.0.1", "localhost", "::1", "127.0.0.2"):
            self.assertEqual(config.Settings.from_env({"AUTH_HOST": host}).host, host)
        for host in ("0.0.0.0", "192.168.0.181", "::", "example.com"):
            self.assertRaises(config.ConfigError, config.Settings.from_env, {"AUTH_HOST": host})


class ServiceTest(unittest.TestCase):
    def setUp(self):
        saved = passwords.N
        passwords.N = 2 ** 10
        self.addCleanup(setattr, passwords, "N", saved)
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        settings = config.Settings(data_dir=temp.name)
        store = store_module.Store(settings.db_path)
        self.auth = auth.Authenticator(store, settings, JWT_KEY)
        self.auth.create_user("cli", "admin", PASSWORD, roles=["admin"], temporary=False)
        self.auth.create_user("cli", "anna", "Anna-jelszava-2026", temporary=False)
        self.server = service.AuthServer(("127.0.0.1", 0), self.auth, SERVICE_KEY)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(store.close)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base = "http://127.0.0.1:%d" % self.server.server_address[1]

    def call(self, path, body=None, key=SERVICE_KEY, raw=None):
        data = raw if raw is not None else json.dumps(body or {}).encode("utf-8")
        request = urllib.request.Request(self.base + path, data=data, method="POST")
        request.add_header("Content-Type", "application/json")
        if key is not None:
            request.add_header(service.KEY_HEADER, key.decode("ascii"))
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def sign_in(self, username, password):
        status, first = self.call("/auth/login", {"username": username, "password": password,
                                                  "client": "10.0.0.5"})
        self.assertEqual(status, 200, first)
        code = totp.code_at(first["totp_secret"], self.auth.clock())
        status, session = self.call("/auth/login/totp", {"challenge": first["challenge"], "code": code})
        self.assertEqual(status, 200, session)
        return session["session"]

    def test_health_needs_no_key(self):
        with urllib.request.urlopen(self.base + "/auth/health", timeout=10) as response:
            self.assertEqual(json.loads(response.read()), {"ok": True, "service": "auth", "directory": "off"})

    @unittest.skipUnless(socket.has_ipv6, "no IPv6 on this machine")
    def test_listens_on_ipv6_loopback(self):
        try:
            server = service.AuthServer(("::1", 0), self.auth, SERVICE_KEY)
        except OSError as e:
            self.skipTest("::1 not available: %s" % e)
        server.server_close()
        self.assertEqual(server.address_family, socket.AF_INET6)

    def test_service_key_is_required(self):
        self.assertEqual(self.call("/auth/login", {}, key=None), (401, {"ok": False, "error": "service_key"}))
        self.assertEqual(self.call("/auth/login", {}, key=b"wrong")[0], 401)

    def test_login_token_and_logout_over_http(self):
        session = self.sign_in("anna", "Anna-jelszava-2026")
        status, me = self.call("/auth/me", {"session": session})
        self.assertEqual((status, me["user"]["username"]), (200, "anna"))
        self.assertEqual(me["password_min_length"], 12)
        status, issued = self.call("/auth/token", {"session": session})
        self.assertEqual(status, 200)
        self.assertEqual(issued["token"].count("."), 2)
        self.assertEqual(self.call("/auth/logout", {"session": session}), (200, {"ok": True}))
        self.assertEqual(self.call("/auth/token", {"session": session}),
                         (401, {"ok": False, "error": "session_invalid"}))

    def test_errors_carry_codes_not_details(self):
        self.assertEqual(self.call("/auth/login", {"username": "anna", "password": "nope-nope-nope"}),
                         (401, {"ok": False, "error": "invalid_credentials"}))
        self.assertEqual(self.call("/auth/login", raw=b"not json"), (400, {"ok": False, "error": "bad_request"}))
        self.assertEqual(self.call("/auth/login", raw=b"[1, 2]")[0], 400)
        self.assertEqual(self.call("/auth/nope", {})[0], 404)
        self.assertEqual(self.call("/auth/login", raw=b"x" * (service.MAX_BODY_BYTES + 1))[0], 413)

    def test_admin_routes(self):
        admin = self.sign_in("admin", PASSWORD)
        user = self.sign_in("anna", "Anna-jelszava-2026")
        self.assertEqual(self.call("/auth/admin/users", {"session": user}),
                         (403, {"ok": False, "error": "forbidden"}))
        self.assertEqual(self.call("/auth/admin/users", {})[0], 401)
        status, listed = self.call("/auth/admin/users", {"session": admin})
        self.assertEqual(status, 200)
        self.assertEqual([u["username"] for u in listed["users"]], ["admin", "anna"])
        self.assertNotIn("password_hash", json.dumps(listed))
        self.assertNotIn("totp_secret\"", json.dumps(listed))
        status, created = self.call("/auth/admin/users/create", {
            "session": admin, "username": "bela", "password": "Bela-ideiglenes-1",
            "drives": ["/storage/drives/ugy-1"], "client": "10.0.0.7"})
        self.assertEqual((status, created["user"]["drives"]), (200, ["/storage/drives/ugy-1/"]))
        self.assertTrue(created["user"]["must_change_password"])
        self.assertEqual(self.call("/auth/admin/users/create", {
            "session": admin, "username": "cecil", "password": "Cecil-jelszava-1", "temporary": "no"}),
            (400, {"ok": False, "error": "invalid_temporary"}))
        status, reset = self.call("/auth/admin/users/reset-totp", {"session": admin, "username": "anna"})
        self.assertEqual((status, reset["user"]["totp_enrolled"]), (200, False))
        self.assertEqual(self.call("/auth/me", {"session": user})[0], 401)
        status, events = self.call("/auth/admin/events", {"session": admin, "username": "bela"})
        self.assertEqual([e["event"] for e in events["events"]], ["user_created"])
        self.assertEqual(events["events"][0]["actor"], "admin")
        self.assertEqual(events["events"][0]["client"], "10.0.0.7")

    def test_unexpected_errors_stay_internal(self):
        original = self.auth.me

        def broken(_session):
            raise RuntimeError("database detail that must not leak")
        self.auth.me = broken
        self.addCleanup(setattr, self.auth, "me", original)
        self.assertEqual(self.call("/auth/me", {"session": "x"}), (500, {"ok": False, "error": "internal"}))


class ManageTest(unittest.TestCase):
    def setUp(self):
        saved = passwords.N
        passwords.N = 2 ** 10
        self.addCleanup(setattr, passwords, "N", saved)
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.env = {"AUTH_DATA_DIR": os.path.join(temp.name, "auth")}

    def run_cli(self, *argv, stdin=""):
        out = io.StringIO()
        code = manage.run(list(argv), env=self.env, out=out, stdin=io.StringIO(stdin))
        return code, out.getvalue()

    def test_init_is_idempotent_and_keeps_the_keys(self):
        code, out = self.run_cli("init")
        self.assertEqual(code, 0)
        self.assertIn("token signing key created", out)
        settings = config.Settings.from_env(self.env)
        key = tokens.read_key(settings.jwt_secret_file, "key")
        self.assertEqual(len(key), 64)
        if os.name == "posix":
            self.assertEqual(os.stat(settings.jwt_secret_file).st_mode & 0o777, 0o600)
            self.assertEqual(os.stat(settings.data_dir).st_mode & 0o777, 0o700)
            self.assertEqual(os.stat(settings.db_path).st_mode & 0o777, 0o600)
        code, out = self.run_cli("init")
        self.assertIn("token signing key kept", out)
        self.assertEqual(tokens.read_key(settings.jwt_secret_file, "key"), key)

    def test_user_administration(self):
        self.run_cli("init")
        code, out = self.run_cli("create-user", "Admin", "--role", "admin", "--drive", "/storage/drives/a",
                                 "--password-stdin", stdin=PASSWORD + "\n")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["drives"], ["/storage/drives/a/"])
        self.assertFalse(json.loads(out)["must_change_password"])
        code, out = self.run_cli("update-user", "admin", "--no-drives")
        self.assertEqual(json.loads(out)["drives"], [])
        code, out = self.run_cli("reset-totp", "admin")
        self.assertFalse(json.loads(out)["totp_enrolled"])
        code, out = self.run_cli("list-users")
        self.assertEqual([u["username"] for u in json.loads(out)], ["admin"])
        code, out = self.run_cli("events", "--user", "admin")
        self.assertEqual({e["actor"] for e in json.loads(out)}, {"cli"})
        self.assertRaises(auth.AuthError, self.run_cli, "suspend", "admin")

    def test_commands_need_init(self):
        self.assertRaises(tokens.KeyFileError, self.run_cli, "list-users")


if __name__ == "__main__":
    unittest.main()
