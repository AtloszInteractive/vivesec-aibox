"""A minimal LDAP v3 client for the directory login (E27 S6): standard library only.

It does what the login needs and nothing more: StartTLS or LDAPS, a simple
bind, a search, and an unbind. Every connection is encrypted: an `ldap://` URL
always upgrades with StartTLS before anything else is sent, an `ldaps://` URL
starts with TLS. Plain-text binds do not exist here.

The server is trusted through a `TlsPolicy`, checked right after the TLS
handshake and before the first bind:

- `pins`: SHA-256 fingerprints of the accepted server certificates. The
  certificate itself is pinned, so a self-signed domain controller certificate
  is enough, and neither the chain, the host name nor the expiry date matter.
- `ca_file`: the usual chain and host name check against these CA
  certificates.

With both, both must pass. A policy with neither cannot be built.

Filters are built as structures (`equal`, `chain`, `all_of`, ...), never parsed
from text, so a value typed by a user cannot change a filter: it always ends up
as one octet string.

Only definite BER lengths of at most four bytes are read, a message may not be
larger than MAX_MESSAGE_BYTES, and every read has a deadline.
"""
import hashlib
import re
import socket
import ssl
import time
from urllib.parse import urlsplit

MAX_MESSAGE_BYTES = 4 * 1024 * 1024
STARTTLS_OID = b"1.3.6.1.4.1.1466.20037"
# LDAP_MATCHING_RULE_IN_CHAIN: transitive group membership in Active Directory.
IN_CHAIN = "1.2.840.113556.1.4.1941"
SCOPE_BASE, SCOPE_ONE, SCOPE_SUBTREE = 0, 1, 2
NO_ATTRIBUTES = "1.1"
DEFAULT_PORTS = {"ldaps": 636, "ldap": 389}
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

RESULT_SUCCESS = 0
RESULT_SIZE_LIMIT = 4
RESULT_NO_SUCH_OBJECT = 32
RESULT_INVALID_CREDENTIALS = 49

# Protocol operation tags (RFC 4511, [APPLICATION n]).
_BIND_REQUEST, _BIND_RESPONSE, _UNBIND_REQUEST = 0x60, 0x61, 0x42
_SEARCH_REQUEST, _SEARCH_ENTRY, _SEARCH_DONE, _SEARCH_REFERENCE = 0x63, 0x64, 0x65, 0x73
_EXTENDED_REQUEST, _EXTENDED_RESPONSE = 0x77, 0x78


class LdapError(Exception):
    """The directory cannot be used: connection, TLS or protocol problem."""


class TlsPinError(LdapError):
    """The server presented a certificate that is not pinned."""

    def __init__(self, fingerprint):
        super().__init__("certificate %s is not pinned" % format_fingerprint(fingerprint))
        self.fingerprint = fingerprint


class ResultError(LdapError):
    """The server answered an operation with a result code other than success."""

    def __init__(self, operation, code, diagnostic=""):
        super().__init__("%s failed: result %d" % (operation, code))
        self.operation = operation
        self.code = code
        self.diagnostic = diagnostic


# ---------------------------------------------------------------- fingerprints

def normalize_fingerprint(text):
    """`AB:CD:...` or `abcd...` (64 hex digits) -> lower-case hex, or None."""
    if not isinstance(text, str):
        return None
    value = text.strip().lower().replace(":", "")
    if value.startswith("sha256/"):
        value = value[len("sha256/"):]
    return value if SHA256_RE.match(value) else None


def format_fingerprint(hex_digest):
    return ":".join(hex_digest[i:i + 2] for i in range(0, len(hex_digest), 2)).upper()


def fingerprint(der):
    return hashlib.sha256(der).hexdigest()


# ---------------------------------------------------------------- URLs

def parse_url(url):
    """(scheme, host, port) of an `ldaps://host[:port]` or `ldap://host[:port]`
    URL; ValueError for anything else (paths, credentials, other schemes)."""
    if not isinstance(url, str) or not url.strip():
        raise ValueError("empty LDAP URL")
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower()
    if scheme not in DEFAULT_PORTS:
        raise ValueError("%r: only ldaps:// and ldap:// (with StartTLS) are allowed" % url)
    if parts.username or parts.password or parts.path not in ("", "/") or parts.query or parts.fragment:
        raise ValueError("%r: only scheme, host and port are allowed" % url)
    try:
        port = parts.port or DEFAULT_PORTS[scheme]
    except ValueError:
        raise ValueError("%r: invalid port" % url)
    host = parts.hostname
    if not host:
        raise ValueError("%r: no host" % url)
    return scheme, host, port


# ---------------------------------------------------------------- TLS policy

class TlsPolicy:
    def __init__(self, pins=(), ca_file=None):
        normalized = []
        for pin in pins or ():
            value = normalize_fingerprint(pin)
            if value is None:
                raise ValueError("invalid certificate fingerprint %r" % pin)
            normalized.append(value)
        if not normalized and not ca_file:
            raise ValueError("the directory server must be pinned (fingerprint) or verified (CA file)")
        self.pins = frozenset(normalized)
        self.ca_file = ca_file or None

    def context(self):
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        if self.ca_file:
            context.load_verify_locations(cafile=self.ca_file)
            context.check_hostname = True
            context.verify_mode = ssl.CERT_REQUIRED
        else:
            # The pin below is the trust decision; the chain does not matter.
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
        return context

    def wrap(self, sock, host):
        try:
            secure = self.context().wrap_socket(sock, server_hostname=host)
        except (ssl.SSLError, ssl.CertificateError) as e:
            raise LdapError("TLS handshake with %s failed: %s" % (host, e))
        der = secure.getpeercert(binary_form=True)
        if not der:
            secure.close()
            raise LdapError("%s presented no certificate" % host)
        if self.pins and fingerprint(der) not in self.pins:
            secure.close()
            raise TlsPinError(fingerprint(der))
        return secure


# ---------------------------------------------------------------- BER

def _length(n):
    if n < 0x80:
        return bytes([n])
    raw = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(raw)]) + raw


def _tlv(tag, content):
    return bytes([tag]) + _length(len(content)) + content


def _integer(value, tag=0x02):
    size = max(1, (value.bit_length() + 8) // 8)
    return _tlv(tag, value.to_bytes(size, "big", signed=True))


def _octets(value, tag=0x04):
    return _tlv(tag, value.encode("utf-8") if isinstance(value, str) else bytes(value))


def _boolean(value):
    return _tlv(0x01, b"\xff" if value else b"\x00")


def _sequence(*parts, tag=0x30):
    return _tlv(tag, b"".join(parts))


def _read_tlv(data, pos):
    """(tag, content, next position) of the element at `pos`."""
    if pos + 2 > len(data):
        raise LdapError("truncated BER element")
    tag, first = data[pos], data[pos + 1]
    if tag & 0x1F == 0x1F:
        raise LdapError("unsupported BER tag")
    pos += 2
    if first < 0x80:
        length = first
    else:
        count = first & 0x7F
        if not 1 <= count <= 4 or pos + count > len(data):
            raise LdapError("unsupported BER length")
        length = int.from_bytes(data[pos:pos + count], "big")
        pos += count
    end = pos + length
    if end > len(data):
        raise LdapError("truncated BER element")
    return tag, data[pos:end], end


def _children(content):
    items, pos = [], 0
    while pos < len(content):
        tag, value, pos = _read_tlv(content, pos)
        items.append((tag, value))
    return items


def _decode_integer(content):
    if not 1 <= len(content) <= 8:
        raise LdapError("bad BER integer")
    return int.from_bytes(content, "big", signed=True)


def _text(content):
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError:
        raise LdapError("text is not UTF-8")


# ---------------------------------------------------------------- filters

def equal(attribute, value):
    return _sequence(_octets(attribute), _octets(value), tag=0xA3)


def present(attribute):
    return _octets(attribute, tag=0x87)


def extensible(rule, attribute, value):
    return _sequence(_octets(rule, tag=0x81), _octets(attribute, tag=0x82),
                     _octets(value, tag=0x83), tag=0xA9)


def chain(attribute, dn):
    """`attribute:1.2.840.113556.1.4.1941:=dn`: transitive membership in AD."""
    return extensible(IN_CHAIN, attribute, dn)


def all_of(*filters):
    return _sequence(*filters, tag=0xA0)


def any_of(*filters):
    return _sequence(*filters, tag=0xA1)


def negate(filter_bytes):
    return _tlv(0xA2, filter_bytes)


# ---------------------------------------------------------------- connection

def _ldap_result(op_tag, content, operation):
    """Raise ResultError unless the LDAPResult in `content` is a success."""
    items = _children(content)
    if len(items) < 3 or items[0][0] != 0x0A:
        raise LdapError("malformed %s response" % operation)
    code = _decode_integer(items[0][1])
    if code != RESULT_SUCCESS:
        diagnostic = items[2][1].decode("utf-8", "replace")[:300] if items[2][0] == 0x04 else ""
        raise ResultError(operation, code, diagnostic)
    return items


class Connection:
    """One encrypted connection to one directory server. Not thread-safe; open
    one per login or lookup."""

    def __init__(self, url, tls, timeout=5.0):
        self.scheme, self.host, self.port = parse_url(url)
        self.url = url
        self.tls = tls
        self.timeout = float(timeout)
        self.sock = None
        self._message_id = 0
        self._deadline = None

    # -- transport -------------------------------------------------------------
    def open(self):
        self._deadline = time.monotonic() + self.timeout
        try:
            raw = socket.create_connection((self.host, self.port), timeout=self.timeout)
        except OSError as e:
            raise LdapError("cannot connect to %s:%d: %s" % (self.host, self.port, e))
        try:
            if self.scheme == "ldaps":
                self.sock = self.tls.wrap(raw, self.host)
            else:
                self.sock = raw
                self._start_tls()
                self.sock = self.tls.wrap(raw, self.host)
        except BaseException:
            raw.close()
            self.sock = None
            raise
        return self

    def close(self):
        if self.sock is None:
            return
        try:
            self._deadline = time.monotonic() + 1.0
            self._send(_tlv(_UNBIND_REQUEST, b""))
        except (OSError, LdapError):
            pass
        try:
            self.sock.close()
        finally:
            self.sock = None

    def __enter__(self):
        return self.open()

    def __exit__(self, *exc):
        self.close()

    def _remaining(self):
        left = self._deadline - time.monotonic()
        if left <= 0:
            raise LdapError("directory operation timed out")
        return left

    def _send(self, op):
        if self.sock is None:
            raise LdapError("connection is closed")
        self._message_id += 1
        message = _sequence(_integer(self._message_id), op)
        self.sock.settimeout(self._remaining())
        try:
            self.sock.sendall(message)
        except OSError as e:
            raise LdapError("send failed: %s" % e)
        return self._message_id

    def _recv_exact(self, count):
        data = bytearray()
        while len(data) < count:
            self.sock.settimeout(self._remaining())
            try:
                chunk = self.sock.recv(min(count - len(data), 65536))
            except OSError as e:
                raise LdapError("receive failed: %s" % e)
            if not chunk:
                raise LdapError("the directory server closed the connection")
            data += chunk
        return bytes(data)

    def _receive(self, message_id):
        """(operation tag, operation content) of the next message."""
        head = self._recv_exact(2)
        if head[0] != 0x30:
            raise LdapError("not an LDAP message")
        if head[1] < 0x80:
            length, length_bytes = head[1], b""
        else:
            count = head[1] & 0x7F
            if not 1 <= count <= 4:
                raise LdapError("unsupported BER length")
            length_bytes = self._recv_exact(count)
            length = int.from_bytes(length_bytes, "big")
        if length > MAX_MESSAGE_BYTES:
            raise LdapError("LDAP message too large")
        body = self._recv_exact(length)
        items = _children(body)
        if len(items) < 2 or items[0][0] != 0x02:
            raise LdapError("malformed LDAP message")
        received = _decode_integer(items[0][1])
        op_tag, op = items[1]
        if received == 0 and op_tag == _EXTENDED_RESPONSE:
            raise LdapError("the directory server ended the connection (notice of disconnection)")
        if received != message_id:
            raise LdapError("unexpected LDAP message id")
        return op_tag, op

    # -- operations ------------------------------------------------------------
    def _start_tls(self):
        message_id = self._send(_sequence(_octets(STARTTLS_OID, tag=0x80), tag=_EXTENDED_REQUEST))
        op_tag, op = self._receive(message_id)
        if op_tag != _EXTENDED_RESPONSE:
            raise LdapError("unexpected answer to StartTLS")
        try:
            _ldap_result(op_tag, op, "StartTLS")
        except ResultError as e:
            raise LdapError("the server refused StartTLS (result %d)" % e.code)

    def bind(self, dn, password):
        """Simple bind. An empty password is refused here: LDAP treats it as an
        unauthenticated bind, which "succeeds" without checking anything."""
        if not isinstance(password, str) or not password:
            raise ValueError("a bind needs a password")
        if not isinstance(dn, str) or not dn:
            raise ValueError("a bind needs a name")
        self._deadline = time.monotonic() + self.timeout
        message_id = self._send(_sequence(_integer(3), _octets(dn), _octets(password, tag=0x80),
                                          tag=_BIND_REQUEST))
        op_tag, op = self._receive(message_id)
        if op_tag != _BIND_RESPONSE:
            raise LdapError("unexpected answer to bind")
        _ldap_result(op_tag, op, "bind")

    def search(self, base, scope, filter_bytes, attributes=(NO_ATTRIBUTES,), size_limit=10):
        """[(dn, {attribute (lower case): [bytes, ...]})]. A missing base answers
        []; more than `size_limit` entries raise ResultError (size limit)."""
        self._deadline = time.monotonic() + self.timeout
        request = _sequence(
            _octets(base), _integer(scope, tag=0x0A), _integer(0, tag=0x0A),
            _integer(size_limit), _integer(max(1, int(self.timeout))), _boolean(False),
            filter_bytes, _sequence(*[_octets(name) for name in attributes]),
            tag=_SEARCH_REQUEST)
        message_id = self._send(request)
        entries = []
        while True:
            op_tag, op = self._receive(message_id)
            if op_tag == _SEARCH_ENTRY:
                entries.append(self._entry(op))
                if len(entries) > size_limit:
                    raise ResultError("search", RESULT_SIZE_LIMIT)
            elif op_tag == _SEARCH_REFERENCE:
                continue  # continuation references (other partitions) are not followed
            elif op_tag == _SEARCH_DONE:
                try:
                    _ldap_result(op_tag, op, "search")
                except ResultError as e:
                    if e.code == RESULT_NO_SUCH_OBJECT:
                        return []
                    raise
                return entries
            else:
                raise LdapError("unexpected answer to search")

    @staticmethod
    def _entry(op):
        items = _children(op)
        if len(items) != 2 or items[0][0] != 0x04 or items[1][0] != 0x30:
            raise LdapError("malformed search entry")
        attributes = {}
        for tag, partial in _children(items[1][1]):
            parts = _children(partial)
            if tag != 0x30 or len(parts) != 2 or parts[0][0] != 0x04 or parts[1][0] != 0x31:
                raise LdapError("malformed search entry")
            values = [value for value_tag, value in _children(parts[1][1]) if value_tag == 0x04]
            attributes.setdefault(_text(parts[0][1]).lower(), []).extend(values)
        return _text(items[0][1]), attributes


def fetch_certificate(url, timeout=5.0):
    """The DER certificate a server presents, without trusting it: for an
    administrator to compare and pin (`manage.py ldap-cert`)."""
    scheme, host, port = parse_url(url)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE

    class _Inspect(TlsPolicy):
        def __init__(self):
            self.pins, self.ca_file = frozenset(), None

        def context(self):
            return context

    connection = Connection(url, _Inspect(), timeout)
    connection.open()
    try:
        return connection.sock.getpeercert(binary_form=True)
    finally:
        connection.close()
