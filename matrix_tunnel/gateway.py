"""Matrix tunnel gateway: the ViVeSecBox proxy tunnel -> the standalone adapter.

On a shared DGX Spark the Matrix AI is not paired with the ViVeSecBox; the
ViVeSecBox only embeds its UI and relays the UI's API calls under
`/api/v1/matrix/` over its proxy tunnel. The path on the box:

    ViVeSecBox --mTLS--> tunnel front (nginx, client certificate checked against
    the ViVeSecBox CA) --loopback--> this gateway --VVS-Identity--> adapter

The gateway trusts the `VVS-User` header only on a request the front marked
with the shared forward key and a verified client certificate. It turns that
identity into the box's own identity token (auth_service `/auth/tunnel/token`:
only an administrator-mapped user gets one, or one the auth service creates)
and relays the call to the
adapter's `/api/v1/ui/` API with the headers the web login gate would send.
`VVS-Permissions` naming `owner` (the ViVeSecBox owner's requests) is passed on
as `owner`: the auth service may then make that user an administrator. The
header itself never reaches the adapter.
The adapter itself stays in standalone mode with ADAPTER_IDENTITY_MODE=require:
the tunnel adds no new trust to it.

  /api/v1/matrix/<path>  ->  adapter /api/v1/ui/<path>
  /api/v1/matrix/status  ->  adapter /api/v1/status
  /api/v1/matrix/init    ->  200 when the identity may use the box, else 400
  /api/v1/matrix/end     ->  200: the ViVeSecBox closed the session (its token is dropped)
  /api/v1/matrix/auth/admin/<action>
                         ->  auth service /auth/admin/<action> (the admin page's users,
                             directory, clock and sign-in events), POST only. The
                             gateway sets `vvs_user` and `client`; the auth service
                             acts as the local user that identity is mapped to and
                             refuses it without the admin role.
  /api/v1/matrix/app/manifest, /api/v1/matrix/app/bundle/<sha256>
                         ->  the UI the Matrix bootloader loads (MATRIX_TUNNEL_APP_DIR:
                             manifest.json + bundle.json from scripts/matrix-bundle.mjs)

The app files need the front's checks but no identity: they are the UI code,
the same for everybody, and the bootloader loads them before anything else.

The ViVeSecBox's `VVS-Drive` names one of its drives, not a room of this box,
so it is ignored: the active room is the first room the user's token grants.

Settings (environment): MATRIX_TUNNEL_HOST (loopback only, default 127.0.0.1),
MATRIX_TUNNEL_PORT (18476), MATRIX_TUNNEL_FORWARD_KEY_FILE (required),
MATRIX_ADAPTER_URL (http://127.0.0.1:8088), MATRIX_AUTH_URL
(http://127.0.0.1:8097), AUTH_SERVICE_KEY_FILE and AUTH_TUNNEL_KEY_FILE
(required), MATRIX_TUNNEL_TIMEOUT_SECONDS (330), MATRIX_TUNNEL_APP_DIR (empty:
no app files, 404). Stdlib only.
"""
import base64
import hmac
import http.client
import json
import os
import re
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PREFIX = "/api/v1/matrix/"
ADAPTER_PREFIX = "/api/v1/ui/"
APP_PREFIX = PREFIX + "app/"
AUTH_ADMIN_PREFIX = PREFIX + "auth/admin/"
# What the admin page may ask the authentication service (the same list as the
# web login gate, src/lib/auth/gate.server.ts ADMIN_ACTIONS).
AUTH_ADMIN_ACTIONS = frozenset(("users", "users/create", "users/update", "users/delete",
                                "users/reset-totp", "users/unlock", "users/reset-password",
                                "events", "system", "directory", "directory/map",
                                "directory/check"))
# Fields only the gateway may set on an administrative call.
AUTH_ADMIN_RESERVED = ("session", "vvs_user", "vvs_session", "client")
MAX_ADMIN_BODY = 64 * 1024
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
FORWARD_KEY_HEADER = "X-Matrix-Forward-Key"
CLIENT_VERIFY_HEADER = "X-Matrix-Client-Verify"
PEER_HEADER = "X-Matrix-Peer"
PATH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,255}$")
# Request headers the UI may send through the tunnel; everything else is dropped.
REQUEST_HEADERS = ("content-type", "accept", "accept-language", "x-store-path",
                   "x-store-overwrite", "x-template-name")
HOP_BY_HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te",
              "trailer", "trailers", "transfer-encoding", "upgrade"}
TOKEN_MARGIN_SECONDS = 30
REFUSAL_CACHE_SECONDS = 30
MAX_CACHE_ENTRIES = 5000
CHUNK = 64 * 1024


class ConfigError(Exception):
    pass


def is_loopback(host):
    host = (host or "").strip().lower()
    return host in ("localhost", "::1") or host.startswith("127.")


def read_key(path, what):
    try:
        with open(path, "rb") as handle:
            key = handle.read().strip()
    except OSError as e:
        raise ConfigError("cannot read the %s %s: %s" % (what, path, e.strerror))
    if len(key) < 32:
        raise ConfigError("the %s %s is shorter than 32 bytes" % (what, path))
    return key


class Settings:
    def __init__(self, env):
        def text(name, default=""):
            return (env.get(name) or "").strip() or default

        self.host = text("MATRIX_TUNNEL_HOST", "127.0.0.1")
        if not is_loopback(self.host):
            raise ConfigError("MATRIX_TUNNEL_HOST=%r is not a loopback address" % self.host)
        try:
            self.port = int(text("MATRIX_TUNNEL_PORT", "18476"))
            self.timeout = int(text("MATRIX_TUNNEL_TIMEOUT_SECONDS", "330"))
        except ValueError:
            raise ConfigError("MATRIX_TUNNEL_PORT and MATRIX_TUNNEL_TIMEOUT_SECONDS must be numbers")
        self.adapter = urllib.parse.urlsplit(text("MATRIX_ADAPTER_URL", "http://127.0.0.1:8088"))
        self.auth = urllib.parse.urlsplit(text("MATRIX_AUTH_URL", "http://127.0.0.1:8097"))
        for name, url in (("MATRIX_ADAPTER_URL", self.adapter), ("MATRIX_AUTH_URL", self.auth)):
            if url.scheme != "http" or not is_loopback(url.hostname):
                raise ConfigError("%s must be a loopback http:// URL" % name)
        files = {}
        for name in ("MATRIX_TUNNEL_FORWARD_KEY_FILE", "AUTH_SERVICE_KEY_FILE", "AUTH_TUNNEL_KEY_FILE"):
            files[name] = text(name)
            if not files[name]:
                raise ConfigError("%s is required" % name)
        self.forward_key = read_key(files["MATRIX_TUNNEL_FORWARD_KEY_FILE"], "forward key")
        self.service_key = read_key(files["AUTH_SERVICE_KEY_FILE"], "auth service key")
        self.tunnel_key = read_key(files["AUTH_TUNNEL_KEY_FILE"], "auth tunnel key")
        self.app_dir = text("MATRIX_TUNNEL_APP_DIR")


def encode_drive(path):
    """`VVS-Drive` as the adapter reads it: urlsafe base64 of the UTF-8 path."""
    return base64.urlsafe_b64encode(path.encode("utf-8")).decode("ascii")


def is_owner(permissions):
    """The ViVeSecBox sends `VVS-Permissions: write,owner` on the requests of
    the user who owns it; that user may administer the box (auth service
    AUTH_TUNNEL_OWNER_ADMIN)."""
    return "owner" in {part.strip().lower() for part in (permissions or "").split(",")}


def adapter_path(raw_path):
    """The adapter path for a tunnel path (with its query), or None."""
    path, _, query = raw_path.partition("?")
    if not path.startswith(PREFIX):
        return None
    rest = path[len(PREFIX):].rstrip("/")
    if not PATH_RE.match(rest) or any(part in ("", ".", "..") for part in rest.split("/")):
        return None
    if rest == "status":
        return "/api/v1/status"
    return ADAPTER_PREFIX + rest + ("?" + query if query else "")


def app_file(app_dir, path):
    """(status, body, content type, cache control) of an app path. The bundle is
    only served under the digest the current manifest names."""
    if not app_dir:
        return 404, None, None, None
    rest = path.split("?", 1)[0][len(APP_PREFIX):].rstrip("/")
    try:
        with open(os.path.join(app_dir, "manifest.json"), "rb") as handle:
            manifest_bytes = handle.read()
        manifest = json.loads(manifest_bytes)
    except (OSError, ValueError):
        return 503, None, None, None
    if rest == "manifest":
        return 200, manifest_bytes, "application/json; charset=utf-8", "no-store"
    digest = rest[len("bundle/"):] if rest.startswith("bundle/") else ""
    expected = (manifest.get("bundle") or {}).get("sha256")
    if not SHA256_RE.match(digest) or digest != expected:
        return 404, None, None, None
    try:
        with open(os.path.join(app_dir, "bundle.json"), "rb") as handle:
            body = handle.read()
    except OSError:
        return 503, None, None, None
    return 200, body, "application/json; charset=utf-8", "public, max-age=31536000, immutable"


def auth_post(settings, path, body):
    """(status, answer) of a call to the auth service with both keys."""
    data = json.dumps(body).encode("utf-8")
    connection = http.client.HTTPConnection(settings.auth.hostname, settings.auth.port or 80, timeout=10)
    try:
        connection.request("POST", path, data, {
            "Content-Type": "application/json",
            "X-Auth-Service-Key": settings.service_key.decode("ascii"),
            "X-Auth-Tunnel-Key": settings.tunnel_key.decode("ascii")})
        response = connection.getresponse()
        answer = json.loads(response.read() or b"{}")
        return response.status, answer if isinstance(answer, dict) else {}
    finally:
        connection.close()


class Identities:
    """Identity tokens from the auth service, cached per ViVeSecBox session
    until shortly before they expire. A refusal is remembered briefly, so an
    unmapped user cannot turn every request into an auth service call."""

    def __init__(self, settings, clock=time.time):
        self.settings = settings
        self.clock = clock
        self._lock = threading.Lock()
        self._cache = {}

    def _ask(self, vvs_user, vvs_session, owner):
        return auth_post(self.settings, "/auth/tunnel/token",
                         {"vvs_user": vvs_user, "vvs_session": vvs_session, "owner": owner})

    def get(self, vvs_user, vvs_session, owner=False):
        """(status, identity) -- identity is {token, username, drives} on 200."""
        key = (vvs_user, vvs_session, owner)
        now = self.clock()
        with self._lock:
            cached = self._cache.get(key)
            if cached and cached[0] > now:
                return cached[1], cached[2]
        try:
            status, answer = self._ask(vvs_user, vvs_session, owner)
        except (OSError, ValueError, http.client.HTTPException) as e:
            sys.stderr.write("[tunnel] auth service unreachable: %s\n" % e)
            return 503, None
        if status == 200 and answer.get("token"):
            user = answer.get("user") or {}
            identity = {"token": answer["token"], "username": user.get("username") or "",
                        "drives": list(user.get("drives") or [])}
            until = min(float(answer.get("expires_at") or 0) - TOKEN_MARGIN_SECONDS, now + 3600)
            entry = (until, 200, identity)
        elif status in (400, 403):
            # The administrator maps this id with `manage.py tunnel-map set`; the
            # refusal cache keeps this to one line per user and session in 30 s.
            sys.stderr.write("[tunnel] identity not accepted: VVS-User=%s\n" % vvs_user[:128])
            entry = (now + REFUSAL_CACHE_SECONDS, 403, None)
        else:
            sys.stderr.write("[tunnel] auth service answered %d: %s\n" % (status, answer.get("error")))
            return 503, None
        with self._lock:
            if len(self._cache) >= MAX_CACHE_ENTRIES:
                self._cache = {k: v for k, v in self._cache.items() if v[0] > now}
                if len(self._cache) >= MAX_CACHE_ENTRIES:
                    self._cache.clear()
            self._cache[key] = entry
        return entry[1], entry[2]

    def forget(self, vvs_user, vvs_session):
        with self._lock:
            for owner in (False, True):
                self._cache.pop((vvs_user, vvs_session, owner), None)

    def clear(self):
        """After an administrator changed users: every token is asked for again,
        so new roles and rooms apply at once."""
        with self._lock:
            self._cache.clear()


class Handler(BaseHTTPRequestHandler):
    server_version = "matrix-tunnel"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        sys.stderr.write("[tunnel] %s\n" % (fmt % args))

    def _json(self, status, payload):
        data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.close_connection = True
        self.end_headers()
        self.wfile.write(data)

    def _from_front(self):
        presented = (self.headers.get(FORWARD_KEY_HEADER) or "").strip().encode("utf-8")
        return (bool(presented) and hmac.compare_digest(presented, self.server.settings.forward_key)
                and self.headers.get(CLIENT_VERIFY_HEADER) == "SUCCESS")

    def _handle(self):
        if not self._from_front():
            # Only the tunnel front may call: a request that did not pass the
            # ViVeSecBox client certificate check must never carry an identity.
            self._json(403, {"ok": False, "error": "forbidden"})
            return
        target = adapter_path(self.path)
        if target is None:
            self._json(404, {"ok": False, "error": "not found"})
            return
        if self.path.startswith(APP_PREFIX):
            self._app()
            return
        if target == "/api/v1/status":
            # The ViVeSecBox polls it without a user (presence, licence), and the
            # adapter answers it to anybody: no identity is needed or passed on.
            self._relay(target, None, None, None)
            return
        if target.split("?", 1)[0] == ADAPTER_PREFIX + "end":
            # The ViVeSecBox closes a frame socket session: drop its cached token.
            self.server.identities.forget((self.headers.get("VVS-User") or "").strip(),
                                          (self.headers.get("VVS-Session") or "").strip()[:256])
            self._json(200, {"ok": True})
            return
        init = target.split("?", 1)[0] == ADAPTER_PREFIX + "init"
        vvs_user = (self.headers.get("VVS-User") or "").strip()
        vvs_session = (self.headers.get("VVS-Session") or "").strip()[:256]
        if not vvs_user:
            self._json(400 if init else 401, {"ok": False, "accepted": False, "error": "no identity"})
            return
        status, identity = self.server.identities.get(vvs_user, vvs_session,
                                                      is_owner(self.headers.get("VVS-Permissions")))
        if identity is None:
            if init:
                self._json(400, {"ok": False, "accepted": False, "error": "identity not accepted"})
            elif status == 503:
                self._json(503, {"ok": False, "error": "sign-in unavailable"})
            else:
                self._json(403, {"ok": False, "error": "identity not accepted"})
            return
        path = self.path.split("?", 1)[0]
        if path.startswith(AUTH_ADMIN_PREFIX):
            self._auth_admin(path[len(AUTH_ADMIN_PREFIX):], vvs_user)
            return
        self._relay(target, identity, vvs_user, vvs_session)

    def _auth_admin(self, action, vvs_user):
        if action not in AUTH_ADMIN_ACTIONS:
            self._json(404, {"ok": False, "error": "not_found"})
            return
        if self.command != "POST":
            self._json(405, {"ok": False, "error": "method_not_allowed"})
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if length < 0 or length > MAX_ADMIN_BODY or self.headers.get("Transfer-Encoding"):
            self._json(413 if length > MAX_ADMIN_BODY else 400, {"ok": False, "error": "bad_request"})
            return
        try:
            body = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
        except (ValueError, UnicodeDecodeError):
            body = None
        if not isinstance(body, dict):
            self._json(400, {"ok": False, "error": "bad_request"})
            return
        for name in AUTH_ADMIN_RESERVED:
            body.pop(name, None)
        body["vvs_user"] = vvs_user
        body["client"] = (self.headers.get(PEER_HEADER) or "tunnel").strip()[:64]
        try:
            status, answer = auth_post(self.server.settings, "/auth/admin/" + action, body)
        except (OSError, ValueError, http.client.HTTPException) as e:
            sys.stderr.write("[tunnel] auth service unreachable: %s\n" % e)
            self._json(503, {"ok": False, "error": "auth_unavailable"})
            return
        if status == 200 and (action.startswith("users/") or action == "directory/map"):
            self.server.identities.clear()
        self._json(status, answer)

    def _app(self):
        if self.command != "GET":
            self._json(405, {"ok": False, "error": "method not allowed"})
            return
        status, body, content_type, cache = app_file(self.server.settings.app_dir, self.path)
        if body is None:
            self._json(status, {"ok": False, "error": "not found" if status == 404 else "app unavailable"})
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        self.send_header("Connection", "close")
        self.close_connection = True
        self.end_headers()
        self.wfile.write(body)

    def _relay(self, target, identity, vvs_user, vvs_session):
        headers = {name: value for name, value in self.headers.items() if name.lower() in REQUEST_HEADERS}
        headers["X-Forwarded-For"] = (self.headers.get(PEER_HEADER) or "tunnel").strip()[:64]
        if identity is not None:
            headers.update({"VVS-Identity": identity["token"], "VVS-User": identity["username"]})
            if identity["drives"]:
                headers["VVS-Drive"] = encode_drive(identity["drives"][0])
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            self._json(400, {"ok": False, "error": "bad length"})
            return
        if self.headers.get("Transfer-Encoding"):
            self._json(411, {"ok": False, "error": "length required"})
            return
        settings = self.server.settings
        upstream = http.client.HTTPConnection(settings.adapter.hostname, settings.adapter.port or 80,
                                              timeout=settings.timeout)
        try:
            upstream.putrequest(self.command, target, skip_accept_encoding=True)
            for name, value in headers.items():
                upstream.putheader(name, value)
            upstream.putheader("Content-Length", str(length))
            upstream.endheaders()
            remaining = length
            while remaining > 0:
                chunk = self.rfile.read(min(CHUNK, remaining))
                if not chunk:
                    raise ConnectionError("the request body ended early")
                upstream.send(chunk)
                remaining -= len(chunk)
            response = upstream.getresponse()
        except (OSError, http.client.HTTPException) as e:
            sys.stderr.write("[tunnel] adapter unreachable: %s\n" % e)
            self._json(502, {"ok": False, "error": "service unavailable"})
            upstream.close()
            return
        try:
            if response.status == 401 and identity is not None:
                # The adapter refused the token (rotated key, clock): ask again next time.
                self.server.identities.forget(vvs_user, vvs_session)
            self.send_response(response.status)
            for name, value in response.getheaders():
                if name.lower() not in HOP_BY_HOP and name.lower() not in ("content-length", "server", "date"):
                    self.send_header(name, value)
            declared = response.getheader("Content-Length")
            if declared is not None:
                self.send_header("Content-Length", declared)
            self.send_header("Connection", "close")
            self.close_connection = True
            self.end_headers()
            while True:
                chunk = response.read(CHUNK)
                if not chunk:
                    break
                self.wfile.write(chunk)
        except OSError:
            pass
        finally:
            upstream.close()

    do_GET = do_POST = _handle

    def _not_allowed(self):
        self._json(405, {"ok": False, "error": "method not allowed"})

    do_PUT = do_DELETE = do_PATCH = do_HEAD = _not_allowed


class Gateway(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, settings, identities=None):
        self.settings = settings
        self.identities = identities or Identities(settings)
        super().__init__((settings.host, settings.port), Handler)


def main():
    try:
        settings = Settings(os.environ)
        server = Gateway(settings)
    except (ConfigError, OSError) as e:
        sys.stderr.write("[tunnel] not starting: %s\n" % e)
        return 2
    sys.stderr.write("[tunnel] listening on %s:%d -> adapter %s\n"
                     % (settings.host, settings.port, settings.adapter.geturl()))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
