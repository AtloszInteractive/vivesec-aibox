"""Signed identity assertions (E02): HS256 / RS256 verification, claim checks,
header binding and the fail-closed mode handling."""
import hashlib
import json
import os
import random
import tempfile
import unittest

import identity

NOW = 1_800_000_000
SECRET = b"s" * 48


def _is_probable_prime(n, rng, rounds=24):
    if n < 2:
        return False
    for p in (2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37):
        if n % p == 0:
            return n == p
    d, r = n - 1, 0
    while d % 2 == 0:
        d //= 2
        r += 1
    for _ in range(rounds):
        x = pow(rng.randrange(2, n - 2), d, n)
        if x in (1, n - 1):
            continue
        for _ in range(r - 1):
            x = pow(x, 2, n)
            if x == n - 1:
                break
        else:
            return False
    return True


def _prime(bits, rng):
    while True:
        candidate = rng.getrandbits(bits) | (1 << (bits - 1)) | (1 << (bits - 2)) | 1
        if _is_probable_prime(candidate, rng):
            return candidate


def make_rsa_key(bits=2048, seed=7):
    """Test-only RSA key pair, deterministic so the suite stays reproducible."""
    rng = random.Random(seed)
    e = 65537
    while True:
        p, q = _prime(bits // 2, rng), _prime(bits // 2, rng)
        phi = (p - 1) * (q - 1)
        if p != q and phi % e and (p * q).bit_length() == bits:
            return p * q, e, pow(e, -1, phi)


def _int_b64(value):
    return identity.b64url_encode(value.to_bytes((value.bit_length() + 7) // 8, "big"))


def sign_rs256(claims, key, kid="k1", header=None):
    n, _e, d = key
    head = dict({"alg": "RS256", "typ": "JWT"}, **({"kid": kid} if kid else {}))
    head.update(header or {})
    signing_input = (identity.b64url_encode(json.dumps(head).encode())
                     + "." + identity.b64url_encode(json.dumps(claims).encode()))
    k = (n.bit_length() + 7) // 8
    t = identity._SHA256_DIGEST_INFO + hashlib.sha256(signing_input.encode()).digest()
    em = b"\x00\x01" + b"\xff" * (k - len(t) - 3) + b"\x00" + t
    signature = pow(int.from_bytes(em, "big"), d, n).to_bytes(k, "big")
    return signing_input + "." + identity.b64url_encode(signature)


RSA_KEY = make_rsa_key()


def claims(**overrides):
    base = {"sub": "lars.nygaard", "upn": "lars@example.com", "iat": NOW - 10,
            "exp": NOW + 300, "iss": "vivesecbox", "aud": "aibox",
            "groups": ["g-finance"], "roles": ["Analyst"]}
    base.update(overrides)
    return {k: v for k, v in base.items() if v is not None}


class Headers(dict):
    def get(self, key, default=None):
        for k, v in self.items():
            if k.lower() == key.lower():
                return v
        return default


class VerifierTestBase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.secret_file = os.path.join(self.temp.name, "hs256.secret")
        with open(self.secret_file, "wb") as handle:
            handle.write(SECRET + b"\n")
        self.jwks_file = os.path.join(self.temp.name, "jwks.json")
        self.write_jwks([("k1", RSA_KEY)])

    def write_jwks(self, keys):
        with open(self.jwks_file, "w", encoding="utf-8") as handle:
            json.dump({"keys": [{"kty": "RSA", "kid": kid, "use": "sig", "alg": "RS256",
                                 "n": _int_b64(key[0]), "e": _int_b64(key[1])}
                                for kid, key in keys]}, handle)
        # Make the mtime move even on coarse-grained file systems.
        stamp = os.path.getmtime(self.jwks_file) + len(keys) + random.random()
        os.utime(self.jwks_file, (stamp, stamp))

    def verifier(self, **overrides):
        options = dict(mode=identity.MODE_VERIFY, hs256_secret_file=self.secret_file,
                       jwks_file=self.jwks_file, issuer="vivesecbox", audience="aibox",
                       clock=lambda: NOW)
        options.update(overrides)
        return identity.Verifier(**options)

    def assertRejected(self, verifier, headers, drive=None, status=401):
        with self.assertRaises(identity.IdentityError) as caught:
            verifier.resolve(Headers(headers), drive)
        self.assertEqual(caught.exception.status, status)
        return caught.exception


class ConfigurationTest(unittest.TestCase):
    def test_mode_defaults_to_off_and_unknown_fails_closed(self):
        warnings = []
        self.assertEqual(identity.parse_mode(None), identity.MODE_OFF)
        self.assertEqual(identity.parse_mode("  "), identity.MODE_OFF)
        self.assertEqual(identity.parse_mode("Verify"), identity.MODE_VERIFY)
        self.assertEqual(identity.parse_mode("strict", warnings.append), identity.MODE_REQUIRE)
        self.assertTrue(warnings)

    def test_algorithm_allowlist_drops_unsupported_names(self):
        self.assertEqual(identity.parse_algorithms(""), ("HS256", "RS256"))
        self.assertEqual(identity.parse_algorithms("rs256, none, ES256"), ("RS256",))
        self.assertEqual(identity.parse_algorithms("none"), ())

    def test_from_env(self):
        verifier = identity.Verifier.from_env({
            "ADAPTER_IDENTITY_MODE": "require", "ADAPTER_IDENTITY_ALGORITHMS": "RS256",
            "ADAPTER_IDENTITY_AUDIENCE": "aibox", "ADAPTER_IDENTITY_MAX_TTL_SECONDS": "x"},
            warn=lambda _m: None)
        self.assertEqual(verifier.mode, identity.MODE_REQUIRE)
        self.assertEqual(verifier.algorithms, ("RS256",))
        self.assertEqual(verifier.audience, "aibox")
        self.assertEqual(verifier.max_ttl, 3600)
        self.assertEqual(verifier.header, "VVS-Identity")


class ModeTest(VerifierTestBase):
    def test_off_uses_the_header_and_ignores_any_token(self):
        verifier = self.verifier(mode=identity.MODE_OFF)
        caller = verifier.resolve(Headers({"VVS-User": "anna", "VVS-Identity": "garbage"}))
        self.assertEqual((caller.user, caller.source), ("anna", identity.SOURCE_HEADER))

    def test_verify_without_token_falls_back_to_the_header(self):
        caller = self.verifier().resolve(Headers({"VVS-User": "anna", "VVS-Session": "s1"}))
        self.assertEqual((caller.user, caller.source, caller.session),
                         ("anna", identity.SOURCE_HEADER, "s1"))

    def test_verify_rejects_an_invalid_token_instead_of_falling_back(self):
        self.assertRejected(self.verifier(), {"VVS-User": "anna", "VVS-Identity": "a.b.c"})

    def test_require_rejects_a_missing_token(self):
        self.assertRejected(self.verifier(mode=identity.MODE_REQUIRE), {"VVS-User": "anna"})


class Hs256Test(VerifierTestBase):
    def test_valid_token_yields_the_asserted_identity(self):
        token = identity.sign_hs256(claims(), SECRET)
        caller = self.verifier().resolve(Headers({"VVS-Identity": token}))
        self.assertEqual(caller.user, "lars.nygaard")
        self.assertEqual(caller.source, identity.SOURCE_TOKEN)
        self.assertEqual(caller.upn, "lars@example.com")
        self.assertEqual(caller.groups, ("g-finance",))
        self.assertEqual(caller.roles, ("Analyst",))
        self.assertEqual(caller.keys(), ("lars.nygaard", "lars@example.com"))
        self.assertIsNone(caller.drives)

    def test_bearer_prefix_and_custom_header(self):
        token = identity.sign_hs256(claims(), SECRET)
        verifier = self.verifier(header="Authorization")
        self.assertEqual(verifier.resolve(Headers({"Authorization": "Bearer " + token})).user,
                         "lars.nygaard")

    def test_wrong_secret_is_rejected(self):
        token = identity.sign_hs256(claims(), b"x" * 48)
        self.assertRejected(self.verifier(), {"VVS-Identity": token})

    def test_tampered_claims_are_rejected(self):
        token = identity.sign_hs256(claims(), SECRET)
        head, _body, signature = token.split(".")
        forged = identity.b64url_encode(json.dumps(claims(sub="admin")).encode())
        self.assertRejected(self.verifier(), {"VVS-Identity": "%s.%s.%s" % (head, forged, signature)})

    def test_short_secret_disables_hs256(self):
        with open(self.secret_file, "wb") as handle:
            handle.write(b"short")
        verifier = self.verifier()
        self.assertNotIn("HS256", verifier.key_status())
        self.assertRejected(verifier, {"VVS-Identity": identity.sign_hs256(claims(), b"short")})

    def test_algorithm_outside_the_allowlist_is_rejected(self):
        token = identity.sign_hs256(claims(), SECRET)
        self.assertRejected(self.verifier(algorithms=("RS256",)), {"VVS-Identity": token})

    def test_alg_none_is_rejected(self):
        head = identity.b64url_encode(b'{"alg":"none","typ":"JWT"}')
        body = identity.b64url_encode(json.dumps(claims()).encode())
        self.assertRejected(self.verifier(), {"VVS-Identity": "%s.%s." % (head, body)})

    def test_critical_header_is_rejected(self):
        token = identity.sign_hs256(claims(), SECRET, header={"crit": ["exp"]})
        self.assertRejected(self.verifier(), {"VVS-Identity": token})


class ClaimTest(VerifierTestBase):
    def check(self, status=401, **overrides):
        token = identity.sign_hs256(claims(**overrides), SECRET)
        return self.assertRejected(self.verifier(), {"VVS-Identity": token}, status=status)

    def test_expiry_is_mandatory_and_enforced(self):
        self.check(exp=None)
        self.check(exp=NOW - 61)
        self.check(exp="soon")

    def test_leeway_tolerates_small_clock_skew(self):
        token = identity.sign_hs256(claims(exp=NOW - 30), SECRET)
        self.assertEqual(self.verifier().resolve(Headers({"VVS-Identity": token})).user,
                         "lars.nygaard")

    def test_not_before_and_future_issue(self):
        self.check(nbf=NOW + 120)
        self.check(iat=NOW + 120)

    def test_long_lived_tokens_are_refused(self):
        self.check(iat=NOW, exp=NOW + 7200)
        self.check(iat=None, exp=NOW + 7200)

    def test_issuer_audience_and_scope(self):
        self.check(iss="someone-else")
        self.check(aud=["other"])
        token = identity.sign_hs256(claims(aud=["other", "aibox"], scope="read aibox.query"),
                                    SECRET)
        verifier = self.verifier(scope="aibox.query")
        self.assertEqual(verifier.resolve(Headers({"VVS-Identity": token})).user, "lars.nygaard")
        token = identity.sign_hs256(claims(scp=["read"]), SECRET)
        self.assertRejected(verifier, {"VVS-Identity": token})

    def test_missing_user_claim(self):
        self.check(sub=None)
        self.check(sub="  ")

    def test_header_binding(self):
        token = identity.sign_hs256(claims(drive="/storage/drives/finance/", sid="s1"), SECRET)
        verifier = self.verifier()
        self.assertRejected(verifier, {"VVS-Identity": token, "VVS-User": "someone"})
        self.assertRejected(verifier, {"VVS-Identity": token, "VVS-Session": "s2"})
        self.assertRejected(verifier, {"VVS-Identity": token}, drive="/storage/drives/hr/",
                            status=403)
        caller = verifier.resolve(Headers({"VVS-Identity": token, "VVS-User": "lars.nygaard",
                                           "VVS-Session": "s1"}),
                                  drive="/storage/drives/finance")
        self.assertEqual((caller.user, caller.session), ("lars.nygaard", "s1"))
        # A drive-less endpoint (voice) does not need the drive binding.
        self.assertEqual(verifier.resolve(Headers({"VVS-Identity": token})).user, "lars.nygaard")

    def test_drives_claim(self):
        token = identity.sign_hs256(claims(drives=["/storage/drives/hr/"]), SECRET)
        self.assertEqual(self.verifier().resolve(Headers({"VVS-Identity": token})).drives,
                         ("/storage/drives/hr/",))
        self.check(drives="/storage/drives/hr/")

    def test_malformed_tokens(self):
        verifier = self.verifier()
        for token in ("x", "a.b", "a.b.c.d", "!!.??.##", "e30.e30.", "W10.W10.AA"):
            self.assertRejected(verifier, {"VVS-Identity": token})
        self.assertRejected(verifier, {"VVS-Identity": "a" * (identity.MAX_TOKEN_BYTES + 1)})


class Rs256Test(VerifierTestBase):
    def test_valid_token(self):
        caller = self.verifier().resolve(Headers({"VVS-Identity": sign_rs256(claims(), RSA_KEY)}))
        self.assertEqual(caller.user, "lars.nygaard")
        self.assertIn("RS256", self.verifier().key_status())

    def test_unknown_kid_and_missing_kid_with_several_keys(self):
        self.assertRejected(self.verifier(),
                            {"VVS-Identity": sign_rs256(claims(), RSA_KEY, kid="other")})
        # A single key may be used without a kid...
        self.assertEqual(self.verifier().resolve(
            Headers({"VVS-Identity": sign_rs256(claims(), RSA_KEY, kid=None)})).user,
            "lars.nygaard")
        # ...but with several keys the token must say which one.
        self.write_jwks([("k1", RSA_KEY), ("k2", make_rsa_key(seed=11))])
        self.assertRejected(self.verifier(),
                            {"VVS-Identity": sign_rs256(claims(), RSA_KEY, kid=None)})

    def test_tampered_signature(self):
        token = sign_rs256(claims(), RSA_KEY)
        head, body, signature = token.split(".")
        raw = bytearray(identity.b64url_decode(signature))
        raw[-1] ^= 1
        self.assertRejected(self.verifier(), {"VVS-Identity": "%s.%s.%s" % (
            head, body, identity.b64url_encode(bytes(raw)))})

    def test_weak_keys_are_not_loaded(self):
        self.write_jwks([("k1", make_rsa_key(bits=1024, seed=3))])
        verifier = self.verifier()
        self.assertNotIn("RS256", verifier.key_status())
        self.assertRejected(verifier, {"VVS-Identity": sign_rs256(claims(), RSA_KEY)})

    def test_key_rotation_is_picked_up(self):
        verifier = self.verifier()
        new_key = make_rsa_key(seed=13)
        self.assertRejected(verifier, {"VVS-Identity": sign_rs256(claims(), new_key, kid="k2")})
        self.write_jwks([("k2", new_key)])
        self.assertEqual(verifier.resolve(
            Headers({"VVS-Identity": sign_rs256(claims(), new_key, kid="k2")})).user,
            "lars.nygaard")

    def test_public_key_cannot_be_used_as_an_hmac_secret(self):
        # Classic algorithm confusion: an HS256 token "signed" with the RSA
        # public key must not verify against the RSA key material.
        public = json.dumps({"n": _int_b64(RSA_KEY[0]), "e": _int_b64(RSA_KEY[1])}).encode()
        verifier = self.verifier(hs256_secret_file=None)
        for secret in (public, _int_b64(RSA_KEY[0]).encode()):
            self.assertRejected(verifier, {"VVS-Identity": identity.sign_hs256(claims(), secret)})

    def test_unreadable_jwks_grants_nothing(self):
        with open(self.jwks_file, "w", encoding="utf-8") as handle:
            handle.write("{broken")
        stamp = os.path.getmtime(self.jwks_file) + 5
        os.utime(self.jwks_file, (stamp, stamp))
        self.assertRejected(self.verifier(), {"VVS-Identity": sign_rs256(claims(), RSA_KEY)})


if __name__ == "__main__":
    unittest.main()
