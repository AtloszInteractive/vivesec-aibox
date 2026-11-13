import unittest

import passwords


class FastHashing(unittest.TestCase):
    """Lower scrypt cost for speed; the stored parameters keep hashes valid."""

    def setUp(self):
        self._saved = passwords.N
        passwords.N = 2 ** 10
        self.addCleanup(setattr, passwords, "N", self._saved)


class PasswordTest(FastHashing):
    def test_round_trip_and_unique_salt(self):
        first = passwords.hash_password("correct horse battery")
        self.assertTrue(first.startswith("scrypt$1024$8$1$"))
        self.assertTrue(passwords.verify_password("correct horse battery", first))
        self.assertFalse(passwords.verify_password("correct horse batterY", first))
        self.assertNotEqual(first, passwords.hash_password("correct horse battery"))

    def test_malformed_hashes_never_match(self):
        good = passwords.hash_password("correct horse battery")
        scheme, n, r, p, salt, digest = good.split("$")
        for bad in (None, "", "plain", good + "$x", "bcrypt$%s$%s$%s$%s$%s" % (n, r, p, salt, digest),
                    "scrypt$1000$8$1$%s$%s" % (salt, digest), "scrypt$%s$8$1$%s$!!" % (n, salt),
                    "scrypt$%d$8$1$%s$%s" % (2 ** 24, salt, digest)):
            self.assertFalse(passwords.verify_password("correct horse battery", bad), bad)

    def test_oversized_password_is_refused(self):
        stored = passwords.hash_password("x" * 10)
        self.assertFalse(passwords.verify_password("x" * (passwords.MAX_PASSWORD_BYTES + 1), stored))
        passwords.burn("x" * (passwords.MAX_PASSWORD_BYTES + 1))
        passwords.burn(None)

    def test_needs_rehash_follows_the_current_cost(self):
        stored = passwords.hash_password("correct horse battery")
        self.assertFalse(passwords.needs_rehash(stored))
        passwords.N = 2 ** 11
        self.assertTrue(passwords.needs_rehash(stored))
        self.assertTrue(passwords.verify_password("correct horse battery", stored))
        self.assertTrue(passwords.needs_rehash("garbage"))

    def test_policy(self):
        self.assertIsNone(passwords.policy_problem("Tavaszi-szel-2026", "anna", 12))
        self.assertEqual(passwords.policy_problem("short", "anna", 12), "password_too_short")
        self.assertEqual(passwords.policy_problem("anna.kiss.2026", "Anna.Kiss.2026", 12),
                         "password_same_as_username")
        self.assertEqual(passwords.policy_problem("aaaaaaaaaaaaaab", "anna", 12), "password_too_simple")
        self.assertEqual(passwords.policy_problem("line\nbreak-password", "anna", 12),
                         "password_invalid_characters")
        self.assertEqual(passwords.policy_problem("é" * 600, "anna", 12), "password_too_long")
        self.assertEqual(passwords.policy_problem(None, "anna", 12), "password_too_short")


if __name__ == "__main__":
    unittest.main()
