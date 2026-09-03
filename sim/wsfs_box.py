"""ViVeSecBox side of the ws-fs save channel (aibox_more3 sec 1).

The real box connects to the AIBox and receives the generated documents; this
is the dev stand-in for it. Received files land under `sim/drives/<drive>/` —
the same tree `vivesecbox_sim.py` syncs INTO the box — so on the dev machine a
"Save to drive" in the UI becomes a real file you can open.

    python sim/wsfs_box.py                     # against 127.0.0.1:8088
    python sim/wsfs_box.py --adapter http://192.168.0.181:8088
    python sim/wsfs_box.py --reject permission # exercise the download fallback
    python sim/wsfs_box.py --reject temporary  # exercise the retry path

Pure stdlib; the RFC6455 codec is reused from the adapter (adapter/wsfs.py), so
the two ends cannot drift apart.
"""
import argparse
import base64
import os
import re
import socket
import sys
import threading
import time
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "adapter"))

import wsfs  # noqa: E402

DRIVES_ROOT = os.path.join(HERE, "drives")
STORAGE_PREFIX = "/storage/drives"
_UNSAFE = re.compile(r"[^A-Za-z0-9._ ()\[\]\-\u00C0-\u024F\u0400-\u04FF]+")


def log(msg):
    sys.stdout.write("[box] %s %s\n" % (time.strftime("%H:%M:%S"), msg))
    sys.stdout.flush()


def connect(url, timeout=15):
    """HTTP upgrade to the adapter's ws-fs endpoint -> (socket, reader)."""
    parts = urllib.parse.urlsplit(url)
    host = parts.hostname or "127.0.0.1"
    port = parts.port or (443 if parts.scheme == "https" else 80)
    sock = socket.create_connection((host, port), timeout=timeout)
    key = base64.b64encode(os.urandom(16)).decode("ascii")
    req = ("GET /api/v1/ws/fs HTTP/1.1\r\nHost: %s:%d\r\n"
           "Upgrade: websocket\r\nConnection: Upgrade\r\n"
           "Sec-WebSocket-Key: %s\r\nSec-WebSocket-Version: 13\r\n\r\n"
           % (host, port, key))
    sock.sendall(req.encode("ascii"))
    buf = b""
    while b"\r\n\r\n" not in buf:
        chunk = sock.recv(4096)
        if not chunk:
            raise RuntimeError("ws handshake failed: connection closed")
        buf += chunk
    status = buf.split(b"\r\n", 1)[0].decode("latin-1")
    if "101" not in status:
        raise RuntimeError("ws handshake failed: %s" % status)
    expected = wsfs.accept_key(key)
    head = buf.split(b"\r\n\r\n", 1)[0].decode("latin-1")
    if expected not in head:
        raise RuntimeError("ws handshake failed: bad Sec-WebSocket-Accept")
    sock.settimeout(None)
    return sock, sock.makefile("rb")


def drive_folder(drive):
    """Box absolute drive path -> local folder under sim/drives."""
    path = (drive or "").strip().rstrip("/")
    if path.startswith(STORAGE_PREFIX):
        path = path[len(STORAGE_PREFIX):]
    parts = [p for p in path.split("/") if p and p not in (".", "..")]
    return os.path.join(DRIVES_ROOT, *parts) if parts else DRIVES_ROOT


def target_path(drive, name, subdir):
    """Where the received file lands, with traversal stripped from the name."""
    base = (name or "").replace("\\", "/").rsplit("/", 1)[-1].strip()
    base = _UNSAFE.sub("_", base).lstrip(".") or "unnamed"
    folder = drive_folder(drive)
    if subdir:
        folder = os.path.join(folder, subdir)
    return folder, base


def box_path(drive, subdir, name):
    """The path the box reports back (what the UI shows in the toast)."""
    parts = [(drive or "").rstrip("/")]
    if subdir:
        parts.append(subdir)
    parts.append(name)
    return "/".join(p for p in parts if p)


class BoxSim(object):
    def __init__(self, sock, reader, subdir, reject, keepalive):
        self._sock = sock
        self._reader = reader
        self._subdir = subdir
        self._reject = reject
        self._keepalive = keepalive
        self._send_lock = threading.Lock()
        self._num = 0
        self._stop = threading.Event()
        self.files = 0

    def send(self, header, blob=None):
        opcode = wsfs.OP_BINARY if blob is not None else wsfs.OP_TEXT
        frame = wsfs.encode_frame(opcode, wsfs.build_message(header, blob),
                                  mask=True)
        with self._send_lock:
            self._sock.sendall(frame)

    def keepalive_loop(self):
        while not self._stop.wait(self._keepalive):
            self._num += 1
            try:
                self.send({"type": "keepalive", "num": self._num})
            except OSError:
                return

    def handle_put_file(self, header, blob):
        name = header.get("name") or "unnamed"
        ack = header.get("num")
        if self._reject:
            # The box refuses: 'permission' keeps the AIBox copy (download
            # fallback), 'temporary' makes the AIBox retry.
            log("put-file %s -> REJECTED (%s)" % (name, self._reject))
            self.send({"type": "put-file", "ack": ack, "error": self._reject})
            return
        folder, base = target_path(header.get("drive"), name, self._subdir)
        try:
            os.makedirs(folder, exist_ok=True)
            tmp = os.path.join(folder, base + ".part")
            with open(tmp, "wb") as f:
                f.write(blob)
            os.replace(tmp, os.path.join(folder, base))
        except OSError as e:
            log("put-file %s -> write failed: %s" % (name, e))
            self.send({"type": "put-file", "ack": ack, "error": "temporary"})
            return
        self.files += 1
        stored = box_path(header.get("drive"), self._subdir, base)
        log("put-file %s (%d bytes, user=%s) -> %s"
            % (base, len(blob), header.get("user"), os.path.join(folder, base)))
        self.send({"type": "put-file", "ack": ack, "path": stored})

    def serve(self):
        ka = threading.Thread(target=self.keepalive_loop, daemon=True)
        ka.start()
        try:
            while True:
                opcode, payload = wsfs.read_message(self._reader)
                if opcode == wsfs.OP_CLOSE:
                    log("adapter closed the channel")
                    return
                if opcode == wsfs.OP_PING:
                    with self._send_lock:
                        self._sock.sendall(wsfs.encode_frame(wsfs.OP_PONG,
                                                             payload, mask=True))
                    continue
                if opcode == wsfs.OP_PONG:
                    continue
                header, blob = wsfs.parse_message(payload)
                if header is None:
                    continue
                if header.get("type") == "put-file" and "num" in header:
                    self.handle_put_file(header, blob)
                elif "ack" in header:
                    continue  # our keepalive came back
                elif "num" in header:
                    self.send({"type": header.get("type"),
                               "ack": header.get("num"), "error": "type"})
        finally:
            self._stop.set()


def main():
    ap = argparse.ArgumentParser(description="ViVeSecBox ws-fs receiver (dev)")
    ap.add_argument("--adapter", default=os.environ.get("AIBOX_URL",
                                                        "http://127.0.0.1:8088"))
    ap.add_argument("--drives-root", default=None,
                    help="where received files land (default: sim/drives)")
    ap.add_argument("--subdir", default="",
                    help="sub-folder inside the drive, e.g. 'ViVeSec/generated'")
    ap.add_argument("--reject", choices=["permission", "temporary"], default=None,
                    help="refuse every put-file (to test the UI fallbacks)")
    ap.add_argument("--keepalive", type=float, default=20.0)
    ap.add_argument("--retry", type=float, default=3.0,
                    help="seconds between reconnect attempts (0 = do not retry)")
    args = ap.parse_args()

    global DRIVES_ROOT
    if args.drives_root:
        DRIVES_ROOT = os.path.abspath(args.drives_root)
    log("receiving into %s%s" % (DRIVES_ROOT,
                                 (" / " + args.subdir) if args.subdir else ""))

    while True:
        try:
            sock, reader = connect(args.adapter)
        except (OSError, RuntimeError) as e:
            log("connect to %s failed: %s" % (args.adapter, e))
            if args.retry <= 0:
                return 1
            time.sleep(args.retry)
            continue
        log("connected to %s" % args.adapter)
        sim = BoxSim(sock, reader, args.subdir, args.reject, args.keepalive)
        try:
            sim.serve()
        except (wsfs.ChannelError, OSError) as e:
            log("channel lost: %s" % e)
        except KeyboardInterrupt:
            log("stopping (%d file(s) received)" % sim.files)
            try:
                sock.sendall(wsfs.encode_frame(wsfs.OP_CLOSE, b"", mask=True))
            except OSError:
                pass
            return 0
        finally:
            try:
                sock.close()
            except OSError:
                pass
        if args.retry <= 0:
            return 0
        time.sleep(args.retry)


if __name__ == "__main__":
    sys.exit(main())
