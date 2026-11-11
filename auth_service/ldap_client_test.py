import os
import shutil
import socket
import ssl
import subprocess
import tempfile
import threading
import unittest

import ldap_client as lc

GIT_OPENSSL = r"C:\Program Files\Git\usr\bin\openssl.exe"


def openssl():
    found = shutil.which("openssl")
    if found:
        return found
    return GIT_OPENSSL if os.path.exists(GIT_OPENSSL) else None


# ---------------------------------------------------------------- a scripted server

def result(code=0, diagnostic=""):
    return lc._integer(code, tag=0x0A) + lc._octets("") + lc._octets(diagnostic)


def bind_response(code=0, diagnostic=""):
    return lc._tlv(0x61, result(code, diagnostic))


def search_entry(dn, attributes):
    parts = [lc._sequence(lc._octets(name), lc._sequence(*[lc._octets(v) for v in values], tag=0x31))
             for name, values in attributes.items()]
    return lc._sequence(lc._octets(dn), lc._sequence(*parts), tag=0x64)


def search_done(code=0):
    return lc._tlv(0x65, result(code))


def extended_response(code=0):
    return lc._tlv(0x78, result(code))


def read_message(sock):
    head = b""
    while len(head) < 2:
        chunk = sock.recv(2 - len(head))
        if not chunk:
            return None
        head += chunk
    if head[1] < 0x80:
        length, extra = head[1], b""
    else:
        extra = sock.recv(head[1] & 0x7F)
        length = int.from_bytes(extra, "big")
    body = b""
    while len(body) < length:
        body += sock.recv(length - len(body))
    items = lc._children(body)
    return lc._decode_integer(items[0][1]), items[1][0], items[1][1]


class ScriptedServer(threading.Thread):
    """Answers each request with `handler(op_tag, op) -> [op bytes]`. With
    `tls_context` an ldaps server, or StartTLS on request."""

    def __init__(self, handler, tls_context=None, ldaps=False):
        super().__init__(daemon=True)
        self.handler = handler
        self.tls_context = tls_context
        self.ldaps = ldaps
        self.listener = socket.create_server(("127.0.0.1", 0))
        self.port = self.listener.getsockname()[1]
        self.requests = []
        self.error = None

    def run(self):
        sock = None
        try:
            sock, _ = self.listener.accept()
            sock.settimeout(5)
            if self.ldaps:
                sock = self.tls_context.wrap_socket(sock, server_side=True)
            while True:
                message = read_message(sock)
                if message is None:
                    break
                message_id, op_tag, op = message
                self.requests.append((op_tag, op))
                if op_tag == 0x42:
                    break
                if op_tag == 0x77:
                    sock.sendall(lc._sequence(lc._integer(message_id), extended_response(0)))
                    sock = self.tls_context.wrap_socket(sock, server_side=True)
                    continue
                for answer in self.handler(op_tag, op):
                    sock.sendall(lc._sequence(lc._integer(message_id), answer))
        except (OSError, ssl.SSLError) as e:
            self.error = e
        finally:
            if sock is not None:
                sock.close()
            self.listener.close()

    def tags(self):
        return [tag for tag, _ in self.requests]


class PlainForTests(lc.TlsPolicy):
    """No TLS: only for exercising the protocol against the scripted server."""

    def __init__(self):
        self.pins, self.ca_file = frozenset(), None

    def wrap(self, sock, host):
        return sock


def directory_handler(op_tag, op):
    if op_tag == 0x60:
        name = lc._children(op)[1][1].decode()
        password = lc._children(op)[2][1].decode()
        if name == "CN=svc" and password == "svc-pw":
            return [bind_response(0)]
        return [bind_response(49, "80090308: LdapErr: DSID-0C09044E, comment: AcceptSecurityContext "
                                  "error, data 52e, v4563")]
    if op_tag == 0x63:
        base = lc._children(op)[0][1].decode()
        if base == "DC=missing":
            return [search_done(32)]
        return [search_entry("CN=Anna,DC=test", {"sAMAccountName": [b"anna"], "displayName": [b"Anna T"]}),
                lc._sequence(lc._octets("ldap://other/DC=x"), tag=0x73),
                search_done(0)]
    return []


class ProtocolTest(unittest.TestCase):
    def connect(self, handler):
        server = ScriptedServer(handler)
        server.start()
        self.addCleanup(server.join, 5)
        connection = lc.Connection("ldaps://127.0.0.1:%d" % server.port, PlainForTests(), timeout=5)
        connection.open()
        self.addCleanup(connection.close)
        return server, connection

    def test_bind_search_and_unbind(self):
        server, connection = self.connect(directory_handler)
        connection.bind("CN=svc", "svc-pw")
        entries = connection.search("DC=test", lc.SCOPE_SUBTREE, lc.equal("sAMAccountName", "anna"),
                                    ("sAMAccountName", "displayName"))
        self.assertEqual(entries, [("CN=Anna,DC=test", {"samaccountname": [b"anna"], "displayname": [b"Anna T"]})])
        self.assertEqual(connection.search("DC=missing", lc.SCOPE_BASE, lc.present("objectClass")), [])
        connection.close()
        server.join(5)
        self.assertEqual(server.tags(), [0x60, 0x63, 0x63, 0x42])

    def test_a_refused_bind_carries_the_code_and_the_diagnostic(self):
        _server, connection = self.connect(directory_handler)
        with self.assertRaises(lc.ResultError) as caught:
            connection.bind("CN=svc", "wrong")
        self.assertEqual(caught.exception.code, 49)
        self.assertIn("data 52e", caught.exception.diagnostic)

    def test_an_empty_password_never_reaches_the_server(self):
        server, connection = self.connect(directory_handler)
        self.assertRaises(ValueError, connection.bind, "CN=svc", "")
        self.assertRaises(ValueError, connection.bind, "CN=svc", None)
        connection.close()
        server.join(5)
        self.assertEqual(server.tags(), [0x42])

    def test_too_many_entries_is_an_error(self):
        def handler(op_tag, op):
            return [search_entry("CN=%d" % i, {}) for i in range(3)] + [search_done(0)]
        _server, connection = self.connect(handler)
        with self.assertRaises(lc.ResultError) as caught:
            connection.search("DC=test", lc.SCOPE_SUBTREE, lc.present("objectClass"), size_limit=2)
        self.assertEqual(caught.exception.code, lc.RESULT_SIZE_LIMIT)

    def test_a_wrong_message_id_or_a_disconnection_notice_is_refused(self):
        def handler(op_tag, op):
            return [bind_response(0)]

        server = ScriptedServer(handler)
        server.handler = None

        def run():
            sock, _ = server.listener.accept()
            message_id, _tag, _op = read_message(sock)
            sock.sendall(lc._sequence(lc._integer(message_id + 5), bind_response(0)))
            read_message(sock)
            sock.sendall(lc._sequence(lc._integer(0), extended_response(52)))
            sock.close()
        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        connection = lc.Connection("ldaps://127.0.0.1:%d" % server.port, PlainForTests(), timeout=5)
        connection.open()
        self.assertRaisesRegex(lc.LdapError, "message id", connection.bind, "CN=svc", "pw")
        self.assertRaisesRegex(lc.LdapError, "disconnection", connection.bind, "CN=svc", "pw")
        connection.close()
        thread.join(5)
        server.listener.close()

    def test_oversized_and_malformed_messages(self):
        def run(payload):
            listener = socket.create_server(("127.0.0.1", 0))

            def serve():
                sock, _ = listener.accept()
                read_message(sock)
                sock.sendall(payload)
                sock.close()
                listener.close()
            threading.Thread(target=serve, daemon=True).start()
            connection = lc.Connection("ldaps://127.0.0.1:%d" % listener.getsockname()[1],
                                       PlainForTests(), timeout=2)
            connection.open()
            try:
                connection.bind("CN=svc", "pw")
            finally:
                connection.close()

        self.assertRaisesRegex(lc.LdapError, "too large", run, b"\x30\x84\x7f\xff\xff\xff")
        self.assertRaisesRegex(lc.LdapError, "length", run, b"\x30\x85\x01\x00\x00\x00\x00")
        self.assertRaisesRegex(lc.LdapError, "not an LDAP message", run, b"\x04\x00")
        self.assertRaisesRegex(lc.LdapError, "truncated", run, b"\x30\x05\x02\x01\x01\x61\x10")
        self.assertRaisesRegex(lc.LdapError, "closed", run, b"\x30\x10\x02")

    def test_a_silent_server_times_out(self):
        listener = socket.create_server(("127.0.0.1", 0))
        self.addCleanup(listener.close)
        accepted = []
        threading.Thread(target=lambda: accepted.append(listener.accept()), daemon=True).start()
        connection = lc.Connection("ldaps://127.0.0.1:%d" % listener.getsockname()[1],
                                   PlainForTests(), timeout=0.5)
        connection.open()
        self.assertRaises(lc.LdapError, connection.bind, "CN=svc", "pw")
        connection.close()
        for sock, _address in accepted:
            sock.close()


class EncodingTest(unittest.TestCase):
    def test_lengths_and_integers_round_trip(self):
        for size in (0, 1, 127, 128, 255, 256, 70000):
            data = lc._octets(b"x" * size)
            tag, content, end = lc._read_tlv(data, 0)
            self.assertEqual((tag, len(content), end), (0x04, size, len(data)))
        for value in (0, 1, 127, 128, 255, 256, -1, -129, 2 ** 31 - 1):
            self.assertEqual(lc._decode_integer(lc._read_tlv(lc._integer(value), 0)[1]), value)

    def test_a_value_cannot_change_the_filter(self):
        hostile = "anna)(|(objectClass=*"
        encoded = lc.equal("sAMAccountName", hostile)
        tag, content, _ = lc._read_tlv(encoded, 0)
        self.assertEqual(tag, 0xA3)
        self.assertEqual([value for _tag, value in lc._children(content)],
                         [b"sAMAccountName", hostile.encode()])

    def test_the_chain_filter(self):
        tag, content, _ = lc._read_tlv(lc.chain("member", "CN=Anna,DC=test"), 0)
        self.assertEqual(tag, 0xA9)
        self.assertEqual(lc._children(content), [(0x81, lc.IN_CHAIN.encode()), (0x82, b"member"),
                                                 (0x83, b"CN=Anna,DC=test")])

    def test_urls(self):
        self.assertEqual(lc.parse_url("ldaps://dc01.bv.local"), ("ldaps", "dc01.bv.local", 636))
        self.assertEqual(lc.parse_url("LDAP://10.0.0.5:3389/"), ("ldap", "10.0.0.5", 3389))
        self.assertEqual(lc.parse_url("ldaps://[fd00::5]:1636"), ("ldaps", "fd00::5", 1636))
        for bad in ("", "http://dc", "ldapi:///var/run", "ldaps://user:pw@dc", "ldaps://dc/DC=x",
                    "ldaps://dc?x", "ldaps://:636", "ldaps://dc:99999"):
            self.assertRaises(ValueError, lc.parse_url, bad)

    def test_fingerprints(self):
        hex_value = "ab" * 32
        self.assertEqual(lc.normalize_fingerprint(":".join(["AB"] * 32)), hex_value)
        self.assertEqual(lc.normalize_fingerprint("sha256/" + hex_value), hex_value)
        self.assertIsNone(lc.normalize_fingerprint("ab" * 20))
        self.assertIsNone(lc.normalize_fingerprint(None))
        self.assertEqual(lc.format_fingerprint(hex_value)[:5], "AB:AB")

    def test_a_policy_needs_a_pin_or_a_ca(self):
        self.assertRaises(ValueError, lc.TlsPolicy)
        self.assertRaises(ValueError, lc.TlsPolicy, ["not-a-fingerprint"])
        self.assertEqual(lc.TlsPolicy(["AB" * 32]).pins, frozenset(["ab" * 32]))


@unittest.skipUnless(openssl(), "openssl is needed for the TLS tests")
class TlsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        tool = openssl()
        cls.files = {}
        for name, cn in (("dc", "dc01.test.local"), ("other", "rogue.test.local")):
            key, cert = (os.path.join(cls.temp.name, name + ext) for ext in (".key", ".crt"))
            subprocess.run([tool, "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:P-256",
                            "-nodes", "-keyout", key, "-out", cert, "-days", "2", "-subj", "/CN=" + cn,
                            "-addext", "subjectAltName=DNS:%s" % cn],
                           check=True, capture_output=True)
            with open(cert) as handle:
                der = ssl.PEM_cert_to_DER_cert(handle.read())
            cls.files[name] = (key, cert, lc.fingerprint(der))

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def server_context(self, name):
        key, cert, _ = self.files[name]
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert, key)
        return context

    def serve(self, name="dc", ldaps=False):
        server = ScriptedServer(directory_handler, self.server_context(name), ldaps=ldaps)
        server.start()
        self.addCleanup(server.join, 5)
        return server

    def test_starttls_with_the_pinned_certificate(self):
        server = self.serve()
        policy = lc.TlsPolicy([self.files["dc"][2]])
        with lc.Connection("ldap://127.0.0.1:%d" % server.port, policy, timeout=5) as connection:
            connection.bind("CN=svc", "svc-pw")
            self.assertIsInstance(connection.sock, ssl.SSLSocket)
        server.join(5)
        self.assertEqual(server.tags(), [0x77, 0x60, 0x42])

    def test_ldaps_with_the_pinned_certificate(self):
        server = self.serve(ldaps=True)
        policy = lc.TlsPolicy([self.files["dc"][2]])
        with lc.Connection("ldaps://127.0.0.1:%d" % server.port, policy, timeout=5) as connection:
            connection.bind("CN=svc", "svc-pw")

    def test_an_unpinned_certificate_stops_before_any_bind(self):
        for ldaps in (False, True):
            server = self.serve("other", ldaps=ldaps)
            policy = lc.TlsPolicy([self.files["dc"][2]])
            scheme = "ldaps" if ldaps else "ldap"
            connection = lc.Connection("%s://127.0.0.1:%d" % (scheme, server.port), policy, timeout=5)
            with self.assertRaises(lc.TlsPinError) as caught:
                connection.open()
            self.assertEqual(caught.exception.fingerprint, self.files["other"][2])
            server.join(5)
            self.assertNotIn(0x60, server.tags())

    def test_ca_verification_checks_the_chain_and_the_host_name(self):
        _key, cert, _ = self.files["dc"]
        policy = lc.TlsPolicy(ca_file=cert)
        server = self.serve(ldaps=True)
        connection = lc.Connection("ldaps://127.0.0.1:%d" % server.port, policy, timeout=5)
        self.assertRaisesRegex(lc.LdapError, "TLS handshake", connection.open)  # 127.0.0.1 is not the name
        server = self.serve("other", ldaps=True)
        connection = lc.Connection("ldaps://127.0.0.1:%d" % server.port, lc.TlsPolicy(ca_file=cert), timeout=5)
        self.assertRaisesRegex(lc.LdapError, "TLS handshake", connection.open)

    def test_fetch_certificate_for_pinning(self):
        server = self.serve()
        der = lc.fetch_certificate("ldap://127.0.0.1:%d" % server.port, timeout=5)
        self.assertEqual(lc.fingerprint(der), self.files["dc"][2])


if __name__ == "__main__":
    unittest.main()
