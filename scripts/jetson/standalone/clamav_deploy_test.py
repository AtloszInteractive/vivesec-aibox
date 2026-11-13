"""clamav_deploy.py: settings, the container recipe and the EICAR check."""
import socket
import struct
import threading
import unittest

import clamav_deploy


class FakeClamd:
    def __init__(self, find=True):
        self.find = find
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(5)
        self.port = self.sock.getsockname()[1]
        threading.Thread(target=self.serve, daemon=True).start()

    def serve(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            with conn:
                data = b""
                while not data.endswith(b"\0\0\0\0") and not data.endswith(b"PING\0"):
                    part = conn.recv(65536)
                    if not part:
                        break
                    data += part
                if data.endswith(b"PING\0"):
                    conn.sendall(b"PONG\0")
                elif self.find and b"EICAR-STANDARD" in data:
                    conn.sendall(b"stream: Eicar-Signature FOUND\0")
                else:
                    conn.sendall(b"stream: OK\0")

    def close(self):
        self.sock.close()


class SettingsTest(unittest.TestCase):
    def test_defaults(self):
        config = clamav_deploy.settings({})
        self.assertEqual(config, {"image": "clamav/clamav-debian:1.4", "port": 3310,
                                  "max_mb": 300, "memory": "3g", "freshclam": False,
                                  "db_dir": "/var/lib/aibox/clamav"})

    def test_invalid_values_stop(self):
        for env in ({"AIBOX_CLAMAV_PORT": "0"}, {"AIBOX_CLAMAV_PORT": "x"},
                    {"AIBOX_CLAMAV_MAX_MB": "0"}, {"AIBOX_CLAMAV_MEMORY": "lots"},
                    {"AIBOX_CLAMAV_FRESHCLAM": "maybe"}, {"AIBOX_CLAMAV_DB_DIR": "clamav"},
                    {"AIBOX_CLAMAV_DB_DIR": "/var/../etc"}, {"AIBOX_CLAMAV_DB_DIR": "/clamav"}):
            with self.assertRaises(SystemExit, msg=env):
                clamav_deploy.settings(env)


class RecipeTest(unittest.TestCase):
    def test_loopback_only_limits_and_hardening(self):
        args = clamav_deploy.run_args(clamav_deploy.settings({"AIBOX_CLAMAV_MAX_MB": "300"}))
        joined = " ".join(args)
        self.assertIn("-p 127.0.0.1:3310:3310", joined)
        self.assertNotIn("--network host", joined)
        self.assertIn("--cap-drop ALL", joined)
        self.assertIn("no-new-privileges", joined)
        for setting in ("StreamMaxLength=300M", "MaxFileSize=300M", "MaxScanSize=1200M",
                        "AlertExceedsMax=yes"):
            self.assertIn("CLAMD_CONF_" + setting, joined)
        self.assertIn("CLAMAV_NO_FRESHCLAMD=true", joined)
        # uploads are written to clamd's temporary directory: memory only
        self.assertIn("--tmpfs /tmp:rw,nosuid,nodev,mode=1777,size=600m", joined)
        self.assertIn("CLAMD_CONF_TemporaryDirectory=/tmp", joined)
        self.assertIn("-v /var/lib/aibox/clamav:/var/lib/clamav", joined)
        self.assertEqual(args[-1], "clamav/clamav-debian:1.4")
        online = clamav_deploy.run_args(clamav_deploy.settings({"AIBOX_CLAMAV_FRESHCLAM": "on"}))
        self.assertNotIn("CLAMAV_NO_FRESHCLAMD=true", " ".join(online))


SIGTOOL = """== daily.cvd
File: /d/daily.cvd
Build time: 12 Nov 2026 08:21 +0000
Version: 28182
Signatures: 2075123
Functionality level: 90
Builder: raynman
MD5: 0123
Digital signature: abc
Verification OK.
== main.cvd
File: /d/main.cvd
Build time: 16 Dec 2025 23:18 +0000
Version: 63
Verification FAILED.
== bytecode.cvd
ERROR: Can't open file
"""


class DefinitionsTest(unittest.TestCase):
    def test_parse_sigtool(self):
        info = clamav_deploy.parse_sigtool(SIGTOOL)
        self.assertEqual(info["daily.cvd"], {"version": 28182, "build": "12 Nov 2026 08:21 +0000",
                                             "signed": True})
        self.assertEqual((info["main.cvd"]["version"], info["main.cvd"]["signed"]), (63, False))
        self.assertEqual(info["bytecode.cvd"], {"version": None, "build": None, "signed": False})

    def test_plan_only_signed_and_newer(self):
        signed = lambda v: {"version": v, "build": "x", "signed": True}  # noqa: E731
        current = {"daily": signed(28181), "main": signed(63)}
        self.assertEqual(clamav_deploy.plan_update({"daily.cvd": signed(28182), "main.cvd": signed(63)}, current),
                         ["daily.cvd"])
        self.assertEqual(clamav_deploy.plan_update({"bytecode.cvd": signed(339)}, current), ["bytecode.cvd"])
        with self.assertRaises(SystemExit):
            clamav_deploy.plan_update({"daily.cvd": signed(28180)}, current)
        self.assertEqual(clamav_deploy.plan_update({"daily.cvd": signed(28180)}, current, allow_older=True),
                         ["daily.cvd"])
        for bad in ({"version": 28190, "build": "x", "signed": False}, {"version": None, "build": None,
                                                                         "signed": True}):
            with self.assertRaises(SystemExit):
                clamav_deploy.plan_update({"daily.cvd": signed(28190), "main.cvd": bad}, current)

    def test_swap_script_keeps_the_previous_files(self):
        self.assertIn("/db/.prev", clamav_deploy.SWAP)
        self.assertIn('rm -f "/db/$b.cld"', clamav_deploy.SWAP)
        self.assertIn("cp -p /db/.prev/* /db/", clamav_deploy.UNDO)


class EicarTest(unittest.TestCase):
    def test_found_and_clean(self):
        fake = FakeClamd()
        self.addCleanup(fake.close)
        self.assertTrue(clamav_deploy.ready(fake.port, seconds=5))
        self.assertTrue(clamav_deploy.check_eicar(fake.port).endswith("FOUND"))

    def test_scanner_that_finds_nothing_fails(self):
        fake = FakeClamd(find=False)
        self.addCleanup(fake.close)
        with self.assertRaises(RuntimeError):
            clamav_deploy.check_eicar(fake.port)

    def test_eicar_framing(self):
        self.assertEqual(len(clamav_deploy.EICAR), 68)
        self.assertEqual(struct.pack(">I", 0), b"\0\0\0\0")


if __name__ == "__main__":
    unittest.main()
