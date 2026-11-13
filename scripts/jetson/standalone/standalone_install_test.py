"""standalone_install.py: phase order, resume state, preflight."""
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from unittest import mock

import kitlib
import standalone_install as si


class PhaseTest(unittest.TestCase):
    def test_order(self):
        # the volume must exist before anything writes data; the audit comes last
        self.assertEqual(si.PHASES, ("preflight", "time", "vault", "tls", "clamav", "auth", "backup", "audit"))
        for phase in si.PHASES:
            self.assertTrue(callable(getattr(si, "phase_" + phase)))

    def test_resume_state(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "state")
            with open(path, "w") as f:
                f.write("2026-11-12T10:00:00 preflight\n2026-11-12T10:01:00 time\nbroken line here\n")
            with mock.patch.object(si, "STATE_FILE", path):
                self.assertEqual(si.done_phases(), {"preflight", "time"})

    def test_list_needs_no_root(self):
        out = StringIO()
        with redirect_stdout(out):
            self.assertEqual(si.main(["x", "--list"]), 0)
        self.assertEqual(out.getvalue().split(), list(si.PHASES))


class PreflightTest(unittest.TestCase):
    def run_preflight(self, settings, checks=()):
        out = StringIO()
        with mock.patch.object(si.shutil, "which", lambda t: "/usr/bin/" + t), \
                mock.patch.object(si.vault, "host_checks", lambda cfg: list(checks)), redirect_stdout(out):
            try:
                si.phase_preflight(settings)
                return None, out.getvalue()
            except kitlib.KitError as e:
                return str(e), out.getvalue()

    def test_missing_settings_fail(self):
        err, out = self.run_preflight({"AIBOX_VAULT_FILE": "/data/v.img", "AIBOX_VAULT_MOUNT": "/data/vault"})
        self.assertIn("3 preflight problems", err)
        self.assertIn("AIBOX_NTP_SERVERS", out)

    def test_plaintext_swap_fails_guard_lines_are_left_to_the_vault_phase(self):
        good = {"AIBOX_VAULT_FILE": "/data/v.img", "AIBOX_VAULT_MOUNT": "/data/vault", "AIBOX_TLS_DNS": "a",
                "AIBOX_SOURCE_DIR": "/x", "AIBOX_NTP_SERVERS": "none"}
        self.assertIsNone(self.run_preflight(good, [("FAIL", "closed volume: mount point x"),
                                                    ("FAIL", "link /data/a: missing")])[0])
        err, _ = self.run_preflight(good, [("FAIL", "plaintext swap: /swapfile")])
        self.assertIn("1 preflight problems", err)

    def test_no_volume_configured(self):
        self.assertIn("no volume configured", self.run_preflight({})[0])


if __name__ == "__main__":
    unittest.main()
