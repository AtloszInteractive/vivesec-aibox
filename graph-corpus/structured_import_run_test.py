"""The import run's identity: in standalone mode (E27) the adapter must accept
the token it signs."""
import base64
import os
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "adapter"))
import identity  # noqa: E402
import structured_import_run as run  # noqa: E402

KEY = "cd" * 32
DRIVE = "/storage/drives/bv"
STANDALONE = {"ADAPTER_INTEGRATION": "standalone",
              "ADAPTER_IDENTITY_HS256_SECRET_FILE": "/run/aibox-auth/identity-hs256.key",
              "ADAPTER_IDENTITY_ISSUER": "vivesec-aibox-auth",
              "ADAPTER_IDENTITY_AUDIENCE": "vivesec-aibox-adapter"}


class HeadersTest(unittest.TestCase):
    def test_vivesecbox_mode_sends_plain_headers(self):
        with mock.patch.object(run, "_docker") as docker:
            headers = run.headers_for("admin", DRIVE, {"ADAPTER_INTEGRATION": "vivesecbox"})
        docker.assert_not_called()
        self.assertEqual(base64.urlsafe_b64decode(headers["VVS-Drive"]).decode(), DRIVE + "/")
        self.assertNotIn("VVS-Identity", headers)

    def test_standalone_token_passes_the_adapter_verifier(self):
        with mock.patch.object(run, "_docker", return_value=KEY + "\n") as docker:
            headers = run.headers_for("admin", DRIVE, STANDALONE)
        self.assertEqual(docker.call_args[0][-1], STANDALONE["ADAPTER_IDENTITY_HS256_SECRET_FILE"])
        with tempfile.TemporaryDirectory() as temp:
            key_file = os.path.join(temp, "key")
            with open(key_file, "w") as handle:
                handle.write(KEY + "\n")
            verifier = identity.Verifier(mode=identity.MODE_REQUIRE, algorithms=("HS256",),
                                         hs256_secret_file=key_file,
                                         issuer=STANDALONE["ADAPTER_IDENTITY_ISSUER"],
                                         audience=STANDALONE["ADAPTER_IDENTITY_AUDIENCE"])
            caller = verifier.resolve(headers, DRIVE + "/")
            claims = verifier.verify_token(headers["VVS-Identity"])
        self.assertEqual(caller.user, "admin")
        self.assertEqual(list(caller.drives), [DRIVE + "/"])
        self.assertLessEqual(claims["exp"] - claims["iat"], 120)


if __name__ == "__main__":
    unittest.main()
