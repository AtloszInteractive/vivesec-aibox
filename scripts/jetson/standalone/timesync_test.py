"""timesync.py: settings, the SNTP exchange against a fake server, client configuration."""
import socket
import struct
import threading
import time
import unittest

import kitlib
import timesync


class FakeNtp:
    def __init__(self, skew=0.0, stratum=2, leap=0, echo=True):
        self.skew, self.stratum, self.leap, self.echo = skew, stratum, leap, echo
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.port = self.sock.getsockname()[1]
        threading.Thread(target=self.serve, daemon=True).start()

    def serve(self):
        while True:
            try:
                data, addr = self.sock.recvfrom(512)
            except OSError:
                return
            now = time.time() + self.skew
            origin = data[40:48] if self.echo else b"\0" * 8
            reply = (bytes([(self.leap << 6) | (4 << 3) | 4, self.stratum, 6, 0xEC]) + bytes(12)
                     + timesync.to_ntp(now) + origin + timesync.to_ntp(now) + timesync.to_ntp(now))
            self.sock.sendto(reply, addr)

    def close(self):
        self.sock.close()


class SettingsTest(unittest.TestCase):
    def test_defaults_and_none(self):
        cfg = timesync.settings({})
        self.assertEqual((cfg["servers"], cfg["client"], cfg["max_offset"]), ([], "auto", 5.0))
        self.assertIsNone(timesync.settings({"AIBOX_NTP_SERVERS": "none"})["servers"])
        cfg = timesync.settings({"AIBOX_NTP_SERVERS": "dc1.bv.local, 10.0.0.5 fd00::1"})
        self.assertEqual(cfg["servers"], ["dc1.bv.local", "10.0.0.5", "fd00::1"])

    def test_unknown_client_is_auto_and_bad_values_fail(self):
        cfg = timesync.settings({"AIBOX_TIME_CLIENT": "ntpd"})
        self.assertEqual(cfg["client"], "auto")
        self.assertIn("ntpd", cfg["note"])
        for env in ({"AIBOX_NTP_SERVERS": "dc1;rm"}, {"AIBOX_NTP_SERVERS": "-x"},
                    {"AIBOX_NTP_SERVERS": "a..b"}, {"AIBOX_TIME_MAX_OFFSET": "60"},
                    {"AIBOX_TIME_MAX_OFFSET": "x"}):
            with self.assertRaises(kitlib.KitError, msg=env):
                timesync.settings(env)


class SntpTest(unittest.TestCase):
    def test_offset_of_a_skewed_server(self):
        server = FakeNtp(skew=12.5)
        try:
            r = timesync.sntp("127.0.0.1", port=server.port)
        finally:
            server.close()
        self.assertAlmostEqual(r["offset"], 12.5, delta=0.2)
        self.assertEqual(r["stratum"], 2)

    def test_unsynchronised_or_foreign_answers_are_refused(self):
        for kw in ({"stratum": 16}, {"stratum": 0}, {"leap": 3}, {"echo": False}):
            server = FakeNtp(**kw)
            try:
                with self.assertRaises(kitlib.KitError, msg=kw):
                    timesync.sntp("127.0.0.1", port=server.port)
            finally:
                server.close()

    def test_probe_all_reports_unreachable(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()
        orig = timesync.sntp
        try:
            timesync.sntp = lambda s, port=123: orig(s, timeout=0.3, port=port)
            results = timesync.probe_all(["127.0.0.1"], port=port)
        finally:
            timesync.sntp = orig
        self.assertFalse(results[0]["ok"])

    def test_ntp_time_round_trip(self):
        t = 1794518117.25
        self.assertAlmostEqual(timesync.from_ntp(timesync.to_ntp(t)), t, places=6)
        self.assertEqual(struct.unpack("!I", timesync.to_ntp(0)[:4])[0], timesync.NTP_DELTA)


class ConfigTest(unittest.TestCase):
    def test_chrony_uses_only_given_servers_and_serves_nothing(self):
        text = timesync.chrony_conf(["dc1.bv.local", "10.0.0.5"])
        self.assertIn("server dc1.bv.local iburst", text)
        self.assertIn("server 10.0.0.5 iburst", text)
        self.assertNotIn("pool", text)
        self.assertNotIn("sourcedir", text)
        self.assertIn("port 0", text.splitlines())
        self.assertIn("makestep 1.0 3", text)

    def test_timesyncd_drops_the_fallback_pool(self):
        self.assertGreater(timesync.TIMESYNCD_DROPIN.rsplit("/", 1)[1], "nv-fallback-ntp.conf")
        text = timesync.timesyncd_conf(["dc1"])
        self.assertIn("NTP=dc1\n", text)
        self.assertIn("FallbackNTP=\n", text)
        self.assertIn("NTP=\n", timesync.timesyncd_conf(None))


if __name__ == "__main__":
    unittest.main()
