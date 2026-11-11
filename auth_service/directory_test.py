import io
import json
import os
import tempfile
import unittest

import auth
import config
import directory
import ldap_client as lc
import manage
import passwords
import store as store_module
import totp
from auth_test import KEY, AuthTestCase

SERVICE_DN, SERVICE_PW = "CN=svc-aibox,OU=Service,DC=bv,DC=test", "Service-pw-2026"
BASE = "DC=bv,DC=test"
ADMINS = "CN=AIBox-Admins,OU=Groups,DC=bv,DC=test"
ANALYSTS = "CN=AIBox-Analysts,OU=Groups,DC=bv,DC=test"
TEAM = "CN=Team-A,OU=Groups,DC=bv,DC=test"
CASES, HR = "/storage/drives/cases/", "/storage/drives/hr/"
GROUP_MAP = {"groups": [{"dn": ADMINS, "name": "aibox-admins", "roles": ["admin"]},
                        {"dn": ANALYSTS, "roles": ["analyst"]}],
             "roles": {"admin": [CASES, HR], "analyst": [CASES]}}
PIN = "ab" * 32


class World:
    """A small Active Directory: users, nested groups, servers that are down
    or present an unpinned certificate. Records every operation."""

    def __init__(self):
        self.users = {
            "anna": {"dn": "CN=Anna Kovacs,OU=People,DC=bv,DC=test", "password": "Anna-AD-pw-1",
                     "display": "Kovács Anna", "uac": 512, "groups": {TEAM}},
            "bela": {"dn": "CN=Bela,OU=People,DC=bv,DC=test", "password": "Bela-AD-pw-1",
                     "display": "Béla", "uac": 512, "groups": {ADMINS}},
            "cecil": {"dn": "CN=Cecil,OU=People,DC=bv,DC=test", "password": "Cecil-AD-pw-1",
                      "display": "", "uac": 514, "groups": {ANALYSTS}},
            "dora": {"dn": "CN=Dora,OU=People,DC=bv,DC=test", "password": "Dora-AD-pw-1",
                     "display": "Dóra", "uac": 512, "groups": set()},
        }
        self.nested = {TEAM: {ANALYSTS}}   # Team-A is a member of AIBox-Analysts
        self.groups = {ADMINS, ANALYSTS, TEAM}
        self.down, self.unpinned, self.refusal = set(), set(), {}
        self.log = []

    def closure(self, groups):
        result, todo = set(), list(groups)
        while todo:
            group = todo.pop()
            if group not in result:
                result.add(group)
                todo.extend(self.nested.get(group, ()))
        return result

    def by_dn(self, dn):
        return next((u for u in self.users.values() if u["dn"].lower() == dn.lower()), None)

    def factory(self, url, tls, timeout):
        return FakeConnection(self, url)


def _filter(data):
    tag, content, _ = lc._read_tlv(data, 0)
    if tag in (0xA0, 0xA1):
        return (tag, [_filter(lc._tlv(t, v)) for t, v in lc._children(content)])
    if tag == 0xA3:
        attribute, value = [v.decode() for _t, v in lc._children(content)]
        return ("eq", attribute, value)
    if tag == 0x87:
        return ("present", content.decode())
    if tag == 0xA9:
        rule, attribute, value = [v.decode() for _t, v in lc._children(content)]
        return ("ext", rule, attribute, value)
    raise AssertionError("unexpected filter")


class FakeConnection:
    def __init__(self, world, url):
        self.world, self.url = world, url

    def open(self):
        self.world.log.append(("open", self.url))
        if self.url in self.world.down:
            raise lc.LdapError("cannot connect")
        if self.url in self.world.unpinned:
            raise lc.TlsPinError("cd" * 32)

    def close(self):
        self.world.log.append(("close", self.url))

    def bind(self, dn, password):
        if not password:
            raise ValueError("a bind needs a password")
        self.world.log.append(("bind", dn))
        if dn == SERVICE_DN and password == SERVICE_PW:
            return
        user = self.world.by_dn(dn)
        if user is None or user["password"] != password:
            raise lc.ResultError("bind", 49, "80090308: LdapErr: DSID-0C09044E, comment: "
                                             "AcceptSecurityContext error, data 52e, v4563")
        name = next(n for n, u in self.world.users.items() if u is user)
        if name in self.world.refusal:
            raise lc.ResultError("bind", 49, "AcceptSecurityContext error, data %s, v4563"
                                 % self.world.refusal[name])

    def search(self, base, scope, filter_bytes, attributes=(lc.NO_ATTRIBUTES,), size_limit=10):
        query = _filter(filter_bytes)
        self.world.log.append(("search", base, query))
        if scope == lc.SCOPE_BASE and query[0] == "present":
            return [(base, {})] if base in self.world.groups else []
        if scope == lc.SCOPE_BASE and query[0] == "ext":
            assert query[1] == lc.IN_CHAIN and query[2] == "member"
            user = self.world.by_dn(query[3])
            member = user is not None and base in self.world.closure(user["groups"])
            return [(base, {})] if member else []
        assert scope == lc.SCOPE_SUBTREE and base == BASE and query[0] == 0xA0
        wanted = {item[1]: item[2] for item in query[1] if item[0] == "eq"}
        assert wanted["objectClass"] == "user" and wanted["objectCategory"] == "person"
        name = wanted["sAMAccountName"].lower()
        user = self.world.users.get(name)
        if user is None:
            return []
        return [(user["dn"], {"samaccountname": [name.encode()],
                              "displayname": [user["display"].encode()] if user["display"] else [],
                              "useraccountcontrol": [str(user["uac"]).encode()]})]


def directory_settings(data_dir, **overrides):
    values = dict(data_dir=data_dir, ldap_urls=["ldaps://dc01.bv.test", "ldaps://dc02.bv.test"],
                  ldap_tls_pins=[PIN], ldap_bind_dn=SERVICE_DN, ldap_base_dn=BASE)
    values.update(overrides)
    return config.Settings(**values)


def install(settings, group_map=GROUP_MAP, password=SERVICE_PW):
    if group_map is not None:
        directory.write_private(settings.ldap_map_file, json.dumps(group_map).encode())
    if password is not None:
        directory.write_private(settings.ldap_bind_password_file, (password + "\n").encode())


class DirectoryTestCase(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.dir = temp.name
        self.world = World()
        self.logged = []

    def make(self, **overrides):
        settings = directory_settings(self.dir, **overrides)
        return directory.Directory(settings, log=self.logged.append, connection_factory=self.world.factory)


class ConfigurationTest(DirectoryTestCase):
    def test_off_without_urls(self):
        self.assertEqual(self.make(ldap_urls=[]).state, "off")

    def test_an_unusable_configuration_switches_the_directory_off(self):
        for overrides, problem in (({"ldap_tls_pins": []}, "pinned"),
                                   ({"ldap_tls_pins": ["xyz"]}, "fingerprint"),
                                   ({"ldap_urls": ["http://dc01"]}, "only ldaps"),
                                   ({"ldap_base_dn": ""}, "BASE_DN"),
                                   ({"ldap_bind_dn": ""}, "service account"),
                                   ({"ldap_user_attribute": "cn"}, "USER_ATTRIBUTE"),
                                   ({"ldap_ca_file": os.path.join(self.dir, "none.pem")}, "does not exist")):
            checker = self.make(**overrides)
            self.assertEqual(checker.state, "invalid", overrides)
            self.assertIn(problem, checker.problem)
            self.assertRaises(directory.Unavailable, checker.authenticate, "anna", "x")
        self.assertEqual(self.make().state, "ready")

    def test_settings_from_the_environment(self):
        settings = config.Settings.from_env({
            "AUTH_DATA_DIR": self.dir, "AUTH_LDAP_URLS": "ldaps://dc01, ldaps://dc02",
            "AUTH_LDAP_TLS_PINS": "%s %s" % (PIN, "cd" * 32), "AUTH_LDAP_BIND_DN": SERVICE_DN,
            "AUTH_LDAP_BASE_DN": BASE, "AUTH_LDAP_REFRESH_SECONDS": "600",
            "AUTH_LDAP_MAX_STALE_SECONDS": "120"})
        self.assertEqual(settings.ldap_urls, ["ldaps://dc01", "ldaps://dc02"])
        self.assertEqual(settings.ldap_tls_pins, [PIN, "cd" * 32])
        self.assertEqual(settings.ldap_map_file, os.path.join(self.dir, "ldap-groups.json"))
        self.assertEqual(settings.ldap_bind_password_file, os.path.join(self.dir, "ldap-bind.password"))
        # The grace period is never shorter than the refresh interval.
        self.assertEqual((settings.ldap_refresh_seconds, settings.ldap_max_stale_seconds), (600, 600))
        self.assertEqual(config.Settings.from_env({}).ldap_urls, [])


class GroupMapTest(unittest.TestCase):
    def test_resolve_merges_roles_and_rooms(self):
        group_map = directory.parse_group_map(GROUP_MAP)
        self.assertEqual(group_map.groups[1][1], "AIBox-Analysts")  # the CN by default
        self.assertEqual(group_map.resolve({ADMINS, ANALYSTS}),
                         (["aibox-admins", "AIBox-Analysts"], ["admin", "analyst"], [CASES, HR]))
        self.assertEqual(group_map.resolve(set()), ([], [], []))

    def test_invalid_maps(self):
        def broken(change):
            data = json.loads(json.dumps(GROUP_MAP))
            change(data)
            return data
        for data in (
                [], {"groups": GROUP_MAP["groups"]}, broken(lambda d: d.update(extra=1)),
                broken(lambda d: d["groups"][0]["roles"].append("ghost")),
                broken(lambda d: d["groups"][0].update(roles=[])),
                broken(lambda d: d["groups"][0].update(name="Nyomozók")),
                broken(lambda d: d["groups"][0].update(dn="no-equals-sign")),
                broken(lambda d: d["groups"][0].update(dn="CN=x\n,DC=y")),
                broken(lambda d: d["groups"].append(dict(d["groups"][0]))),
                broken(lambda d: d["groups"][0].update(other=1)),
                broken(lambda d: d["roles"].update(analyst=["../etc"])),
                broken(lambda d: d["roles"].update({"Bad Role": []})),
                broken(lambda d: d.update(groups=[])),
                broken(lambda d: d["roles"].update(many=["/storage/drives/r%d/" % i for i in range(70)])),
                broken(lambda d: d["groups"].append({"dn": "CN=x\\,y,DC=t", "roles": ["admin"]}))):
            self.assertRaises(ValueError, directory.parse_group_map, data)

    def test_ad_refusal_codes(self):
        def error(diagnostic):
            return lc.ResultError("bind", 49, diagnostic)
        self.assertEqual(directory.ad_refusal(error("AcceptSecurityContext error, data 775, v4563")), "locked")
        self.assertEqual(directory.ad_refusal(error("... data 52e, v1db1")), "bad_password")
        self.assertEqual(directory.ad_refusal(error("... data 9ff, v1")), "refused_9ff")
        self.assertEqual(directory.ad_refusal(error("invalid credentials")), "bad_password")


class AuthenticateTest(DirectoryTestCase):
    def setUp(self):
        super().setUp()
        self.directory = self.make()
        install(self.directory.settings)

    def refused(self, reason, name, password):
        with self.assertRaises(directory.Refused) as caught:
            self.directory.authenticate(name, password)
        self.assertEqual(caught.exception.reason, reason)
        return caught.exception

    def test_nested_membership_grants_the_roles_and_rooms(self):
        user = self.directory.authenticate("anna", "Anna-AD-pw-1")
        self.assertEqual((user.dn, user.display_name), ("CN=Anna Kovacs,OU=People,DC=bv,DC=test", "Kovács Anna"))
        self.assertEqual((user.groups, user.roles, user.drives), (["AIBox-Analysts"], ["analyst"], [CASES]))
        binds = [entry[1] for entry in self.world.log if entry[0] == "bind"]
        self.assertEqual(binds, [SERVICE_DN, user.dn])
        self.assertEqual(self.world.log[-1][0], "close")

    def test_refusals(self):
        self.assertFalse(self.refused("unknown_user", "zoltan", "x").user_found)
        self.assertTrue(self.refused("disabled", "cecil", "Cecil-AD-pw-1").user_found)
        self.assertTrue(self.refused("bad_password", "anna", "wrong").user_found)
        self.assertTrue(self.refused("not_authorized", "dora", "Dora-AD-pw-1").user_found)
        self.world.refusal["anna"] = "775"
        self.refused("locked", "anna", "Anna-AD-pw-1")
        self.world.refusal["anna"] = "773"
        self.refused("password_must_change", "anna", "Anna-AD-pw-1")

    def test_an_empty_password_never_reaches_the_directory(self):
        self.refused("empty_password", "anna", "")
        self.refused("empty_password", "anna", None)
        self.refused("password_too_long", "anna", "x" * 2000)
        self.assertEqual(self.world.log, [])

    def test_failover_and_failing_closed(self):
        self.world.down.add("ldaps://dc01.bv.test")
        self.assertEqual(self.directory.authenticate("bela", "Bela-AD-pw-1").roles, ["admin"])
        self.world.down.add("ldaps://dc02.bv.test")
        with self.assertRaises(directory.Unavailable) as caught:
            self.directory.authenticate("bela", "Bela-AD-pw-1")
        self.assertEqual(caught.exception.reason, "unreachable")
        self.world.down.clear()
        self.world.log.clear()
        self.world.unpinned.update({"ldaps://dc01.bv.test", "ldaps://dc02.bv.test"})
        with self.assertRaises(directory.Unavailable) as caught:
            self.directory.authenticate("bela", "Bela-AD-pw-1")
        self.assertEqual(caught.exception.reason, "certificate_not_pinned")
        self.assertTrue(any("CD:CD" in line for line in self.logged))
        self.assertNotIn("bind", [entry[0] for entry in self.world.log])

    def test_service_account_problems(self):
        install(self.directory.settings, group_map=None, password="wrong")
        with self.assertRaises(directory.Unavailable) as caught:
            self.directory.authenticate("bela", "Bela-AD-pw-1")
        self.assertEqual(caught.exception.reason, "service_bind_failed")
        os.unlink(self.directory.settings.ldap_bind_password_file)
        with self.assertRaises(directory.Unavailable) as caught:
            self.directory.authenticate("bela", "Bela-AD-pw-1")
        self.assertEqual(caught.exception.reason, "service_password_missing")

    def test_group_map_problems_and_reload(self):
        path = self.directory.settings.ldap_map_file
        with open(path, "w") as handle:
            handle.write("{broken")
        with self.assertRaises(directory.Unavailable) as caught:
            self.directory.authenticate("bela", "Bela-AD-pw-1")
        self.assertEqual(caught.exception.reason, "group_map_invalid")
        changed = json.loads(json.dumps(GROUP_MAP))
        changed["groups"][0]["roles"] = ["analyst"]
        install(self.directory.settings, group_map=changed, password=None)
        self.assertEqual(self.directory.authenticate("bela", "Bela-AD-pw-1").roles, ["analyst"])
        os.unlink(path)
        with self.assertRaises(directory.Unavailable) as caught:
            self.directory.authenticate("bela", "Bela-AD-pw-1")
        self.assertEqual(caught.exception.reason, "group_map_missing")

    def test_lookup_and_check(self):
        self.assertEqual(self.directory.lookup("anna").roles, ["analyst"])
        self.world.users["anna"]["uac"] = 514
        self.assertRaises(directory.Refused, self.directory.lookup, "anna")
        self.world.groups.discard(ADMINS)
        report = self.directory.check("bela")
        self.assertEqual([g["found"] for g in report["groups"]], [False, True])
        self.assertEqual(report["user"]["roles"], ["admin"])
        self.assertEqual(self.directory.check("zoltan")["user"]["refused"], "unknown_user")
        self.assertNotIn(("bind", self.world.users["bela"]["dn"]), self.world.log)


class DirectoryLoginTest(AuthTestCase):
    """The authenticator with a directory (E27 S6)."""

    def setUp(self):
        super().setUp()
        self.world = World()
        self.logged = []
        self.auth = self.make_directory()

    def make_directory(self, **overrides):
        settings = directory_settings(self.dir, **overrides)
        install(settings)
        store = store_module.Store(settings.db_path)
        self.addCleanup(store.close)
        checker = directory.Directory(settings, log=self.logged.append, connection_factory=self.world.factory)
        return auth.Authenticator(store, settings, KEY, clock=self.clock, directory=checker)

    def events(self, name=None):
        return [(e["event"], e["detail"]) for e in reversed(self.auth.list_events(100, name))]

    def test_first_login_creates_the_user_and_the_token_carries_the_directory_roles(self):
        session, secret = self.sign_in("anna", "Anna-AD-pw-1")
        token = self.auth.issue_token(session["session"])
        user = token["user"]
        self.assertEqual((user["source"], user["roles"], user["drives"], user["groups"]),
                         ("ldap", ["analyst"], [CASES], ["AIBox-Analysts"]))
        self.assertEqual(user["display_name"], "Kovács Anna")
        self.assertFalse(session["password_change_required"])
        record = self.auth.store.get_user("anna")
        self.assertEqual((record["source"], record["password_hash"]), ("ldap", None))
        self.assertIn(("directory_user_created", "roles=analyst"), self.events("anna"))
        # The second login asks for the code of the enrolled authenticator.
        self.assertEqual(self.auth.login("anna", "Anna-AD-pw-1", "10.0.0.5")["step"], "totp")

    def test_a_local_user_wins_over_the_directory(self):
        self.auth.create_user("cli", "bela", "Local-bela-pw-2026", roles=["analyst"])
        self.assertRefused("invalid_credentials", 401, self.auth.login, "bela", "Bela-AD-pw-1")
        self.assertEqual(self.auth.login("bela", "Local-bela-pw-2026")["step"], "enroll")
        self.assertNotIn("open", [entry[0] for entry in self.world.log])

    def test_refusals_answer_invalid_credentials(self):
        for name, password in (("zoltan", "x"), ("cecil", "Cecil-AD-pw-1"), ("dora", "Dora-AD-pw-1"),
                               ("anna", "wrong"), ("anna", "")):
            self.assertRefused("invalid_credentials", 401, self.auth.login, name, password, "10.0.0.9")
        self.assertIsNone(self.auth.store.get_user("anna"))
        details = [d for e, d in self.events() if e == "login_failed"]
        self.assertEqual(details, ["directory_unknown_user", "directory_disabled", "directory_not_authorized",
                                   "directory_bad_password", "directory_empty_password"])
        names = [e["username"] for e in reversed(self.auth.list_events(100)) if e["event"] == "login_failed"]
        self.assertEqual(names, ["", "cecil", "dora", "anna", ""])

    def test_wrong_passwords_lock_a_known_directory_user_without_asking_the_directory(self):
        self.sign_in("anna", "Anna-AD-pw-1")
        for _ in range(self.auth.settings.lockout_threshold):
            self.assertRefused("invalid_credentials", 401, self.auth.login, "anna", "wrong")
        self.assertGreater(self.auth.store.get_user("anna")["locked_until"], self.clock())
        opened = len([e for e in self.world.log if e[0] == "open"])
        self.assertRefused("invalid_credentials", 401, self.auth.login, "anna", "Anna-AD-pw-1")
        self.assertEqual(len([e for e in self.world.log if e[0] == "open"]), opened)

    def test_wrong_passwords_of_a_new_directory_user_stop_before_the_directory_locks_it(self):
        threshold = self.auth.settings.lockout_threshold
        for index in range(threshold):
            self.assertRefused("invalid_credentials", 401, self.auth.login, "anna", "wrong",
                               "10.0.1.%d" % index)
        binds = len([e for e in self.world.log if e[0] == "bind" and e[1] != SERVICE_DN])
        self.assertEqual(binds, threshold)
        self.assertRefused("invalid_credentials", 401, self.auth.login, "anna", "Anna-AD-pw-1", "10.0.2.1")
        self.assertEqual(len([e for e in self.world.log if e[0] == "bind" and e[1] != SERVICE_DN]), binds)
        self.clock.advance(self.auth.settings.lockout_seconds)
        self.assertEqual(self.auth.login("anna", "Anna-AD-pw-1", "10.0.2.1")["step"], "enroll")

    def test_an_unreachable_directory_fails_closed(self):
        self.world.down.update({"ldaps://dc01.bv.test", "ldaps://dc02.bv.test"})
        self.assertRefused("directory_unavailable", 503, self.auth.login, "anna", "Anna-AD-pw-1")
        self.assertIn(("directory_unavailable", "login: unreachable"), self.events())
        # The local emergency administrator still signs in.
        self.auth.create_user("cli", "admin", "Admin-jelszo-2026", roles=["admin"])
        self.assertEqual(self.auth.login("admin", "Admin-jelszo-2026")["step"], "enroll")

    def test_an_invalid_configuration_fails_closed_but_keeps_local_logins(self):
        self.auth = self.make_directory(ldap_tls_pins=[])
        self.assertEqual(self.auth.directory.state, "invalid")
        self.assertRefused("directory_unavailable", 503, self.auth.login, "anna", "Anna-AD-pw-1")
        self.auth.create_user("cli", "admin", "Admin-jelszo-2026", roles=["admin"])
        self.assertEqual(self.auth.login("admin", "Admin-jelszo-2026")["step"], "enroll")
        self.assertEqual(self.world.log, [])

    def test_the_refresh_follows_the_directory(self):
        session, _ = self.sign_in("anna", "Anna-AD-pw-1")
        secret = session["session"]
        self.world.users["anna"]["groups"] = {ADMINS}
        self.clock.advance(self.auth.settings.ldap_refresh_seconds - 1)
        self.assertEqual(self.auth.issue_token(secret)["user"]["roles"], ["analyst"])
        self.clock.advance(2)
        self.assertEqual(self.auth.issue_token(secret)["user"]["roles"], ["admin"])
        self.assertIn(("directory_user_updated", "roles=admin"), self.events("anna"))
        self.assertEqual(self.auth.admin_actor(secret), "anna")

    def test_a_user_the_directory_drops_loses_every_session(self):
        for change in (lambda u: u.update(uac=514), lambda u: u.update(groups=set())):
            self.world = World()
            self.auth = self.make_directory()
            session, _ = self.sign_in("anna", "Anna-AD-pw-1")
            change(self.world.users["anna"])
            self.clock.advance(self.auth.settings.ldap_refresh_seconds)
            self.assertRefused("session_invalid", 401, self.auth.me, session["session"])
            self.assertEqual(self.auth.store.count_sessions("anna"), 0)
            self.assertEqual(self.events("anna")[-1][0], "directory_access_ended")

    def test_an_outage_keeps_sessions_for_the_grace_period_then_fails_closed(self):
        session, _ = self.sign_in("anna", "Anna-AD-pw-1")
        secret = session["session"]
        self.world.down.update({"ldaps://dc01.bv.test", "ldaps://dc02.bv.test"})
        self.clock.advance(self.auth.settings.ldap_refresh_seconds)
        self.assertEqual(self.auth.issue_token(secret)["user"]["roles"], ["analyst"])
        attempts = len([e for e in self.world.log if e[0] == "open"])
        self.auth.me(secret)
        self.assertEqual(len([e for e in self.world.log if e[0] == "open"]), attempts)  # retry backoff
        self.clock.advance(self.auth.settings.ldap_max_stale_seconds)
        self.assertRefused("directory_unavailable", 503, self.auth.issue_token, secret)
        self.assertRefused("directory_unavailable", 503, self.auth.me, secret)
        self.assertEqual(self.auth.store.count_sessions("anna"), 1)
        self.world.down.clear()
        self.clock.advance(auth.DIRECTORY_RETRY_SECONDS)
        self.assertEqual(self.auth.issue_token(secret)["user"]["roles"], ["analyst"])

    def test_switching_the_directory_off_ends_directory_sessions(self):
        session, _ = self.sign_in("anna", "Anna-AD-pw-1")
        store = self.auth.store
        self.auth = auth.Authenticator(store, config.Settings(data_dir=self.dir), KEY, clock=self.clock)
        self.assertEqual(self.auth.directory.state, "off")
        self.assertRefused("invalid_credentials", 401, self.auth.login, "anna", "Anna-AD-pw-1")
        self.clock.advance(self.auth.settings.ldap_refresh_seconds)
        self.assertRefused("session_invalid", 401, self.auth.me, session["session"])

    def test_the_directory_owns_passwords_roles_and_rooms(self):
        session, _ = self.sign_in("anna", "Anna-AD-pw-1")
        self.assertRefused("managed_by_directory", 403, self.auth.change_password,
                           session["session"], "Anna-AD-pw-1", "Something-new-2026")
        self.assertRefused("managed_by_directory", 409, self.auth.update_user, "cli", "anna", roles=["admin"])
        self.assertRefused("managed_by_directory", 409, self.auth.update_user, "cli", "anna", drives=[HR])
        self.assertRefused("managed_by_directory", 409, self.auth.reset_password, "cli", "anna",
                           "Something-new-2026")
        # Suspension, TOTP reset and unlock stay with the box's administrator.
        self.assertEqual(self.auth.update_user("cli", "anna", status="suspended")["status"], "suspended")
        self.assertEqual(self.auth.store.count_sessions("anna"), 0)
        self.assertRefused("invalid_credentials", 401, self.auth.login, "anna", "Anna-AD-pw-1")
        self.auth.update_user("cli", "anna", status="active")
        self.assertFalse(self.auth.reset_totp("cli", "anna")["totp_enrolled"])
        self.assertEqual(self.auth.login("anna", "Anna-AD-pw-1")["step"], "enroll")


class MigrationTest(unittest.TestCase):
    def test_a_version_1_database_gains_the_directory_columns(self):
        import sqlite3
        with tempfile.TemporaryDirectory() as temp:
            path = os.path.join(temp, "auth.db")
            connection = sqlite3.connect(path)
            connection.executescript(
                "CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);"
                "INSERT INTO meta VALUES ('schema', '1');"
                "CREATE TABLE users (username TEXT PRIMARY KEY, display_name TEXT NOT NULL DEFAULT '',"
                " source TEXT NOT NULL DEFAULT 'local', password_hash TEXT, roles TEXT NOT NULL DEFAULT '[]',"
                " user_groups TEXT NOT NULL DEFAULT '[]', drives TEXT NOT NULL DEFAULT '[]',"
                " status TEXT NOT NULL DEFAULT 'active', must_change_password INTEGER NOT NULL DEFAULT 0,"
                " totp_secret TEXT, totp_last_step INTEGER, failed_count INTEGER NOT NULL DEFAULT 0,"
                " locked_until REAL NOT NULL DEFAULT 0, created_at REAL NOT NULL, updated_at REAL NOT NULL,"
                " password_changed_at REAL, last_login_at REAL);"
                "INSERT INTO users (username, created_at, updated_at) VALUES ('admin', 1, 1);")
            connection.close()
            store = store_module.Store(path)
            try:
                self.assertIsNone(store.get_user("admin")["directory_synced_at"])
                store.update_user("admin", 2, directory_dn="CN=x", directory_synced_at=2)
                self.assertEqual(store.get_user("admin")["directory_dn"], "CN=x")
                self.assertEqual(store._one("SELECT value FROM meta WHERE key = 'schema'")["value"], "2")
            finally:
                store.close()


class ManageLdapTest(unittest.TestCase):
    def setUp(self):
        saved = passwords.N
        passwords.N = 2 ** 10
        self.addCleanup(setattr, passwords, "N", saved)
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.dir = os.path.join(temp.name, "auth")
        os.makedirs(self.dir)
        self.env = {"AUTH_DATA_DIR": self.dir}

    def run_cli(self, *argv, stdin=""):
        out = io.StringIO()
        code = manage.run(list(argv), env=self.env, out=out, stdin=io.StringIO(stdin))
        return code, out.getvalue()

    def test_service_password_and_group_map(self):
        code, out = self.run_cli("ldap-password", "--password-stdin", stdin=SERVICE_PW + "\n")
        self.assertEqual(code, 0)
        path = os.path.join(self.dir, "ldap-bind.password")
        with open(path) as handle:
            self.assertEqual(handle.read(), SERVICE_PW + "\n")
        code, out = self.run_cli("ldap-map", "import", "-", stdin=json.dumps(GROUP_MAP))
        self.assertIn("2 groups, 2 roles", out)
        code, out = self.run_cli("ldap-map", "show")
        self.assertEqual(json.loads(out)["groups"][0]["name"], "aibox-admins")
        if os.name == "posix":
            for name in ("ldap-bind.password", "ldap-groups.json"):
                self.assertEqual(os.stat(os.path.join(self.dir, name)).st_mode & 0o777, 0o600)
        bad = dict(GROUP_MAP, groups=[{"dn": ADMINS, "roles": ["ghost"]}])
        self.assertRaises(ValueError, self.run_cli, "ldap-map", "import", "-", stdin=json.dumps(bad))
        code, out = self.run_cli("ldap-map", "show")
        self.assertEqual(len(json.loads(out)["groups"]), 2)  # the valid map was kept

    def test_check_reports_the_state(self):
        code, out = self.run_cli("ldap-check")
        self.assertEqual((code, json.loads(out)["state"]), (1, "off"))
        self.env.update(AUTH_LDAP_URLS="ldaps://dc01", AUTH_LDAP_BASE_DN=BASE, AUTH_LDAP_BIND_DN=SERVICE_DN)
        code, out = self.run_cli("ldap-check")
        self.assertEqual((code, json.loads(out)["state"]), (1, "invalid"))
        self.assertIn("pinned", json.loads(out)["problem"])


if __name__ == "__main__":
    unittest.main()
