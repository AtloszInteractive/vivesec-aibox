"""Virus scanning through clamd (E27 S5), pure stdlib.

A file uploaded in standalone mode waits in quarantine until clamd has seen
it. The adapter streams it with clamd's `INSTREAM` command over a local
socket, so no ClamAV library is needed and the adapter stays stdlib-only.

`ADAPTER_CLAMD` names the daemon: `host:port` (loopback only) or
`unix:/path/to/clamd.sock`. A TCP address that is not loopback is refused: the
documents must never leave the box to be scanned somewhere else.

Results:
  ("clean", None)          -- clamd answered OK;
  ("infected", signature)  -- clamd found something (also the heuristic
                              "limits exceeded" alerts when clamd is set to
                              raise them, which is how a file too large to scan
                              fails closed);
  ScanUnavailable          -- clamd could not be reached; worth retrying;
  ScanError                -- clamd answered with an error for this file.
"""
import calendar
import ipaddress
import os
import re
import socket
import struct
import time

DEFAULT_ADDRESS = "127.0.0.1:3310"
CHUNK = 64 * 1024
MAX_REPLY = 4096


class ScanUnavailable(Exception):
    """clamd is not reachable (or not configured usably)."""


class ScanError(Exception):
    """clamd refused to scan this stream."""


def _loopback_host(host):
    if host.casefold() == "localhost":
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    mapped = getattr(address, "ipv4_mapped", None)
    return (mapped or address).is_loopback


def parse_address(raw):
    """("unix", path) or ("tcp", (host, port)); ValueError for anything else,
    including a TCP address that is not loopback."""
    value = (raw or "").strip() or DEFAULT_ADDRESS
    if value.startswith("unix:"):
        path = value[len("unix:"):]
        if not path.startswith("/"):
            raise ValueError("clamd unix socket must be an absolute path")
        return "unix", path
    host, sep, port = value.rpartition(":")
    if not sep or not host:
        raise ValueError("clamd address must be host:port or unix:/path")
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    try:
        port_number = int(port)
    except ValueError:
        raise ValueError("clamd port is not a number")
    if not 0 < port_number < 65536:
        raise ValueError("clamd port out of range")
    if not _loopback_host(host):
        raise ValueError("clamd must listen on loopback (got %r)" % host)
    return "tcp", (host, port_number)


class Clamd:
    def __init__(self, address=None, timeout=120.0, connect_timeout=5.0):
        self.raw_address = (address or "").strip() or DEFAULT_ADDRESS
        try:
            self.address = parse_address(self.raw_address)
            self.config_error = None
        except ValueError as e:
            self.address = None
            self.config_error = str(e)
        self.timeout = timeout
        self.connect_timeout = connect_timeout

    @classmethod
    def from_env(cls, env=None):
        env = env if env is not None else os.environ
        return cls(env.get("ADAPTER_CLAMD"),
                   timeout=float(env.get("ADAPTER_CLAMD_TIMEOUT", "120") or 120))

    def _connect(self):
        if self.address is None:
            raise ScanUnavailable(self.config_error or "clamd not configured")
        kind, target = self.address
        try:
            if kind == "unix":
                sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                sock.settimeout(self.connect_timeout)
                sock.connect(target)
            else:
                sock = socket.create_connection(target, timeout=self.connect_timeout)
        except (OSError, AttributeError) as e:
            raise ScanUnavailable("clamd not reachable: %s" % e)
        sock.settimeout(self.timeout)
        return sock

    @staticmethod
    def _reply(sock):
        data = b""
        while len(data) < MAX_REPLY:
            try:
                part = sock.recv(MAX_REPLY)
            except socket.timeout:
                raise ScanUnavailable("clamd did not answer in time")
            except OSError as e:
                raise ScanUnavailable("clamd connection failed: %s" % e)
            if not part:
                break
            data += part
            if b"\0" in data:
                break
        return data.split(b"\0", 1)[0].decode("utf-8", "replace").strip()

    def _command(self, name):
        sock = self._connect()
        try:
            sock.sendall(b"z" + name + b"\0")
            return self._reply(sock)
        except OSError as e:
            raise ScanUnavailable("clamd connection failed: %s" % e)
        finally:
            sock.close()

    def ping(self):
        return self._command(b"PING") == "PONG"

    def version(self):
        """`ClamAV <engine>/<database version>/<database date>`."""
        return self._command(b"VERSION")

    def scan_stream(self, stream):
        """Scan a binary file object; see the module docstring for results."""
        sock = self._connect()
        try:
            try:
                sock.sendall(b"zINSTREAM\0")
                while True:
                    block = stream.read(CHUNK)
                    if not block:
                        break
                    sock.sendall(struct.pack(">I", len(block)) + block)
                sock.sendall(struct.pack(">I", 0))
            except socket.timeout:
                raise ScanUnavailable("clamd did not accept the stream in time")
            except OSError:
                # clamd closes the stream early (size limit); its reply says why.
                pass
            reply = self._reply(sock)
        finally:
            sock.close()
        return interpret(reply)

    def scan_file(self, path):
        with open(path, "rb") as handle:
            return self.scan_stream(handle)


_VERSION_RE = re.compile(r"^ClamAV\s+([^/\s]+)(?:/(\d+)/(.+))?$")


def parse_version(reply):
    """{"engine", "database", "database_time"} of a VERSION answer such as
    `ClamAV 1.4.6/28177/Sun Nov  8 06:23:52 2026`. The database time is read as
    UTC (clamd prints it in its own time zone; a day's accuracy is enough to
    tell stale definitions). Missing parts are None."""
    match = _VERSION_RE.match((reply or "").strip())
    if not match:
        return {"engine": None, "database": None, "database_time": None}
    engine, database, stamp = match.groups()
    when = None
    if stamp:
        try:
            when = calendar.timegm(time.strptime(" ".join(stamp.split()), "%a %b %d %H:%M:%S %Y"))
        except ValueError:
            when = None
    return {"engine": engine, "database": int(database) if database else None,
            "database_time": when}


def interpret(reply):
    """Map one clamd INSTREAM reply to a result (or raise)."""
    if not reply:
        raise ScanUnavailable("clamd closed the connection without an answer")
    text = reply
    if text.startswith("stream:"):
        text = text[len("stream:"):].strip()
    if text == "OK":
        return "clean", None
    if text.endswith(" FOUND"):
        return "infected", text[:-len(" FOUND")].strip() or "unknown"
    if text.endswith("ERROR"):
        raise ScanError(text)
    raise ScanError("unexpected clamd reply: %s" % text[:200])
