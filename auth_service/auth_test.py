import json
import os
import sys
import tempfile
import threading
import unittest

import auth
import config
import passwords
import store as store_module
import totp

ADAPTER_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "adapter")
sys.path.append(os.path.abspath(ADAPTER_DIR))
import identity  # noqa: E402  the adapter's verifier: the real contract

KEY = b"0123456789abcdef0123456789abcdef-test"
ADMIN_PW = "Admin-jelszo-2026"
USER_PW = "Anna-jelszava-2026"


class Clock:
    def __init__(self, now=1800000000.0):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class AuthTestCase(unittest.TestCase):
    def setUp(self):
        saved = passwords.N
        passwords.N = 2 ** 10
        self.addCleanup(setattr, passwords, "N", saved)
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.dir = temp.name
        self.clock = Clock()
        self.auth = self.make()

    def make(self, **overrides):
        settings = config.Settings(data_dir=self.dir, **overrides)
        store = store_module.Store(settings.db_path)
        self.addCleanup(store.close)
        return auth.Authenticator(store, settings, KEY, clock=self.clock)

    def create(self, name="anna", password=USER_PW, temporary=False, **fields):
        return self.auth.create_user("cli", name, password, temporary=temporary, **fields)

    def sign_in(self, name="anna", password=USER_PW, client="10.0.0.5"):
        """A full login; enrols the authenticator on the first one. Returns
        the session answer and the user's TOTP secret."""
        first = self.auth.login(name, password, client)
        if first["step"] == "enroll":
            secret = first["totp_secret"]
        else:
            secret = self.secrets[name]
        result = self.auth.complete_login(first["challenge"], totp.code_at(secret, self.clock()), client)
        self.secrets[name] = secret
        return result, secret

    @property
    def secrets(self):
        if not hasattr(self, "_secrets"):
            self._secrets = {}
        return self._secrets

    def assertRefused(self, expected_code, expected_status, function, *args, **kwargs):
        with self.assertRaises(auth.AuthError) as caught:
            function(*args, **kwargs)
        self.assertEqual((caught.exception.code, caught.exception.status),
                         (expected_code, expected_status))


class LoginTest(AuthTestCase):
    def test_first_login_enrols_then_issues_a_token_the_adapter_accepts(self):
        self.create(display_name="Kiss Anna", roles=["Analyst"], groups=["Nyomozok"],
                    drives=["/storage/drives/ugy-2026-17", "/storage/drives/kozos/"])
        first = self.auth.login(" Anna ", USER_PW, "10.0.0.5")
        self.assertEqual(first["step"], "enroll")
        self.assertIn("otpauth://totp/ViVeSec%20AI%20Box:anna?", first["otpauth_uri"])
        self.assertRefused("invalid_code", 401, self.auth.complete_login, first["challenge"], "000000")
        code = totp.code_at(first["totp_secret"], self.clock())
        session = self.auth.complete_login(first["challenge"], code, "10.0.0.5")
        self.assertFalse(session["password_change_required"])
        self.assertEqual(session["user"]["drives"],
                         ["/storage/drives/ugy-2026-17/", "/storage/drives/kozos/"])

        issued = self.auth.issue_token(session["session"])
        key_file = os.path.join(self.dir, "identity.key")
        with open(key_file, "wb") as handle:
            handle.write(KEY + b"\n")
        verifier = identity.Verifier(mode=identity.MODE_REQUIRE, algorithms=("HS256",),
                                     hs256_secret_file=key_file, issuer=config.DEFAULT_ISSUER,
                                     audience=config.DEFAULT_AUDIENCE, clock=self.clock)
        who = verifier.resolve({"VVS-Identity": issued["token"], "VVS-User": "anna"})
        self.assertEqual((who.user, who.upn, who.source), ("anna", "anna", identity.SOURCE_TOKEN))
        self.assertEqual(list(who.roles), ["analyst"])
        self.assertEqual(list(who.groups), ["Nyomozok"])
        self.assertEqual(list(who.drives), ["/storage/drives/ugy-2026-17/", "/storage/drives/kozos/"])
        self.assertEqual(who.session, session["session_id"])
        claims = verifier.verify_token(issued["token"])
        self.assertEqual(claims["exp"] - claims["iat"], 300)
        self.assertNotIn(session["session"], issued["token"])
        # A token for another user header, or after expiry, is refused.
        self.assertRaises(identity.IdentityError, verifier.resolve,
                          {"VVS-Identity": issued["token"], "VVS-User": "bela"})
        self.clock.advance(300 + 61)
        self.assertRaises(identity.IdentityError, verifier.verify_token, issued["token"])

    def test_later_logins_ask_for_a_code_and_refuse_a_replayed_one(self):
        self.create()
        _session, secret = self.sign_in()
        again = self.auth.login("anna", USER_PW)
        self.assertEqual(again["step"], "totp")
        self.assertNotIn("totp_secret", again)
        same_step = totp.code_at(secret, self.clock())
        self.assertRefused("invalid_code", 401, self.auth.complete_login, again["challenge"], same_step)
        self.clock.advance(30)
        result = self.auth.complete_login(again["challenge"], totp.code_at(secret, self.clock()))
        self.assertTrue(result["session"])

    def test_unknown_user_wrong_password_and_lock_answer_alike(self):
        self.create()
        self.assertRefused("invalid_credentials", 401, self.auth.login, "nobody", USER_PW)
        self.assertRefused("invalid_credentials", 401, self.auth.login, "anna", "wrong-password-1")
        self.assertRefused("invalid_credentials", 401, self.auth.login, "../etc", USER_PW)
        self.assertRefused("invalid_credentials", 401, self.auth.login, None, None)
        events = self.auth.list_events()
        unknown = [e for e in events if e["detail"] == "unknown_user"]
        self.assertEqual(len(unknown), 3)
        self.assertTrue(all(e["username"] == "" for e in unknown))

    def test_lockout_after_repeated_failures(self):
        self.create()
        for _ in range(4):
            self.assertRefused("invalid_credentials", 401, self.auth.login, "anna", "wrong-password-1")
        self.assertEqual(self.auth.login("anna", USER_PW)["step"], "enroll")
        for _ in range(5):
            self.assertRefused("invalid_credentials", 401, self.auth.login, "anna", "wrong-password-1")
        self.assertRefused("invalid_credentials", 401, self.auth.login, "anna", USER_PW)
        self.assertIn("account_locked", [e["event"] for e in self.auth.list_events()])
        self.assertTrue(self.auth.list_users()[0]["locked"])
        self.clock.advance(900)
        self.assertEqual(self.auth.login("anna", USER_PW)["step"], "enroll")

    def test_administrator_unlocks(self):
        self.create()
        for _ in range(5):
            self.assertRefused("invalid_credentials", 401, self.auth.login, "anna", "wrong-password-1")
        self.auth.unlock("cli", "anna")
        self.assertEqual(self.auth.login("anna", USER_PW)["step"], "enroll")

    def test_wrong_codes_lock_the_account(self):
        self.create()
        first = self.auth.login("anna", USER_PW)
        for _ in range(4):
            self.assertRefused("invalid_code", 401, self.auth.complete_login, first["challenge"], "000000")
        self.assertRefused("account_locked", 423, self.auth.complete_login, first["challenge"], "000000")
        code = totp.code_at(first["totp_secret"], self.clock())
        self.assertRefused("challenge_expired", 401, self.auth.complete_login, first["challenge"], code)

    def test_a_challenge_expires_and_is_single_use(self):
        self.create()
        first = self.auth.login("anna", USER_PW)
        self.clock.advance(301)
        code = totp.code_at(first["totp_secret"], self.clock())
        self.assertRefused("challenge_expired", 401, self.auth.complete_login, first["challenge"], code)
        second = self.auth.login("anna", USER_PW)
        code = totp.code_at(second["totp_secret"], self.clock())
        self.auth.complete_login(second["challenge"], code)
        self.assertRefused("challenge_expired", 401, self.auth.complete_login, second["challenge"], code)

    def test_a_new_password_step_replaces_the_open_challenge(self):
        self.create()
        first = self.auth.login("anna", USER_PW)
        second = self.auth.login("anna", USER_PW)
        code = totp.code_at(first["totp_secret"], self.clock())
        self.assertRefused("challenge_expired", 401, self.auth.complete_login, first["challenge"], code)
        code = totp.code_at(second["totp_secret"], self.clock())
        self.assertTrue(self.auth.complete_login(second["challenge"], code)["session"])

    def test_suspended_user_cannot_log_in(self):
        self.create()
        self.auth.update_user("cli", "anna", status="suspended")
        self.assertRefused("invalid_credentials", 401, self.auth.login, "anna", USER_PW)
        self.auth.update_user("cli", "anna", status="active")
        self.assertEqual(self.auth.login("anna", USER_PW)["step"], "enroll")

    def test_client_throttle(self):
        self.auth = self.make(client_max_failures=5, client_window_seconds=900)
        self.create()
        for name in ("u1", "u2", "u3", "u4", "u5"):
            self.assertRefused("invalid_credentials", 401, self.auth.login, name, "Spray-password-1", "10.0.0.66")
        self.assertRefused("too_many_attempts", 429, self.auth.login, "anna", USER_PW, "10.0.0.66")
        self.assertEqual(self.auth.login("anna", USER_PW, "10.0.0.5")["step"], "enroll")
        self.clock.advance(901)
        self.assertEqual(self.auth.login("anna", USER_PW, "10.0.0.66")["step"], "enroll")

    def test_outdated_hash_is_upgraded_at_login(self):
        self.create()
        passwords.N = 2 ** 11
        self.auth.login("anna", USER_PW)
        stored = self.auth.store.get_user("anna")["password_hash"]
        self.assertTrue(stored.startswith("scrypt$2048$"))

    def test_unusable_passwords_cost_a_full_hash_for_existing_users_too(self):
        self.create()
        passwords.burn("warm-up")
        derived = []
        original = passwords._derive

        def counting(*args):
            derived.append(1)
            return original(*args)
        passwords._derive = counting
        self.addCleanup(setattr, passwords, "_derive", original)
        for password in ("A" * 2000, ["x"], None, 12345):
            for name in ("anna", "nobody"):
                derived.clear()
                self.assertRefused("invalid_credentials", 401, self.auth.login, name, password)
                self.assertEqual(len(derived), 1, (name, password))

    def test_non_ascii_digits_are_a_wrong_code(self):
        self.create()
        first = self.auth.login("anna", USER_PW)
        self.assertRefused("invalid_code", 401, self.auth.complete_login, first["challenge"],
                           "\u0661\u0662\u0663\u0664\u0665\u0666")
        self.assertEqual(self.auth.store.get_user("anna")["failed_count"], 1)


class SessionTest(AuthTestCase):
    def test_idle_and_absolute_expiry(self):
        self.auth = self.make(session_idle_seconds=600, session_max_seconds=1800)
        self.create()
        session, _ = self.sign_in()
        for _ in range(3):
            self.clock.advance(500)
            self.auth.issue_token(session["session"])
        self.clock.advance(400)
        self.assertRefused("session_invalid", 401, self.auth.issue_token, session["session"])
        self.clock.advance(30)
        session, _ = self.sign_in()
        self.clock.advance(601)
        self.assertRefused("session_invalid", 401, self.auth.me, session["session"])

    def test_a_token_never_outlives_its_session(self):
        self.auth = self.make(session_max_seconds=600)
        self.create()
        session, _ = self.sign_in()
        self.clock.advance(500)
        issued = self.auth.issue_token(session["session"])
        self.assertEqual(issued["expires_at"], int(session["expires_at"]))

    def test_logout(self):
        self.create()
        session, _ = self.sign_in()
        self.auth.logout(session["session"])
        self.assertRefused("session_invalid", 401, self.auth.issue_token, session["session"])
        self.auth.logout("not-a-session")
        self.auth.logout(None)

    def test_garbage_sessions(self):
        for value in (None, "", 42, "x" * 100):
            self.assertRefused("session_invalid", 401, self.auth.issue_token, value)

    def test_temporary_password_must_be_changed_first(self):
        self.create(temporary=True)
        session, _ = self.sign_in()
        self.assertTrue(session["password_change_required"])
        self.assertRefused("password_change_required", 403, self.auth.issue_token, session["session"])
        self.assertTrue(self.auth.me(session["session"])["password_change_required"])
        self.clock.advance(30)
        other, _ = self.sign_in()
        self.assertRefused("wrong_password", 403, self.auth.change_password, session["session"],
                           "wrong-password-1", "Uj-jelszo-2026-tavasz")
        self.assertRefused("password_too_short", 400, self.auth.change_password, session["session"],
                           USER_PW, "short")
        self.assertRefused("password_unchanged", 400, self.auth.change_password, session["session"],
                           USER_PW, USER_PW)
        self.auth.change_password(session["session"], USER_PW, "Uj-jelszo-2026-tavasz")
        self.assertTrue(self.auth.issue_token(session["session"])["token"])
        self.assertRefused("session_invalid", 401, self.auth.me, other["session"])
        self.assertRefused("invalid_credentials", 401, self.auth.login, "anna", USER_PW)
        self.assertEqual(self.auth.login("anna", "Uj-jelszo-2026-tavasz")["step"], "totp")


class AdminTest(AuthTestCase):
    def setUp(self):
        super().setUp()
        self.auth.create_user("cli", "admin", ADMIN_PW, roles=["admin"], temporary=False)

    def test_admin_actor_needs_the_admin_role(self):
        self.create()
        admin, _ = self.sign_in("admin", ADMIN_PW)
        user, _ = self.sign_in()
        self.assertEqual(self.auth.admin_actor(admin["session"]), "admin")
        self.assertRefused("forbidden", 403, self.auth.admin_actor, user["session"])

    def test_reset_totp_ends_sessions_and_enrols_again(self):
        self.create()
        session, old_secret = self.sign_in()
        self.auth.reset_totp("admin", "anna")
        self.assertRefused("session_invalid", 401, self.auth.issue_token, session["session"])
        first = self.auth.login("anna", USER_PW)
        self.assertEqual(first["step"], "enroll")
        self.assertNotEqual(first["totp_secret"], old_secret)
        self.assertIn("totp_reset", [e["event"] for e in self.auth.list_events(username="anna")])

    def test_reset_during_a_login_voids_the_challenge(self):
        self.create()
        self.sign_in()
        self.clock.advance(30)
        pending = self.auth.login("anna", USER_PW)
        self.auth.reset_totp("admin", "anna")
        self.assertRefused("challenge_expired", 401, self.auth.complete_login, pending["challenge"],
                           totp.code_at(self.secrets["anna"], self.clock()))

    def test_suspend_and_password_reset_end_sessions(self):
        self.create()
        session, _ = self.sign_in()
        self.auth.update_user("admin", "anna", status="suspended")
        self.assertRefused("session_invalid", 401, self.auth.me, session["session"])
        self.auth.update_user("admin", "anna", status="active")
        self.clock.advance(30)
        session, _ = self.sign_in()
        self.auth.reset_password("admin", "anna", "Ideiglenes-2026-x")
        self.assertRefused("session_invalid", 401, self.auth.me, session["session"])
        self.clock.advance(30)
        session, _ = self.sign_in(password="Ideiglenes-2026-x")
        self.assertTrue(session["password_change_required"])

    def test_access_changes_apply_to_the_next_token(self):
        self.create(drives=["/storage/drives/a"])
        session, _ = self.sign_in()
        self.auth.update_user("admin", "anna", drives=["/storage/drives/b/"], roles=["Viewer"])
        user = self.auth.issue_token(session["session"])["user"]
        self.assertEqual((user["drives"], user["roles"]), (["/storage/drives/b/"], ["viewer"]))

    def test_the_last_administrator_stays(self):
        self.assertRefused("last_admin", 409, self.auth.delete_user, "cli", "admin")
        self.assertRefused("last_admin", 409, self.auth.update_user, "cli", "admin", status="suspended")
        self.assertRefused("last_admin", 409, self.auth.update_user, "cli", "admin", roles=["viewer"])
        self.auth.create_user("cli", "admin2", ADMIN_PW, roles=["admin"])
        self.auth.update_user("cli", "admin", roles=["viewer"])
        self.assertRefused("last_admin", 409, self.auth.delete_user, "cli", "admin2")
        self.auth.delete_user("cli", "admin")
        self.assertRefused("user_not_found", 404, self.auth.unlock, "cli", "admin")

    def test_parallel_removals_keep_an_administrator(self):
        self.auth.create_user("cli", "admin2", ADMIN_PW, roles=["admin"])
        for round_number in range(10):
            barrier = threading.Barrier(2)
            outcomes = []

            def suspend(name):
                barrier.wait()
                try:
                    self.auth.update_user("cli", name, status="suspended")
                    outcomes.append("ok")
                except auth.AuthError as e:
                    outcomes.append(e.code)
            threads = [threading.Thread(target=suspend, args=(name,)) for name in ("admin", "admin2")]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            self.assertEqual(sorted(outcomes), ["last_admin", "ok"], round_number)
            active = [u for u in self.auth.list_users() if u["status"] == "active" and "admin" in u["roles"]]
            self.assertEqual(len(active), 1)
            for name in ("admin", "admin2"):
                self.auth.update_user("cli", name, status="active")

    def test_input_validation(self):
        for bad in ("", "Ánna", "a/b", "../x", "x" * 65, None):
            self.assertRefused("invalid_username", 400, self.auth.create_user, "cli", bad, USER_PW)
        self.assertRefused("user_exists", 409, self.auth.create_user, "cli", "ADMIN", ADMIN_PW)
        self.assertRefused("password_too_short", 400, self.auth.create_user, "cli", "bela", "short")
        for drives in (["relative/path"], ["/storage/drives/../etc"], ["/storage\\drives"], "/x", [7]):
            self.assertRefused("invalid_drives", 400, self.auth.create_user, "cli", "bela", USER_PW,
                               drives=drives)
        self.assertRefused("invalid_roles", 400, self.auth.create_user, "cli", "bela", USER_PW,
                           roles=["bad role"])
        self.assertRefused("invalid_status", 400, self.auth.update_user, "cli", "admin", status="gone")
        created = self.auth.create_user("cli", "Bela", USER_PW, roles=["Admin", "admin"],
                                        drives=["/storage//drives/x", "/storage/drives/x/"])
        self.assertEqual((created["username"], created["roles"], created["drives"]),
                         ("bela", ["admin"], ["/storage/drives/x/"]))
        self.assertTrue(created["must_change_password"])

    def test_admin_views_and_events_hold_no_secrets(self):
        self.create(temporary=True)
        session, secret = self.sign_in()
        self.auth.change_password(session["session"], USER_PW, "Uj-jelszo-2026-tavasz")
        token = self.auth.issue_token(session["session"])["token"]
        self.auth.logout(session["session"])
        dumped = json.dumps([self.auth.list_users(), self.auth.list_events(limit=1000)])
        for secret_value in (USER_PW, "Uj-jelszo-2026-tavasz", secret, session["session"], token, "scrypt$"):
            self.assertNotIn(secret_value, dumped)
        events = [e["event"] for e in self.auth.list_events(limit=1000)]
        for expected in ("user_created", "login_password_ok", "totp_enrolled", "login_ok",
                         "password_changed", "logout"):
            self.assertIn(expected, events)


if __name__ == "__main__":
    unittest.main()
