"""SSDP/UPnP discovery responder for the AIBox (J6, spec sec 2.5).

So the ViVeSecBox can find the AIBox on the local network, the box answers SSDP
M-SEARCH probes and periodically multicasts ssdp:alive NOTIFY announcements for
its service type (default ``urn:vivesecbox-com:device:AIBox:1``). The reply/notify
carry a LOCATION pointing at the adapter's status endpoint and a stable USN so
the ViVeSecBox can correlate the device across restarts.

The message builders (``build_search_response`` / ``build_notify`` / ``matches``)
are pure so they can be unit-tested without real multicast sockets; ``run()`` and
``announce()`` do the actual UDP I/O on a daemon thread.

Container note: the adapter runs with ``--network host`` on the Jetson, so the
multicast join happens on the host LAN where the ViVeSecBox lives.

Pure stdlib (socket, struct), Py3.8+.
"""
import os
import socket
import struct
import sys
import threading
import time
import uuid as _uuid

SSDP_ADDR = "239.255.255.250"
SSDP_PORT = 1900
DEFAULT_ST = "urn:vivesecbox-com:device:AIBox:1"
ROOTDEVICE = "upnp:rootdevice"
SERVER_STRING = "ViVeSec-AIBox/1.0 UPnP/1.0 adapter"


def local_ip(default="127.0.0.1"):
    """Best-effort LAN IP of this host (no traffic actually sent)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("203.0.113.1", 9))  # TEST-NET-3, never routed
        return s.getsockname()[0]
    except Exception:  # noqa: BLE001
        return default
    finally:
        s.close()


def stable_uuid(persist_path=None):
    """Return a stable device UUID, persisting it next to the PKI/data so the
    USN survives restarts. Falls back to a process-random UUID if no path."""
    if persist_path:
        try:
            if os.path.exists(persist_path):
                with open(persist_path) as f:
                    val = f.read().strip()
                if val:
                    return val
            os.makedirs(os.path.dirname(persist_path) or ".", exist_ok=True)
            val = str(_uuid.uuid4())
            tmp = persist_path + ".tmp"
            with open(tmp, "w") as f:
                f.write(val + "\n")
            os.replace(tmp, persist_path)
            return val
        except OSError:
            pass
    return str(_uuid.uuid4())


class SsdpResponder:
    def __init__(self, st=DEFAULT_ST, usn=None, location="", server=SERVER_STRING,
                 max_age=1800, group=SSDP_ADDR, port=SSDP_PORT, sock_factory=None):
        self.st = st
        # The ViVeSecBox stores the *root-device* USN, so the search reply must
        # carry `<udn>::upnp:rootdevice` (justssdp behaviour) while NOTIFY keeps
        # the device-type form. Accept a full USN for backwards compatibility
        # and keep only its UDN part.
        self.udn = (usn or "uuid:%s" % _uuid.uuid4()).split("::", 1)[0]
        self.usn = "%s::%s" % (self.udn, ROOTDEVICE)
        self.location = location
        self.server = server
        self.max_age = int(max_age)
        self.group = group
        self.port = int(port)
        self._sock_factory = sock_factory
        self._stop = threading.Event()

    # -- pure message helpers (unit-testable, no I/O) -----------------------
    def matches(self, st_header):
        """True if an M-SEARCH ST targets us (our type, all devices, or the
        root-device alias)."""
        st = (st_header or "").strip()
        return st in (self.st, "ssdp:all", "upnp:rootdevice")

    def build_search_response(self, st=None):
        """Unicast HTTP reply to an M-SEARCH (sent back to the prober). The ST is
        echoed back exactly as asked, as the reference justssdp server does."""
        lines = [
            "HTTP/1.1 200 OK",
            "CACHE-CONTROL: max-age=%d" % self.max_age,
            "EXT: ",
            "LOCATION: %s" % self.location,
            "SERVER: %s" % self.server,
            "ST: %s" % (st or self.st),
            "USN: %s" % self.usn,
            "", "",
        ]
        return ("\r\n".join(lines)).encode("utf-8")

    def build_notify(self, nts="ssdp:alive"):
        """Multicast NOTIFY announcement (alive on start/refresh, byebye on
        shutdown)."""
        lines = [
            "NOTIFY * HTTP/1.1",
            "HOST: %s:%d" % (self.group, self.port),
            "CACHE-CONTROL: max-age=%d" % self.max_age,
            "LOCATION: %s" % self.location,
            "SERVER: %s" % self.server,
            "NT: %s" % self.st,
            "NTS: %s" % nts,
            "USN: %s::%s" % (self.udn, self.st),
            "", "",
        ]
        return ("\r\n".join(lines)).encode("utf-8")

    @staticmethod
    def parse_request(data):
        """Parse a datagram into (method, headers dict lowercased). Lenient:
        returns (None, {}) on anything that is not a recognizable HTTP request."""
        try:
            text = data.decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            return None, {}
        parts = text.split("\r\n")
        if not parts or not parts[0]:
            return None, {}
        method = parts[0].split(" ", 1)[0].upper()
        headers = {}
        for line in parts[1:]:
            if ":" in line:
                k, v = line.split(":", 1)
                headers[k.strip().lower()] = v.strip()
        return method, headers

    # -- socket I/O ----------------------------------------------------------
    def _make_socket(self):
        if self._sock_factory:
            return self._sock_factory()
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("", self.port))
        except OSError as e:
            raise OSError("SSDP bind on :%d failed: %s" % (self.port, e))
        mreq = struct.pack("4sl", socket.inet_aton(self.group), socket.INADDR_ANY)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
        sock.settimeout(1.0)
        return sock

    def announce(self, sock, nts="ssdp:alive"):
        try:
            sock.sendto(self.build_notify(nts), (self.group, self.port))
        except OSError:
            pass

    def stop(self):
        self._stop.set()

    def run(self, announce_interval=900, retry_interval=5):
        """Listen for M-SEARCH and reply; re-announce ssdp:alive periodically.
        Blocks until stop(); intended to run on a daemon thread."""
        while not self._stop.is_set():
            try:
                sock = self._make_socket()
                break
            except OSError as e:
                sys.stderr.write("[adapter] SSDP unavailable, retrying: %s\n" % e)
                self._stop.wait(retry_interval)
        else:
            return
        self.announce(sock, "ssdp:alive")
        last_announce = time.time()
        while not self._stop.is_set():
            try:
                data, addr = sock.recvfrom(2048)
            except socket.timeout:
                data = None
            except OSError:
                break
            if data:
                method, headers = self.parse_request(data)
                if method == "M-SEARCH" and self.matches(headers.get("st")):
                    try:
                        sock.sendto(self.build_search_response(headers.get("st")), addr)
                    except OSError:
                        pass
            if time.time() - last_announce >= announce_interval:
                self.announce(sock, "ssdp:alive")
                last_announce = time.time()
        # Polite shutdown: tell listeners we are leaving.
        self.announce(sock, "ssdp:byebye")
        try:
            sock.close()
        except OSError:
            pass


def from_env(env=None, default_port=8080, uuid_dir=None):
    """Build an SsdpResponder from the environment, or None if disabled.

    ADAPTER_DISCOVERY        : auto|on|off (default auto = on)
    ADAPTER_DISCOVERY_ST     : service type (default urn:vivesecbox-com:device:AIBox:1)
    ADAPTER_DISCOVERY_PORT   : advertised HTTP port (default = adapter port)
    ADAPTER_DISCOVERY_LOCATION : full LOCATION URL override
    ADAPTER_DISCOVERY_MAXAGE : cache-control max-age seconds (default 1800)
    """
    env = env or os.environ
    mode = (env.get("ADAPTER_DISCOVERY", "auto") or "auto").strip().lower()
    if mode in ("off", "0", "false", "no"):
        return None
    st = env.get("ADAPTER_DISCOVERY_ST", DEFAULT_ST)
    adv_port = int(env.get("ADAPTER_DISCOVERY_PORT", str(default_port)) or default_port)
    location = env.get("ADAPTER_DISCOVERY_LOCATION", "")
    if not location:
        location = "http://%s:%d/api/v1/status" % (local_ip(), adv_port)
    max_age = int(env.get("ADAPTER_DISCOVERY_MAXAGE", "1800") or 1800)
    udir = uuid_dir or env.get("ADAPTER_PKI_DIR", "")
    persist = os.path.join(udir, "device_uuid") if udir else None
    dev_uuid = stable_uuid(persist)
    usn = "uuid:%s::%s" % (dev_uuid, ROOTDEVICE)
    return SsdpResponder(st=st, usn=usn, location=location, max_age=max_age)
