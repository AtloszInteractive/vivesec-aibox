"""The directory login against a real Samba Active Directory (E27 S6).

Skipped unless AIBOX_SAMBA_CREDENTIALS names the credentials file that
`samba_ad/samba_ad.py start` writes (the test domain controller must run and
be reachable at the URLs in it). On the development box:

    python3 samba_ad/samba_ad.py start
    AIBOX_SAMBA_CREDENTIALS=~/standalone/samba-ad/credentials.json \\
        python3 -m unittest samba_ad_test
"""
import json
import os
import sys
import tempfile
import unittest

import auth
import config
import directory
import ldap_client as lc
import passwords
import store as store_module
import totp

sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "adapter"))
import identity  # noqa: E402

CREDENTIALS = os.environ.get("AIBOX_SAMBA_CREDENTIALS", "")
KEY = b"samba-test-key-0123456789abcdef0123456789"
ANALYST_ROOM, ADMIN_ROOM = "/storage/drives/samba-analysts/", "/storage/drives/samba-admins/"


def load():
    with open(os.path.expanduser(CREDENTIALS)) as handle:
        return json.load(handle)


@unittest.skipUnless(CREDENTIALS, "set AIBOX_SAMBA_CREDENTIALS to run against the Samba test domain")
class SambaDirectoryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = load()

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.dir = temp.name

    def settings(self, url=None, pins=None):
        settings = config.Settings(
            data_dir=self.dir, ldap_urls=[url or self.data["ldaps_url"]],
            ldap_tls_pins=[self.data["pin"]] if pins is None else pins,
            ldap_bind_dn=self.data["service_dn"], ldap_base_dn=self.data["base_dn"])
        directory.write_private(settings.ldap_bind_password_file,
                                (self.data["service_password"] + "\n").encode())
        group_map = {"groups": [{"dn": self.data["groups"]["admin"], "roles": ["admin"]},
                                {"dn": self.data["groups"]["analyst"], "roles": ["analyst"]}],
                     "roles": {"admin": [ADMIN_ROOM], "analyst": [ANALYST_ROOM]}}
        directory.write_private(settings.ldap_map_file, json.dumps(group_map).encode())
        return settings

    def directory(self, **kwargs):
        logged = []
        checker = directory.Directory(self.settings(**kwargs), log=logged.append)
        self.assertEqual(checker.state, "ready", checker.problem)
        return checker, logged

    def refused(self, checker, reason, name, password):
        with self.assertRaises(directory.Refused) as caught:
            checker.authenticate(name, password)
        self.assertEqual(caught.exception.reason, reason)

    def test_the_pin_matches_the_server_certificate(self):
        for url in (self.data["ldaps_url"], self.data["ldap_url"]):
            self.assertEqual(lc.fingerprint(lc.fetch_certificate(url, 5)), self.data["pin"])

    def test_ldaps_and_starttls(self):
        for url in (self.data["ldaps_url"], self.data["ldap_url"]):
            checker, _ = self.directory(url=url)
            user = checker.authenticate("bob", self.data["user_password"])
            self.assertEqual((user.roles, user.drives), (["admin"], [ADMIN_ROOM]))

    def test_nested_membership_and_unicode_names(self):
        checker, _ = self.directory()
        user = checker.authenticate("alice", self.data["user_password"])
        self.assertEqual((user.groups, user.roles, user.drives), (["AIBox-Analysts"], ["analyst"], [ANALYST_ROOM]))
        self.assertEqual(user.display_name, "Alíz Kovács")
        self.assertTrue(user.dn.startswith("CN=Alíz Kovács,OU=People,"), user.dn)

    def test_refusals(self):
        checker, _ = self.directory()
        self.refused(checker, "bad_password", "alice", self.data["user_password"] + "x")
        self.refused(checker, "disabled", "carol", self.data["user_password"])
        self.refused(checker, "not_authorized", "dave", self.data["user_password"])
        self.refused(checker, "unknown_user", "nobody", self.data["user_password"])
        self.refused(checker, "unknown_user", "*", self.data["user_password"])
        self.refused(checker, "empty_password", "alice", "")

    def test_an_unpinned_certificate_is_refused_before_any_bind(self):
        checker, logged = self.directory(pins=["00" * 32])
        with self.assertRaises(directory.Unavailable) as caught:
            checker.authenticate("alice", self.data["user_password"])
        self.assertEqual(caught.exception.reason, "certificate_not_pinned")
        self.assertTrue(any(lc.format_fingerprint(self.data["pin"]) in line for line in logged))

    def test_a_wrong_service_password_fails_closed(self):
        checker, _ = self.directory()
        directory.write_private(checker.settings.ldap_bind_password_file, b"wrong\n")
        with self.assertRaises(directory.Unavailable) as caught:
            checker.authenticate("alice", self.data["user_password"])
        self.assertEqual(caught.exception.reason, "service_bind_failed")

    def test_check_reports_the_groups_and_a_user(self):
        checker, _ = self.directory()
        report = checker.check("alice")
        self.assertEqual([group["found"] for group in report["groups"]], [True, True])
        self.assertEqual(report["user"]["roles"], ["analyst"])

    def test_full_login_with_totp_and_a_token_the_adapter_accepts(self):
        saved = passwords.N
        passwords.N = 2 ** 10
        self.addCleanup(setattr, passwords, "N", saved)
        settings = self.settings()
        store = store_module.Store(settings.db_path)
        self.addCleanup(store.close)
        authenticator = auth.Authenticator(store, settings, KEY)
        first = authenticator.login("Alice", self.data["user_password"], "10.0.0.7")
        self.assertEqual(first["step"], "enroll")
        import time  # noqa: PLC0415 - the real clock: the code goes to the real TOTP check
        session = authenticator.complete_login(first["challenge"],
                                               totp.code_at(first["totp_secret"], time.time()), "10.0.0.7")
        token = authenticator.issue_token(session["session"])["token"]
        key_file = os.path.join(self.dir, "identity.key")
        with open(key_file, "wb") as handle:
            handle.write(KEY + b"\n")
        verifier = identity.Verifier(mode=identity.MODE_REQUIRE, algorithms=("HS256",),
                                     hs256_secret_file=key_file, issuer=config.DEFAULT_ISSUER,
                                     audience=config.DEFAULT_AUDIENCE)
        who = verifier.resolve({"VVS-Identity": token, "VVS-User": "alice"})
        self.assertEqual((who.user, list(who.roles), list(who.drives), list(who.groups)),
                         ("alice", ["analyst"], [ANALYST_ROOM], ["AIBox-Analysts"]))


if __name__ == "__main__":
    unittest.main()
