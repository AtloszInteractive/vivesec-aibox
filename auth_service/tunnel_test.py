import io
import json
import os
import sys
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
import tunnel

ADAPTER_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "adapter")
sys.path.append(os.path.abspath(ADAPTER_DIR))
import identity  # noqa: E402  the adapter's verifier: the real contract

KEY = b"0123456789abcdef0123456789abcdef-test"
SERVICE_KEY = b"service-key-0123456789abcdef0123456789"
TUNNEL_KEY = b"tunnel-key-0123456789abcdef0123456789a"
VVS_USER = "28744CZP27222"


class Clock:
    def __init__(self, now=1800000000.0):
        self.now = now

    def __call__(self):
        return self.now


class TunnelTokenTest(unittest.TestCase):
    def setUp(self):
        saved = passwords.N
        passwords.N = 2 ** 10
        self.addCleanup(setattr, passwords, "N", saved)
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.dir = temp.name
        self.clock = Clock()
        self.settings = config.Settings(data_dir=self.dir)
        store = store_module.Store(self.settings.db_path)
        self.addCleanup(store.close)
        self.auth = auth.Authenticator(store, self.settings, KEY, clock=self.clock)
        self.auth.create_user("cli", "anna", "Anna-jelszava-2026", roles=["analyst"],
                              drives=["/storage/drives/kozos/"], temporary=True)
        self.auth.create_user("cli", "bela", "Bela-jelszava-2026", temporary=False)

    def map(self, mapping):
        tunnel.write_map(self.settings.tunnel_users_file, mapping)

    def verifier(self):
        key_file = os.path.join(self.dir, "identity.key")
        with open(key_file, "wb") as handle:
            handle.write(KEY + b"\n")
        return identity.Verifier(mode=identity.MODE_REQUIRE, algorithms=("HS256",),
                                 hs256_secret_file=key_file, issuer=config.DEFAULT_ISSUER,
                                 audience=config.DEFAULT_AUDIENCE, clock=self.clock)

    def test_a_mapped_identity_gets_the_local_users_token(self):
        self.map({VVS_USER: "anna"})
        issued = self.auth.issue_tunnel_token(VVS_USER, "1tz_n9hzfP0PoQAAAZ9DD-zw")
        # No TOTP enrolment and a temporary password do not matter: the
        # ViVeSecBox sign-in stands in for both.
        who = self.verifier().resolve({"VVS-Identity": issued["token"], "VVS-User": "anna",
                                       "VVS-Drive": "L3N0b3JhZ2UvZHJpdmVzL2tvem9zLw=="},
                                      "/storage/drives/kozos/")
        self.assertEqual((who.user, list(who.roles), list(who.drives)),
                         ("anna", ["analyst"], ["/storage/drives/kozos/"]))
        claims = self.verifier().verify_token(issued["token"])
        self.assertEqual(claims["amr"], ["vivesecbox-tunnel"])
        self.assertEqual(claims["sid"], tunnel.session_id(VVS_USER, "1tz_n9hzfP0PoQAAAZ9DD-zw"))
        self.assertNotIn(VVS_USER, claims["sid"])
        self.assertEqual(claims["exp"] - claims["iat"], self.settings.token_ttl_seconds)

    def test_unmapped_suspended_and_broken_maps_get_nothing(self):
        self.assertRaises(auth.AuthError, self.auth.issue_tunnel_token, VVS_USER)
        self.map({VVS_USER: "anna", "other": "nobody"})
        with self.assertRaises(auth.AuthError) as refused:
            self.auth.issue_tunnel_token("other")
        self.assertEqual((refused.exception.code, refused.exception.status), ("tunnel_user_unknown", 403))
        self.auth.update_user("cli", "anna", status="suspended")
        self.assertRaises(auth.AuthError, self.auth.issue_tunnel_token, VVS_USER)
        self.auth.update_user("cli", "anna", status="active")
        self.assertTrue(self.auth.issue_tunnel_token(VVS_USER)["token"])
        with open(self.settings.tunnel_users_file, "w", encoding="utf-8") as handle:
            handle.write('{"%s": "anna", "bad user": "bela"}' % VVS_USER)
        self.assertRaises(auth.AuthError, self.auth.issue_tunnel_token, VVS_USER)
        self.assertIn("ignored", self.auth.tunnel_map.problem)
        events = [event["event"] for event in self.auth.list_events(20)]
        self.assertIn("tunnel_refused", events)

    def test_a_new_vivesecbox_user_gets_an_analyst_without_rooms_when_switched_on(self):
        self.assertRaises(auth.AuthError, self.auth.issue_tunnel_token, VVS_USER)  # off by default
        self.auth.settings = config.Settings(data_dir=self.dir, tunnel_autocreate="analyst",
                                             tunnel_autocreate_max=1)
        issued = self.auth.issue_tunnel_token(VVS_USER)
        claims = self.verifier().verify_token(issued["token"])
        name = tunnel.auto_username(VVS_USER)
        self.assertEqual((claims["sub"], claims["roles"], claims["drives"]), (name, ["analyst"], []))
        self.assertNotIn(VVS_USER, name)
        self.assertEqual(self.auth.tunnel_map.username_for(VVS_USER), name)
        self.assertEqual(self.auth.issue_tunnel_token(VVS_USER)["user"]["username"], name)
        # No password: it cannot sign in on the web page.
        self.assertRaises(auth.AuthError, self.auth.login, name, "")
        # The limit, and an administrator's decision, hold.
        self.assertRaises(auth.AuthError, self.auth.issue_tunnel_token, "another-user")
        self.auth.delete_user("cli", name)
        self.assertRaises(auth.AuthError, self.auth.issue_tunnel_token, VVS_USER)
        events = [event["event"] for event in self.auth.list_events(20)]
        self.assertIn("tunnel_user_created", events)

    def test_an_unknown_autocreate_mode_is_off(self):
        warnings = []
        settings = config.Settings.from_env({"AUTH_DATA_DIR": self.dir, "AUTH_TUNNEL_AUTOCREATE": "admin"},
                                            warn=warnings.append)
        self.assertIsNone(settings.tunnel_autocreate_roles)
        self.assertEqual(len(warnings), 1)

    def test_malformed_input_is_refused(self):
        self.map({VVS_USER: "anna"})
        for bad in (None, "", "a b", "x" * 200, ["x"]):
            with self.assertRaises(auth.AuthError) as refused:
                self.auth.issue_tunnel_token(bad)
            self.assertEqual(refused.exception.code, "invalid_vvs_user")
        self.assertRaises(auth.AuthError, self.auth.issue_tunnel_token, VVS_USER, "s" * 300)


class TunnelEndpointTest(unittest.TestCase):
    def setUp(self):
        saved = passwords.N
        passwords.N = 2 ** 10
        self.addCleanup(setattr, passwords, "N", saved)
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        settings = config.Settings(data_dir=temp.name)
        store = store_module.Store(settings.db_path)
        self.addCleanup(store.close)
        self.auth = auth.Authenticator(store, settings, KEY)
        self.auth.create_user("cli", "anna", "Anna-jelszava-2026", temporary=False)
        tunnel.write_map(settings.tunnel_users_file, {VVS_USER: "anna"})

    def serve(self, tunnel_key):
        server = service.AuthServer(("127.0.0.1", 0), self.auth, SERVICE_KEY, tunnel_key)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return "http://127.0.0.1:%d" % server.server_address[1]

    def call(self, base, keys, body):
        request = urllib.request.Request(base + service.TUNNEL_PATH, method="POST",
                                         data=json.dumps(body).encode("utf-8"))
        for header, value in keys.items():
            request.add_header(header, value.decode("ascii"))
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_needs_both_keys_and_is_off_without_a_tunnel_key(self):
        both = {service.KEY_HEADER: SERVICE_KEY, service.TUNNEL_KEY_HEADER: TUNNEL_KEY}
        base = self.serve(None)
        self.assertEqual(self.call(base, both, {"vvs_user": VVS_USER})[0], 404)
        base = self.serve(TUNNEL_KEY)
        self.assertEqual(self.call(base, {service.KEY_HEADER: SERVICE_KEY}, {"vvs_user": VVS_USER}),
                         (401, {"ok": False, "error": "tunnel_key"}))
        self.assertEqual(self.call(base, {service.TUNNEL_KEY_HEADER: TUNNEL_KEY}, {"vvs_user": VVS_USER}),
                         (401, {"ok": False, "error": "service_key"}))
        status, body = self.call(base, both, {"vvs_user": VVS_USER, "vvs_session": "s1"})
        self.assertEqual((status, body["user"]["username"]), (200, "anna"))
        self.assertEqual(self.call(base, both, {"vvs_user": "unknown"}),
                         (403, {"ok": False, "error": "tunnel_user_unknown"}))

    def test_an_unusable_key_file_turns_the_tunnel_off(self):
        warnings = []
        settings = config.Settings(data_dir=self.auth.settings.data_dir,
                                   tunnel_key_file=os.path.join(self.auth.settings.data_dir, "missing.key"))
        self.assertIsNone(service.tunnel_key(settings, warnings.append))
        self.assertEqual(len(warnings), 1)
        self.assertIsNone(service.tunnel_key(config.Settings(data_dir="/x"), warnings.append))


class TunnelMapCommandTest(unittest.TestCase):
    def test_set_show_remove(self):
        saved = passwords.N
        passwords.N = 2 ** 10
        self.addCleanup(setattr, passwords, "N", saved)
        with tempfile.TemporaryDirectory() as data:
            env = {"AUTH_DATA_DIR": data, "AUTH_TUNNEL_KEY_FILE": os.path.join(data, "tunnel.key")}
            out = io.StringIO()
            manage.run(["init"], env, out)
            self.assertIn("tunnel key created", out.getvalue())
            manage.run(["create-user", "anna", "--password-stdin"], env, io.StringIO(),
                       io.StringIO("Anna-jelszava-2026\n"))
            self.assertRaises(auth.AuthError, manage.run, ["tunnel-map", "set", VVS_USER, "nobody"],
                              env, io.StringIO())
            self.assertEqual(manage.run(["tunnel-map", "set", VVS_USER, "Anna"], env, io.StringIO()), 0)
            out = io.StringIO()
            manage.run(["tunnel-map", "show"], env, out)
            self.assertEqual(json.loads(out.getvalue())["users"], {VVS_USER: "anna"})
            if os.name == "posix":
                self.assertEqual(os.stat(os.path.join(data, "tunnel-users.json")).st_mode & 0o777, 0o600)
            manage.run(["tunnel-map", "remove", VVS_USER], env, io.StringIO())
            self.assertRaises(SystemExit, manage.run, ["tunnel-map", "remove", VVS_USER], env, io.StringIO())
            # promote: that ViVeSecBox user administers the box through the tunnel.
            manage.run(["tunnel-map", "promote", VVS_USER], env, io.StringIO())
            out = io.StringIO()
            manage.run(["list-users"], env, out)
            users = {u["username"]: u for u in json.loads(out.getvalue())}
            self.assertEqual(users[tunnel.auto_username(VVS_USER)]["roles"], ["admin", "analyst"])


if __name__ == "__main__":
    unittest.main()
