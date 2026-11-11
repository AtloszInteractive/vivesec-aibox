"""ViVeSec AI Box authentication service (E27 S3): the HTTP front.

Internal only: it listens on loopback and answers only callers that present
the service key (`X-Auth-Service-Key`, the content of AUTH_SERVICE_KEY_FILE).
Its one caller is the web UI server, which turns the answers into the browser
session cookie and hands the identity tokens to the adapter.

Every endpoint but the health check is `POST` with a JSON object; the answer is
`{"ok": true, ...}` or `{"ok": false, "error": "<code>"}` with a matching HTTP
status. Request bodies (passwords, codes, session secrets) are never logged.

  GET  /auth/health                                        -> service, directory (off | ready | invalid)
  POST /auth/login          {username, password, client?}  -> step, challenge[, totp_secret, otpauth_uri]
  POST /auth/login/totp     {challenge, code, client?}     -> session, session_id, expires_at, user
  POST /auth/me             {session}                      -> user, password_change_required, password_min_length
  POST /auth/token          {session}                      -> token, expires_at, user
  POST /auth/password       {session, current_password, new_password, client?}
  POST /auth/logout         {session, client?}
  POST /auth/admin/users                {session}
  POST /auth/admin/users/create         {session, username, password, display_name?, roles?, groups?, drives?, temporary?}
  POST /auth/admin/users/update         {session, username, display_name?, roles?, groups?, drives?, status?}
  POST /auth/admin/users/delete         {session, username}
  POST /auth/admin/users/reset-totp     {session, username}
  POST /auth/admin/users/unlock         {session, username}
  POST /auth/admin/users/reset-password {session, username, password, temporary?}
  POST /auth/admin/events               {session, limit?, username?}

`client` is the browser's address as the web UI server saw it; it feeds the
per-client throttle and the event log.

A directory user's login or session answers `directory_unavailable` (503)
when Active Directory cannot be asked (E27 S6, `directory.py`); password,
role and room changes of a directory user answer `managed_by_directory`.
"""
import hmac
import json
import os
import socket
import sys
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import auth  # noqa: E402
import config  # noqa: E402
import store as store_module  # noqa: E402
import tokens  # noqa: E402

KEY_HEADER = "X-Auth-Service-Key"
MAX_BODY_BYTES = 64 * 1024
MAX_CLIENT_LENGTH = 64
REQUEST_TIMEOUT_SECONDS = 30


def _admin(handler):
    def route(authenticator, body, client):
        actor = authenticator.admin_actor(body.get("session"))
        return handler(authenticator, body, client, actor)
    return route


def _flag(body, name, default):
    value = body.get(name, default)
    if not isinstance(value, bool):
        raise auth.AuthError("invalid_" + name, 400)
    return value


ROUTES = {
    "/auth/login": lambda a, b, c: a.login(b.get("username"), b.get("password"), c),
    "/auth/login/totp": lambda a, b, c: a.complete_login(b.get("challenge"), b.get("code"), c),
    "/auth/me": lambda a, b, c: a.me(b.get("session")),
    "/auth/token": lambda a, b, c: a.issue_token(b.get("session")),
    "/auth/password": lambda a, b, c: a.change_password(
        b.get("session"), b.get("current_password"), b.get("new_password"), c),
    "/auth/logout": lambda a, b, c: a.logout(b.get("session"), c),
    "/auth/admin/users": _admin(lambda a, b, c, actor: {"users": a.list_users()}),
    "/auth/admin/users/create": _admin(lambda a, b, c, actor: {"user": a.create_user(
        actor, b.get("username"), b.get("password"), b.get("display_name"),
        b.get("roles") or [], b.get("groups") or [], b.get("drives") or [],
        _flag(b, "temporary", True), c)}),
    "/auth/admin/users/update": _admin(lambda a, b, c, actor: {"user": a.update_user(
        actor, b.get("username"), b.get("display_name"), b.get("roles"), b.get("groups"),
        b.get("drives"), b.get("status"), c)}),
    "/auth/admin/users/delete": _admin(lambda a, b, c, actor: a.delete_user(actor, b.get("username"), c)),
    "/auth/admin/users/reset-totp": _admin(lambda a, b, c, actor: {"user": a.reset_totp(
        actor, b.get("username"), c)}),
    "/auth/admin/users/unlock": _admin(lambda a, b, c, actor: {"user": a.unlock(
        actor, b.get("username"), c)}),
    "/auth/admin/users/reset-password": _admin(lambda a, b, c, actor: {"user": a.reset_password(
        actor, b.get("username"), b.get("password"), _flag(b, "temporary", True), c)}),
    "/auth/admin/events": _admin(lambda a, b, c, actor: {"events": a.list_events(
        b.get("limit") if isinstance(b.get("limit"), int) else 200, b.get("username"))}),
}


class Handler(BaseHTTPRequestHandler):
    server_version = "aibox-auth"
    sys_version = ""
    protocol_version = "HTTP/1.1"
    timeout = REQUEST_TIMEOUT_SECONDS

    def log_message(self, fmt, *args):
        sys.stderr.write("[auth] %s %s\n" % (self.address_string(), fmt % args))

    def _send(self, status, payload, close=False):
        data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        if close:
            self.send_header("Connection", "close")
            self.close_connection = True
        self.end_headers()
        self.wfile.write(data)

    def _authorised(self):
        presented = (self.headers.get(KEY_HEADER) or "").strip().encode("utf-8")
        return bool(presented) and hmac.compare_digest(presented, self.server.service_key)

    def do_GET(self):
        if self.path == "/auth/health":
            self._send(200, {"ok": True, "service": "auth",
                             "directory": self.server.authenticator.directory.state})
        else:
            self._send(404, {"ok": False, "error": "not_found"})

    def do_POST(self):
        if not self._authorised():
            self._send(401, {"ok": False, "error": "service_key"}, close=True)
            return
        try:
            length = int(self.headers.get("Content-Length") or "")
        except ValueError:
            self._send(411, {"ok": False, "error": "length_required"}, close=True)
            return
        if length < 0 or length > MAX_BODY_BYTES:
            self._send(413, {"ok": False, "error": "body_too_large"}, close=True)
            return
        try:
            body = json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            body = None
        if not isinstance(body, dict):
            self._send(400, {"ok": False, "error": "bad_request"})
            return
        route = ROUTES.get(self.path)
        if route is None:
            self._send(404, {"ok": False, "error": "not_found"})
            return
        client = body.get("client")
        client = client.strip()[:MAX_CLIENT_LENGTH] if isinstance(client, str) else ""
        try:
            result = route(self.server.authenticator, body, client)
        except auth.AuthError as e:
            self._send(e.status, {"ok": False, "error": e.code})
            return
        except Exception:  # noqa: BLE001 - the details go to the log, not to the caller
            sys.stderr.write("[auth] error in %s\n%s" % (self.path, traceback.format_exc()))
            self._send(500, {"ok": False, "error": "internal"})
            return
        self._send(200, dict({"ok": True}, **(result or {})))

    def _not_allowed(self):
        self._send(405, {"ok": False, "error": "method_not_allowed"}, close=True)

    do_PUT = do_DELETE = do_PATCH = _not_allowed


class AuthServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, authenticator, service_key):
        self.authenticator = authenticator
        self.service_key = service_key
        if ":" in address[0]:
            self.address_family = socket.AF_INET6
        super().__init__(address, Handler)


def open_service(settings):
    """The authenticator for `settings`, or tokens.KeyFileError when the key
    files are missing (run `manage.py init` first)."""
    jwt_secret = tokens.read_key(settings.jwt_secret_file, "token signing key")
    return auth.Authenticator(store_module.Store(settings.db_path), settings, jwt_secret)


def main():
    try:
        settings = config.Settings.from_env()
        service_key = tokens.read_key(settings.service_key_file, "service key")
        authenticator = open_service(settings)
        server = AuthServer((settings.host, settings.port), authenticator, service_key)
    except (config.ConfigError, tokens.KeyFileError, OSError) as e:
        sys.stderr.write("[auth] not starting: %s\n" % e)
        return 2
    sys.stderr.write("[auth] listening on %s:%d\n" % (settings.host, settings.port))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
