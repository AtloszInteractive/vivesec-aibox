import hashlib
import unittest

import totp

RFC_KEY = b"12345678901234567890"


class TotpTest(unittest.TestCase):
    def test_rfc6238_sha1_vectors(self):
        for timestamp, expected in ((59, "94287082"), (1111111109, "07081804"),
                                    (1111111111, "14050471"), (1234567890, "89005924"),
                                    (2000000000, "69279037"), (20000000000, "65353130")):
            self.assertEqual(totp.hotp(RFC_KEY, totp.step_at(timestamp), 8, hashlib.sha1), expected)

    def test_new_secret_is_base32_of_20_bytes(self):
        secret = totp.new_secret()
        self.assertEqual(len(secret), 32)
        self.assertEqual(len(totp.secret_bytes(secret)), 20)
        self.assertNotEqual(secret, totp.new_secret())

    def test_accepts_one_step_of_drift_either_side(self):
        secret, now = totp.new_secret(), 1800000000.0
        current = totp.step_at(now)
        for offset in (-1, 0, 1):
            code = totp.code_at(secret, now + offset * totp.PERIOD)
            self.assertEqual(totp.match(secret, code, now), current + offset)
        for offset in (-2, 2):
            self.assertIsNone(totp.match(secret, totp.code_at(secret, now + offset * totp.PERIOD), now))

    def test_used_steps_are_refused(self):
        secret, now = totp.new_secret(), 1800000000.0
        code = totp.code_at(secret, now)
        step = totp.match(secret, code, now)
        self.assertIsNone(totp.match(secret, code, now, last_step=step))
        earlier = totp.code_at(secret, now - totp.PERIOD)
        self.assertIsNone(totp.match(secret, earlier, now, last_step=step))
        later = totp.code_at(secret, now + totp.PERIOD)
        self.assertEqual(totp.match(secret, later, now, last_step=step), step + 1)

    def test_malformed_codes(self):
        secret, now = totp.new_secret(), 1800000000.0
        code = totp.code_at(secret, now)
        self.assertEqual(totp.match(secret, code[:3] + " " + code[3:], now), totp.step_at(now))
        for bad in (None, "", "12345", "1234567", "abcdef", 123456, code + "0",
                    "\u0661\u0662\u0663\u0664\u0665\u0666", "12345\u00b2"):
            self.assertIsNone(totp.match(secret, bad, now), bad)

    def test_provisioning_uri(self):
        uri = totp.provisioning_uri("JBSWY3DPEHPK3PXP", "anna.kiss", "ViVeSec AI Box")
        self.assertTrue(uri.startswith("otpauth://totp/ViVeSec%20AI%20Box:anna.kiss?"), uri)
        for part in ("secret=JBSWY3DPEHPK3PXP", "issuer=ViVeSec%20AI%20Box", "algorithm=SHA1",
                     "digits=6", "period=30"):
            self.assertIn(part, uri)


if __name__ == "__main__":
    unittest.main()
