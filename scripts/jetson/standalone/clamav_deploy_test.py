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
                                  "max_mb": 300, "memory": "3g", "freshclam": False})

    def test_invalid_values_stop(self):
        for env in ({"AIBOX_CLAMAV_PORT": "0"}, {"AIBOX_CLAMAV_PORT": "x"},
                    {"AIBOX_CLAMAV_MAX_MB": "0"}, {"AIBOX_CLAMAV_MEMORY": "lots"},
                    {"AIBOX_CLAMAV_FRESHCLAM": "maybe"}):
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
        self.assertEqual(args[-1], "clamav/clamav-debian:1.4")
        online = clamav_deploy.run_args(clamav_deploy.settings({"AIBOX_CLAMAV_FRESHCLAM": "on"}))
        self.assertNotIn("CLAMAV_NO_FRESHCLAMD=true", " ".join(online))


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
