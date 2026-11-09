"""Tests for the verification identity of 172-release-box.py: in standalone
mode (E27) the adapter must accept the token the release signs.

  Push-Location scripts/jetson; python -m unittest release_verify_test; Pop-Location
"""
import base64
import importlib.util
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


if __name__ == "__main__":
    unittest.main()
