"""Tests for the verification identity of 172-release-box.py: in standalone
mode (E27) the adapter must accept the token the release signs.

  Push-Location scripts/jetson; python -m unittest release_verify_test; Pop-Location
"""
import base64
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("release_box", os.path.join(HERE, "172-release-box.py"))
release_box = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(release_box)
sys.path.insert(0, os.path.join(HERE, "..", "..", "adapter"))
import identity  # noqa: E402
import purpose  # noqa: E402

KEY = "ab" * 32
DRIVE = "/storage/drives/engineering/"
STANDALONE = {"ADAPTER_INTEGRATION": "standalone",
              "ADAPTER_IDENTITY_HS256_SECRET_FILE": "/run/aibox-auth/identity-hs256.key",
              "ADAPTER_IDENTITY_ISSUER": "vivesec-aibox-auth",
              "ADAPTER_IDENTITY_AUDIENCE": "vivesec-aibox-adapter"}


class VerificationHeadersTest(unittest.TestCase):
    def test_vivesecbox_mode_keeps_the_plain_headers(self):
        with mock.patch.object(release_box, "command") as command:
            headers = release_box.verification_headers({}, "release-1", DRIVE)
        command.assert_not_called()
        self.assertEqual(headers["VVS-User"], "release-1")
        self.assertEqual(base64.urlsafe_b64decode(headers["VVS-Drive"]).decode(), DRIVE)
        self.assertNotIn("VVS-Identity", headers)

    def test_standalone_token_passes_the_adapter_verifier(self):
        with mock.patch.object(release_box, "command", return_value=KEY + "\n") as command:
            headers = release_box.verification_headers(STANDALONE, "release-1", DRIVE)
        self.assertEqual(command.call_args[0][-1], STANDALONE["ADAPTER_IDENTITY_HS256_SECRET_FILE"])
        with tempfile.TemporaryDirectory() as temp:
            key_file = os.path.join(temp, "key")
            with open(key_file, "w") as handle:
                handle.write(KEY + "\n")
            verifier = identity.Verifier(mode=identity.MODE_REQUIRE, algorithms=("HS256",),
                                         hs256_secret_file=key_file,
                                         issuer=STANDALONE["ADAPTER_IDENTITY_ISSUER"],
                                         audience=STANDALONE["ADAPTER_IDENTITY_AUDIENCE"])
            caller = verifier.resolve(headers, DRIVE)
            claims = verifier.verify_token(headers["VVS-Identity"])
        self.assertEqual(caller.user, "release-1")
        self.assertEqual(list(caller.drives), [DRIVE])
        self.assertLessEqual(claims["exp"] - claims["iat"], 120)


class VerificationPurposeTest(unittest.TestCase):
    BV = {"ADAPTER_PURPOSE": "required", "ADAPTER_PURPOSE_FIELDS": "case_ref",
          "ADAPTER_CASE_REF_PATTERN": r"(?:19|20)\d{2}/\d{1,7}(?:/\d{1,5})?"}

    def purpose_for(self, env, **environ):
        config = purpose.settings(env)
        with tempfile.TemporaryDirectory() as stage:
            with open(os.path.join(stage, "status.json"), "w") as handle:
                json.dump({"app": {"purpose": purpose.public(config)}}, handle)
            with mock.patch.dict(os.environ, environ):
                given = release_box.verification_purpose(stage, {"stamp": "20261113-231015"},
                                                         now=1794608000)
        return given, config

    def test_case_reference_only_box_accepts_the_verification(self):
        given, config = self.purpose_for(self.BV)
        self.assertEqual(["case_ref"], list(given))
        self.assertEqual(given, purpose.parse(given, config))

    def test_explicit_case_reference_wins(self):
        given, _ = self.purpose_for(self.BV, RELEASE_VERIFY_CASE_REF="2026/1/1")
        self.assertEqual({"case_ref": "2026/1/1"}, given)

    def test_any_field_keeps_the_legal_basis(self):
        given, config = self.purpose_for({"ADAPTER_PURPOSE": "required"})
        self.assertEqual(["legal_basis"], list(given))
        purpose.parse(given, config)

    def test_both_fields_are_given(self):
        given, config = self.purpose_for(dict(self.BV, ADAPTER_PURPOSE_FIELDS="both"))
        self.assertEqual({"case_ref", "legal_basis"}, set(given))
        purpose.parse(given, config)

    def test_unmatchable_pattern_names_the_override(self):
        with self.assertRaisesRegex(AssertionError, "RELEASE_VERIFY_CASE_REF"):
            self.purpose_for(dict(self.BV, ADAPTER_CASE_REF_PATTERN=r"BV-\d{4}"))


if __name__ == "__main__":
    unittest.main()
