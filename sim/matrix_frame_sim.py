"""Stand-in for the ViVeSecBox frame socket, to try the Matrix bootloader (E07).

    sudo python3 sim/matrix_frame_sim.py --boot dist/matrix/boot --vvs-user TESZT-VVS-1

Run it on the box next to the tunnel gateway (it reads the forward key, hence
sudo) and open it through an SSH tunnel: `ssh -L 8199:127.0.0.1:8199 <box>`,
then http://localhost:8199/ (localhost is a secure context, so the bootloader
can check digests).

It serves the bootloader directory with a `frameSocketFetcher` that sends every
request to /__tunnel/<path>, and relays those to the gateway the way the tunnel
front does after a verified client certificate: the forward key,
`X-Matrix-Client-Verify: SUCCESS` and the ViVeSecBox identity headers. Like the
real frame socket it carries only Content-Type and Accept from the page, so a
UI change that relies on another header shows up here. Development only.
"""
import argparse
import http.client
import os
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

FRAME_SOCKET = (
    "<script>(function(){var ready=Date.now()+%NOT_READY_MS%;"
    "globalThis.frameSocketFetcher=function(url,init){"
    "if(Date.now()<ready)return Promise.reject(new Error('No active ParentFrameSocket'));"
    "return fetch('/__tunnel'+url,init)}})();</script>"
)
TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript", ".json": "application/json",
         ".png": "image/png", ".ico": "image/x-icon", ".webmanifest": "application/manifest+json"}
PASSED = ("content-type", "accept")


def handler_for(args, forward_key):
    gateway = urllib.parse.urlsplit(args.gateway)
    boot = os.path.realpath(args.boot)
    # Like the real frame socket, the function exists before its channel is up.
    frame_socket = FRAME_SOCKET.replace("%NOT_READY_MS%", str(args.not_ready_ms))

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *rest):
            sys.stderr.write("[frame-sim] %s\n" % (fmt % rest))

        def _send(self, status, body, content_type, extra=()):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            if args.csp and content_type.startswith("text/html"):
                self.send_header("Content-Security-Policy", args.csp)
            for name, value in extra:
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(body)

        def _static(self):
            rel = urllib.parse.unquote(self.path.split("?", 1)[0]).lstrip("/") or "index.html"
            path = os.path.realpath(os.path.join(boot, rel))
            if not path.startswith(boot + os.sep) or not os.path.isfile(path):
                self._send(404, b"not found", "text/plain")
                return
            with open(path, "rb") as handle:
                body = handle.read()
            if rel == "index.html":
                body = body.replace(b"<head>", b"<head>" + frame_socket.encode(), 1)
            self._send(200, body, TYPES.get(os.path.splitext(path)[1], "application/octet-stream"),
                       [("Cache-Control", "no-store")])

        def _tunnel(self):
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else None
            headers = {name: value for name, value in self.headers.items() if name.lower() in PASSED}
            headers.update({"X-Matrix-Forward-Key": forward_key, "X-Matrix-Client-Verify": "SUCCESS",
                            "X-Matrix-Peer": "frame-sim", "VVS-User": args.vvs_user,
                            "VVS-Session": args.vvs_session})
            # The real tunnel drops the query string of a GET.
            path = self.path[len("/__tunnel"):]
            if self.command == "GET":
                path = path.split("?", 1)[0]
            upstream = http.client.HTTPConnection(gateway.hostname, gateway.port, timeout=330)
            try:
                upstream.request(self.command, path, body, headers)
                response = upstream.getresponse()
                data = response.read()
                self._send(response.status, data, response.getheader("Content-Type") or "application/octet-stream")
            except OSError as e:
                self._send(502, str(e).encode(), "text/plain")
            finally:
                upstream.close()

        def do_GET(self):
            (self._tunnel if self.path.startswith("/__tunnel/") else self._static)()

        def do_POST(self):
            if self.path.startswith("/__tunnel/"):
                self._tunnel()
            else:
                self._send(405, b"", "text/plain")

    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--boot", required=True, help="dist/matrix/boot (or an unpacked boot zip)")
    parser.add_argument("--gateway", default="http://127.0.0.1:18476")
    parser.add_argument("--forward-key-file", default="/var/lib/vivesec-matrix/secrets/tunnel-forward.key")
    parser.add_argument("--vvs-user", required=True)
    parser.add_argument("--vvs-session", default="frame-sim")
    parser.add_argument("--port", type=int, default=8199)
    parser.add_argument("--not-ready-ms", type=int, default=1500,
                        help="the frame socket refuses calls this long after the page loads")
    parser.add_argument("--csp", default="style-src 'self' 'unsafe-inline'",
                        help="Content-Security-Policy of the page (the ViVeSec's, as far as known)")
    args = parser.parse_args()
    with open(args.forward_key_file, encoding="ascii") as handle:
        forward_key = handle.read().strip()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), handler_for(args, forward_key))
    sys.stderr.write("[frame-sim] http://127.0.0.1:%d/ -> %s as %s\n" % (args.port, args.gateway, args.vvs_user))
    server.serve_forever()


if __name__ == "__main__":
    main()
