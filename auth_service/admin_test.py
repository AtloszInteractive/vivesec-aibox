"""Administration backend of the standalone box (E27 S7): the clock, the
directory settings and group map, the event log paging and the session counts."""
import ctypes
import json
import os
import threading
import unittest
import urllib.error
import urllib.request

import auth
import clock
import directory
import service
import store as store_module
from auth_test import ADMIN_PW, KEY, AuthTestCase
from directory_test import (ADMINS, ANALYSTS, CASES, GROUP_MAP, HR, SERVICE_PW, World,
                            directory_settings, install)

SERVICE_KEY = b"service-key-0123456789abcdef0123456789"


class ClockTest(unittest.TestCase):
    def test_interpret(self):
        self.assertEqual(clock.interpret(0, 0, 25000, 1200),
                         {"state": "synchronized", "max_error_ms": 25.0, "est_error_ms": 1.2})
        self.assertEqual(clock.interpret(clock.TIME_ERROR, 0, 16000000, 16000000),
                         {"state": "unsynchronized"})
        self.assertEqual(clock.interpret(0, clock.STA_UNSYNC, 100, 100)["state"], "unsynchronized")
        self.assertEqual(clock.interpret(-1, 0, 0, 0)["state"], "unknown")

    def test_kernel_sync_reads_without_setting_anything(self):
        seen = []

        def fake(pointer):
            timex = pointer._obj
            seen.append(timex.modes)
            timex.maxerror, timex.esterror, timex.status = 4000, 500, 0
            return 0

        self.assertEqual(clock.kernel_sync(fake),
                         {"state": "synchronized", "max_error_ms": 4.0, "est_error_ms": 0.5})
        self.assertEqual(seen, [0])

    def test_failures_are_unknown(self):
        def broken(pointer):
            raise OSError("no")

        self.assertEqual(clock.kernel_sync(broken)["state"], "unknown")

    def test_the_real_call_never_raises(self):
        self.assertIn(clock.kernel_sync()["state"], ("synchronized", "unsynchronized", "unknown"))

    def test_struct_layout_matches_the_c_abi(self):
        # 64-bit Linux: 208 bytes; 32-bit: 128. Anything else would mean a
        # misaligned read of the status field.
        self.assertIn(ctypes.sizeof(clock._Timex), (128, 208))


class AdminTest(AuthTestCase):
    def test_system_reports_time_clock_and_directory(self):
        self.auth.kernel_clock = lambda: {"state": "synchronized"}
        report = self.auth.system()
        self.assertEqual(report["now"], self.clock())
        self.assertEqual(report["clock"], {"state": "synchronized"})
        self.assertEqual((report["totp_step_seconds"], report["totp_tolerance_steps"]), (30, 1))
        self.assertEqual(report["directory"], {"state": "off", "problem": None})

    def test_user_list_counts_live_sessions_only(self):
        self.create()
        self.sign_in()
        self.clock.advance(30)
        self.sign_in()
        users = {u["username"]: u for u in self.auth.list_users()}
        self.assertEqual(users["anna"]["sessions"], 2)
        self.clock.advance(self.auth.settings.session_idle_seconds + 1)
        users = {u["username"]: u for u in self.auth.list_users()}
        self.assertEqual(users["anna"]["sessions"], 0)
        self.assertNotIn("totp_secret", users["anna"])
        self.assertNotIn("password_hash", users["anna"])

    def test_events_page_backwards(self):
        for name in ("u1", "u2", "u3"):
            self.create(name)
        newest = self.auth.list_events(2)
        self.assertEqual([e["username"] for e in newest], ["u3", "u2"])
        older = self.auth.list_events(2, before=newest[-1]["id"])
        self.assertEqual([e["username"] for e in older], ["u1"])
        self.assertEqual(self.auth.list_events(5, "u2", before=newest[0]["id"])[0]["username"], "u2")


class DirectoryAdminTest(AuthTestCase):
    def setUp(self):
        super().setUp()
        self.world = World()
        settings = directory_settings(self.dir)
        install(settings)
        self.store = store_module.Store(settings.db_path)
        self.addCleanup(self.store.close)
        checker = directory.Directory(settings, log=lambda m: None, connection_factory=self.world.factory)
        self.auth = auth.Authenticator(self.store, settings, KEY, clock=self.clock, directory=checker)

    def test_info_shows_settings_and_map_without_secrets(self):
        info = self.auth.directory_info()
        self.assertEqual((info["state"], info["pins"], info["users"]), ("ready", 1, 0))
        self.assertEqual(info["map"]["groups"][1]["name"], "AIBox-Analysts")
        self.assertEqual(len(info["map_version"]), 64)
        self.assertNotIn(SERVICE_PW, json.dumps(info))

    def test_saving_a_map_validates_normalises_and_checks_the_version(self):
        info = self.auth.directory_info()
        changed = {"groups": [{"dn": ANALYSTS, "roles": ["Analyst"]}],
                   "roles": {"analyst": ["/storage/drives/hr"]}}
        with self.assertRaises(auth.AuthError) as caught:
            self.auth.set_group_map("admin", {"groups": [], "roles": {}}, info["map_version"])
        self.assertEqual((caught.exception.code, caught.exception.status), ("invalid_group_map", 400))
        self.assertIn("groups", caught.exception.detail)
        self.assertRefused("map_changed", 409, self.auth.set_group_map, "admin", changed, "stale")
        saved = self.auth.set_group_map("admin", changed, info["map_version"], "10.0.0.1")
        self.assertEqual(saved["map"], {"groups": [{"dn": ANALYSTS, "name": "AIBox-Analysts",
                                                    "roles": ["analyst"]}],
                                        "roles": {"analyst": [HR]}})
        self.assertNotEqual(saved["map_version"], info["map_version"])
        # The same version cannot be used twice: the second save would lose the first.
        self.assertRefused("map_changed", 409, self.auth.set_group_map, "admin", changed,
                           info["map_version"])
        event = self.auth.list_events(1)[0]
        self.assertEqual((event["event"], event["actor"], event["detail"]),
                         ("group_map_updated", "admin", "groups=1 roles=1"))
        if os.name == "posix":
            self.assertEqual(os.stat(self.auth.settings.ldap_map_file).st_mode & 0o777, 0o600)

    def test_a_first_map_is_saved_against_version_none(self):
        os.remove(self.auth.settings.ldap_map_file)
        info = self.auth.directory_info()
        self.assertEqual((info["map"], info["map_version"]), (None, "none"))
        self.auth.set_group_map("admin", GROUP_MAP, "none")
        self.assertEqual(len(self.auth.directory_info()["map"]["groups"]), 2)

    def test_an_invalid_file_is_reported_and_can_be_replaced(self):
        with open(self.auth.settings.ldap_map_file, "w", encoding="utf-8") as handle:
            handle.write("{broken")
        info = self.auth.directory_info()
        self.assertIsNone(info["map"])
        self.assertIn("not valid JSON", info["map_error"])
        self.auth.set_group_map("admin", GROUP_MAP, info["map_version"])
        self.assertIsNone(self.auth.directory_info()["map_error"])

    def test_a_new_map_reaches_signed_in_directory_users_at_their_next_request(self):
        session, _secret = self.sign_in("anna", "Anna-AD-pw-1")
        self.assertEqual(self.auth.issue_token(session["session"])["user"]["drives"], [CASES])
        changed = json.loads(json.dumps(GROUP_MAP))
        changed["roles"]["analyst"] = [HR]
        self.auth.set_group_map("admin", changed, self.auth.directory_info()["map_version"])
        self.assertEqual(self.auth.issue_token(session["session"])["user"]["drives"], [HR])

    def test_check_reports_groups_and_a_user(self):
        report = self.auth.directory_check("admin", "anna", "10.0.0.1")
        self.assertEqual([g["found"] for g in report["groups"]], [True, True])
        self.assertEqual((report["user"]["roles"], report["user"]["may_sign_in"]), (["analyst"], True))
        self.assertEqual(self.auth.list_events(1)[0]["event"], "directory_checked")
        self.assertRefused("invalid_username", 400, self.auth.directory_check, "admin", "Bad Name!")
        self.world.down.update({"ldaps://dc01.bv.test", "ldaps://dc02.bv.test"})
        self.assertEqual(self.auth.directory_check("admin"), {"error": "unreachable"})

    def test_check_needs_a_configured_directory(self):
        settings = directory_settings(self.dir, ldap_urls=[])
        self.auth.directory = directory.Directory(settings, log=lambda m: None)
        self.assertRefused("directory_off", 409, self.auth.directory_check, "admin")


class AdminServiceTest(AuthTestCase):
    def setUp(self):
        super().setUp()
        self.create("admin", ADMIN_PW, roles=["admin"])
        self.create("anna")
        self.server = service.AuthServer(("127.0.0.1", 0), self.auth, SERVICE_KEY)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base = "http://127.0.0.1:%d" % self.server.server_address[1]
        self.admin = self.sign_in("admin", ADMIN_PW)[0]["session"]
        self.anna = self.sign_in("anna")[0]["session"]

    def call(self, path, body):
        request = urllib.request.Request(self.base + path, data=json.dumps(body).encode(), method="POST")
        request.add_header(service.KEY_HEADER, SERVICE_KEY.decode())
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_admin_routes(self):
        status, body = self.call("/auth/admin/system", {"session": self.admin})
        self.assertEqual((status, body["directory"]["state"]), (200, "off"))
        self.assertIn(body["clock"]["state"], ("synchronized", "unsynchronized", "unknown"))
        status, body = self.call("/auth/admin/directory", {"session": self.admin})
        self.assertEqual((status, body["directory"]["map_version"]), (200, "none"))
        status, body = self.call("/auth/admin/directory/map",
                                 {"session": self.admin, "map": {"groups": []}, "base": "none"})
        self.assertEqual((status, body["error"]), (400, "invalid_group_map"))
        self.assertIn("message", body)
        status, body = self.call("/auth/admin/directory/check", {"session": self.admin})
        self.assertEqual((status, body["error"]), (409, "directory_off"))
        status, body = self.call("/auth/admin/events", {"session": self.admin, "limit": 2})
        self.assertEqual((status, len(body["events"])), (200, 2))
        status, body = self.call("/auth/admin/events",
                                 {"session": self.admin, "before": body["events"][-1]["id"]})
        self.assertEqual(status, 200)
        status, body = self.call("/auth/admin/events", {"session": self.admin, "limit": "x"})
        self.assertEqual((status, body["error"]), (400, "invalid_limit"))
        status, body = self.call("/auth/admin/users", {"session": self.admin})
        self.assertEqual({u["username"]: u["sessions"] for u in body["users"]}, {"admin": 1, "anna": 1})

    def test_non_admins_are_refused_without_detail(self):
        for path in ("/auth/admin/system", "/auth/admin/directory", "/auth/admin/directory/map",
                     "/auth/admin/directory/check", "/auth/admin/events", "/auth/admin/users"):
            status, body = self.call(path, {"session": self.anna, "map": {}, "base": "none"})
            self.assertEqual((status, body), (403, {"ok": False, "error": "forbidden"}), path)
            status, body = self.call(path, {"session": "x" * 40})
            self.assertEqual((status, body["error"]), (401, "session_invalid"), path)


if __name__ == "__main__":
    unittest.main()
