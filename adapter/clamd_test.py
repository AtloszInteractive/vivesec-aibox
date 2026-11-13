"""clamd client (E27 S5) against a fake clamd on loopback."""
import io
import socket
import struct
import threading
import unittest

import clamd

# The standard antivirus test string, split so this file is not flagged itself.
EICAR = b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$" + b"EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"


class FakeClamd:
    """Speaks just enough of the clamd protocol: PING, VERSION, INSTREAM."""

    def __init__(self, limit=None, reply=None):
        self.limit = limit
        self.reply = reply
        self.received = []
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(5)
        self.port = self.sock.getsockname()[1]
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _read_exact(self, conn, n):
        data = b""
        while len(data) < n:
            part = conn.recv(n - len(data))
            if not part:
                raise EOFError
            data += part
        return data

    def _serve(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            with conn:
                try:
                    self._handle(conn)
                except (EOFError, OSError):
                    pass

    def _handle(self, conn):
        command = b""
        while not command.endswith(b"\0"):
            command += self._read_exact(conn, 1)
        if command == b"zPING\0":
            conn.sendall(b"PONG\0")
            return
        if command == b"zVERSION\0":
            conn.sendall(b"ClamAV 1.4.1/27450/Mon Nov 10 08:00:00 2026\0")
            return
        data = b""
        while True:
            size = struct.unpack(">I", self._read_exact(conn, 4))[0]
            if size == 0:
                break
            data += self._read_exact(conn, size)
        if self.limit is not None and len(data) > self.limit:
            conn.sendall(b"INSTREAM size limit exceeded. ERROR\0")
            return
        self.received.append(data)
        if self.reply is not None:
            conn.sendall(self.reply)
        elif b"EICAR-STANDARD-ANTIVIRUS-TEST-FILE" in data:
            conn.sendall(b"stream: Eicar-Signature FOUND\0")
        else:
            conn.sendall(b"stream: OK\0")

    def close(self):
        self.sock.close()


class AddressTest(unittest.TestCase):
    def test_loopback_and_unix_addresses(self):
        self.assertEqual(clamd.parse_address(""), ("tcp", ("127.0.0.1", 3310)))
        self.assertEqual(clamd.parse_address("localhost:3311"), ("tcp", ("localhost", 3311)))
        self.assertEqual(clamd.parse_address("[::1]:3310"), ("tcp", ("::1", 3310)))
        self.assertEqual(clamd.parse_address("unix:/run/clamav/clamd.sock"),
                         ("unix", "/run/clamav/clamd.sock"))

    def test_remote_or_malformed_addresses_are_refused(self):
        for raw in ("10.0.0.5:3310", "clamd.example.com:3310", "127.0.0.1",
                    "127.0.0.1:x", "127.0.0.1:0", "unix:relative.sock", ":3310"):
            with self.assertRaises(ValueError, msg=raw):
                clamd.parse_address(raw)

    def test_refused_address_makes_the_scanner_unavailable(self):
        scanner = clamd.Clamd("192.168.1.10:3310")
        with self.assertRaises(clamd.ScanUnavailable):
            scanner.scan_stream(io.BytesIO(b"x"))


class ScanTest(unittest.TestCase):
    def setUp(self):
        self.fake = FakeClamd()
        self.addCleanup(self.fake.close)
        self.scanner = clamd.Clamd("127.0.0.1:%d" % self.fake.port, timeout=5)

    def test_ping_and_version(self):
        self.assertTrue(self.scanner.ping())
        self.assertTrue(self.scanner.version().startswith("ClamAV 1.4.1/27450"))

    def test_clean_stream_is_sent_whole(self):
        payload = b"a" * (clamd.CHUNK * 2 + 17)
        self.assertEqual(self.scanner.scan_stream(io.BytesIO(payload)), ("clean", None))
        self.assertEqual(self.fake.received[-1], payload)

    def test_eicar_is_infected(self):
        self.assertEqual(self.scanner.scan_stream(io.BytesIO(EICAR)),
                         ("infected", "Eicar-Signature"))

    def test_size_limit_is_a_scan_error(self):
        fake = FakeClamd(limit=10)
        self.addCleanup(fake.close)
        scanner = clamd.Clamd("127.0.0.1:%d" % fake.port, timeout=5)
        with self.assertRaises(clamd.ScanError):
            scanner.scan_stream(io.BytesIO(b"b" * 100))

    def test_no_daemon_is_unavailable(self):
        probe = socket.socket()
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
        probe.close()
        scanner = clamd.Clamd("127.0.0.1:%d" % port, timeout=2, connect_timeout=1)
        with self.assertRaises(clamd.ScanUnavailable):
            scanner.scan_stream(io.BytesIO(b"x"))
        with self.assertRaises(clamd.ScanUnavailable):
            scanner.ping()

    def test_unexpected_reply_is_an_error(self):
        fake = FakeClamd(reply=b"stream: something odd\0")
        self.addCleanup(fake.close)
        scanner = clamd.Clamd("127.0.0.1:%d" % fake.port, timeout=5)
        with self.assertRaises(clamd.ScanError):
            scanner.scan_stream(io.BytesIO(b"x"))


class InterpretTest(unittest.TestCase):
    def test_replies(self):
        self.assertEqual(clamd.interpret("stream: OK"), ("clean", None))
        self.assertEqual(clamd.interpret("stream: Heuristics.Limits.Exceeded.MaxFileSize FOUND"),
                         ("infected", "Heuristics.Limits.Exceeded.MaxFileSize"))
        with self.assertRaises(clamd.ScanError):
            clamd.interpret("INSTREAM size limit exceeded. ERROR")
        with self.assertRaises(clamd.ScanUnavailable):
            clamd.interpret("")


if __name__ == "__main__":
    unittest.main()
