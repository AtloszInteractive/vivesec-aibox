"""WS /api/v1/ws/fs — the AIBox->ViVeSecBox file-save channel (aibox_more3 §1).

The ViVeSecBox CONNECTS TO US (we are the WebSocket server) automatically while
the status reports fs_ready=true, and keeps the connection alive. Over that
single multiplexed connection:

  * the box sends {"type":"keepalive","num":N} periodically -> we reply
    {"type":"keepalive","ack":N};
  * WE initiate {"type":"put-file","num":N,"user":U,"drive":D,"name":F}
    followed by a newline and the raw file bytes IN THE SAME (binary) message;
    the box replies {"type":"put-file","ack":N,"path":...} on success,
    {"ack":N,"error":"permission"} (keep the session copy + download fallback)
    or {"ack":N,"error":"temporary"} (retry with delay);
  * unknown incoming types get {"type":T,"ack":N,"error":"type"};
  * messages whose basic fields (type + num/ack) cannot be parsed get NO reply
    (spec).

Line protocol: compact JSON (no newline inside) + optional b"\n" + arbitrary
binary payload, carried in TEXT or BINARY frames interchangeably.

Pure stdlib (RFC6455 subset: no extensions, no subprotocols); the frame codec
supports client-side masking too so the smoke test can act as the box.
"""
import base64
import hashlib
import json
import os
import struct
import threading
import time

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

OP_CONT = 0x0
OP_TEXT = 0x1
OP_BINARY = 0x2
OP_CLOSE = 0x8
OP_PING = 0x9
OP_PONG = 0xA

_CONTROL = (OP_CLOSE, OP_PING, OP_PONG)


class ChannelError(Exception):
    """Transport-level failure on the ws-fs channel (down/timeout/protocol)."""


def accept_key(sec_websocket_key):
    """RFC6455 Sec-WebSocket-Accept for a client key."""
    digest = hashlib.sha1((sec_websocket_key.strip() + GUID).encode("ascii"))
    return base64.b64encode(digest.digest()).decode("ascii")


def encode_frame(opcode, payload, mask=False):
    """One complete (FIN) frame. Servers send unmasked; the test client (acting
    as the ViVeSecBox) masks, as RFC6455 requires from clients."""
    payload = payload or b""
    head = bytearray([0x80 | (opcode & 0x0F)])
    n = len(payload)
    mask_bit = 0x80 if mask else 0x00
    if n < 126:
        head.append(mask_bit | n)
    elif n <= 0xFFFF:
        head.append(mask_bit | 126)
        head += struct.pack(">H", n)
    else:
        head.append(mask_bit | 127)
        head += struct.pack(">Q", n)
    if not mask:
        return bytes(head) + payload
    key = os.urandom(4)
    masked = bytes(b ^ key[i % 4] for i, b in enumerate(payload))
    return bytes(head) + key + masked


def read_frame(rfile):
    """Read one frame from a buffered reader -> (fin, opcode, payload).
    Raises ChannelError on EOF/protocol violation."""
    hdr = rfile.read(2)
    if not hdr or len(hdr) < 2:
        raise ChannelError("connection closed")
    b0, b1 = hdr[0], hdr[1]
    fin = bool(b0 & 0x80)
    opcode = b0 & 0x0F
    masked = bool(b1 & 0x80)
    n = b1 & 0x7F
    if n == 126:
        ext = rfile.read(2)
        if len(ext) < 2:
            raise ChannelError("truncated length")
        n = struct.unpack(">H", ext)[0]
    elif n == 127:
        ext = rfile.read(8)
        if len(ext) < 8:
            raise ChannelError("truncated length")
        n = struct.unpack(">Q", ext)[0]
    key = b""
    if masked:
        key = rfile.read(4)
        if len(key) < 4:
            raise ChannelError("truncated mask")
    payload = rfile.read(n) if n else b""
    if len(payload) < n:
        raise ChannelError("truncated payload")
    if masked and payload:
        payload = bytes(b ^ key[i % 4] for i, b in enumerate(payload))
    return fin, opcode, payload


def read_message(rfile):
    """Read one logical message, assembling continuation fragments. Control
    frames may interleave -> returned as-is when complete on their own.
    Returns (opcode, payload)."""
    fin, opcode, payload = read_frame(rfile)
    if opcode in _CONTROL:
        return opcode, payload
    parts = [payload]
    while not fin:
        fin, op, chunk = read_frame(rfile)
        if op in _CONTROL:
            # Control frames may not be fragmented; surface them immediately
            # (the caller handles ping/close and calls again).
            return op, chunk
        if op != OP_CONT:
            raise ChannelError("unexpected non-continuation frame")
        parts.append(chunk)
    return opcode, b"".join(parts)


def build_message(header, blob=None):
    """Compact JSON header (+ newline + binary content when blob given)."""
    data = json.dumps(header, ensure_ascii=False,
                      separators=(",", ":")).encode("utf-8")
    if blob is None:
        return data
    return data + b"\n" + blob


def parse_message(data):
    """Split a message into (header dict | None, blob bytes). The header is
    the compact JSON before the first newline; None when unparseable (spec:
    such messages get no reply)."""
    if not data:
        return None, b""
    idx = data.find(b"\n")
    raw = data if idx < 0 else data[:idx]
    blob = b"" if idx < 0 else data[idx + 1:]
    try:
        header = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None, blob
    if not isinstance(header, dict):
        return None, blob
    return header, blob


class FsChannel:
    """One live ws-fs connection. serve() runs the read loop on the handler
    thread; put_file() is called from other (HTTP worker) threads and blocks
    until the matching ack arrives."""

    def __init__(self, rfile, wfile, on_close=None):
        self._rfile = rfile
        self._wfile = wfile
        self._on_close = on_close
        self._send_lock = threading.Lock()
        self._pending = {}          # num -> {"event": Event, "reply": dict}
        self._pending_lock = threading.Lock()
        self._num_lock = threading.Lock()
        self._num = 0
        self.closed = False

    # -- outgoing ------------------------------------------------------------
    def _next_num(self):
        with self._num_lock:
            self._num += 1
            return self._num

    def send_message(self, header, blob=None):
        opcode = OP_BINARY if blob is not None else OP_TEXT
        frame = encode_frame(opcode, build_message(header, blob))
        with self._send_lock:
            self._wfile.write(frame)
            self._wfile.flush()

    def request(self, header, blob=None, timeout=30.0):
        """Send a numbered request and wait for its ack. Returns
        (reply header, reply body); the body is empty for replies that carry
        no payload. Raises ChannelError on transport failure/timeout."""
        num = self._next_num()
        header = dict(header)
        header["num"] = num
        slot = {"event": threading.Event(), "reply": None, "blob": b""}
        with self._pending_lock:
            self._pending[num] = slot
        try:
            self.send_message(header, blob)
            if not slot["event"].wait(timeout):
                raise ChannelError("timeout waiting for ack %d" % num)
            return slot["reply"], slot["blob"]
        finally:
            with self._pending_lock:
                self._pending.pop(num, None)

    def put_file(self, user, drive, name, content, retries=3, retry_delay=1.0,
                 timeout=30.0, sleep=time.sleep):
        """The put-file client flow with the spec retry semantics. Returns the
        reply header: {"path": ...} on success, {"error": "permission"} when
        the user cannot write the drive. Retries ONLY on {"error":"temporary"};
        raises ChannelError when the transport is gone."""
        attempts = max(1, int(retries))
        reply = None
        for i in range(attempts):
            reply, _blob = self.request({"type": "put-file", "user": user,
                                         "drive": drive, "name": name},
                                        blob=content, timeout=timeout)
            if (reply or {}).get("error") != "temporary":
                return reply
            if i < attempts - 1:
                sleep(retry_delay)
        return reply

    def get_file(self, user, path, timeout=30.0):
        """The get-file client flow (docs/aibox_patch_0902.md): the box returns
        the JSON header, a newline and the file content on success, and a bare
        JSON header with `error` on failure.

        Returns (header, content). NOT retried: the error vocabulary for reads
        is not part of the contract yet, so a retry could hammer the box for a
        condition that will never clear.
        """
        reply, blob = self.request({"type": "get-file", "user": user,
                                    "path": path}, timeout=timeout)
        return (reply or {}), blob

    # -- incoming ------------------------------------------------------------
    def _resolve_ack(self, header, blob=b""):
        try:
            num = int(header.get("ack"))
        except (TypeError, ValueError):
            return
        with self._pending_lock:
            slot = self._pending.get(num)
        if slot is not None:
            slot["reply"] = header
            slot["blob"] = blob or b""
            slot["event"].set()

    def _handle_request(self, header):
        try:
            num = int(header.get("num"))
        except (TypeError, ValueError):
            return  # spec: unparseable basic fields -> no response
        mtype = header.get("type")
        if not isinstance(mtype, str) or not mtype:
            return
        if mtype == "keepalive":
            self.send_message({"type": "keepalive", "ack": num})
        else:
            self.send_message({"type": mtype, "ack": num, "error": "type"})

    def serve(self):
        """Blocking read loop; returns when the peer closes/drops."""
        try:
            while True:
                opcode, payload = read_message(self._rfile)
                if opcode == OP_CLOSE:
                    with self._send_lock:
                        try:
                            self._wfile.write(encode_frame(OP_CLOSE, payload[:2]))
                            self._wfile.flush()
                        except Exception:  # noqa: BLE001
                            pass
                    break
                if opcode == OP_PING:
                    with self._send_lock:
                        self._wfile.write(encode_frame(OP_PONG, payload))
                        self._wfile.flush()
                    continue
                if opcode == OP_PONG:
                    continue
                header, blob = parse_message(payload)
                if header is None:
                    continue  # spec: no response
                if "ack" in header:
                    self._resolve_ack(header, blob)
                elif "num" in header:
                    self._handle_request(header)
                # neither num nor ack -> unparseable basic fields -> silence
        except (ChannelError, OSError):
            # An abrupt box disconnect (reset/timeout) is a normal end of life
            # for the channel, not a server error.
            pass
        finally:
            self.close()

    def close(self):
        if self.closed:
            return
        self.closed = True
        # Fail every in-flight request so callers do not hang.
        with self._pending_lock:
            for slot in self._pending.values():
                slot["reply"] = {"error": "channel-closed"}
                slot["blob"] = b""
                slot["event"].set()
        if self._on_close is not None:
            try:
                self._on_close(self)
            except Exception:  # noqa: BLE001
                pass


class FsChannelHub:
    """Holds the CURRENT ws-fs connection (the box keeps one alive; a newer
    connection replaces the older one)."""

    def __init__(self):
        self._lock = threading.Lock()
        self._channel = None

    def attach(self, channel):
        with self._lock:
            old, self._channel = self._channel, channel
        if old is not None and not old.closed:
            old.close()

    def detach(self, channel):
        with self._lock:
            if self._channel is channel:
                self._channel = None

    def get(self):
        with self._lock:
            ch = self._channel
        return ch if (ch is not None and not ch.closed) else None

    def connected(self):
        return self.get() is not None

    def put_file(self, user, drive, name, content, retries=3, retry_delay=1.0,
                 timeout=30.0):
        ch = self.get()
        if ch is None:
            raise ChannelError("no ws-fs channel connected")
        return ch.put_file(user, drive, name, content, retries=retries,
                           retry_delay=retry_delay, timeout=timeout)

    def get_file(self, user, path, timeout=30.0):
        ch = self.get()
        if ch is None:
            raise ChannelError("no ws-fs channel connected")
        return ch.get_file(user, path, timeout=timeout)
