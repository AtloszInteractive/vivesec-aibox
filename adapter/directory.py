"""Group and role membership of an identity (E02).

The directory is the single source of truth for who belongs where; the AI Box
only reads it and never writes back. `ADAPTER_DIRECTORY` selects the source:

  off    -- no group or role information (the default);
  token  -- the `groups`/`roles` claims of a verified identity token, so a
            ViVeSecBox that forwards the memberships itself works regardless of
            which identity provider sits behind it;
  entra  -- Microsoft Entra ID through Microsoft Graph, with an application
            permission granted by an administrator (client credentials). The
            client secret is read from a file and never logged.

An unknown source falls back to `off`. Lookups are cached for
`ADAPTER_DIRECTORY_TTL_SECONDS`; when the directory cannot be reached and the
cached entry has expired, the result is EMPTY (with `error` set) -- a stale or
guessed membership must never grant anything.
"""
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

SOURCE_OFF = "off"
SOURCE_TOKEN = "token"
SOURCE_ENTRA = "entra"
SOURCES = (SOURCE_OFF, SOURCE_TOKEN, SOURCE_ENTRA)

DEFAULT_AUTHORITY = "https://login.microsoftonline.com"
DEFAULT_GRAPH_URL = "https://graph.microsoft.com"
# Graph pages hold at most 999 objects; a user in more than 50 pages of groups
# is a misconfiguration, not something to page through forever.
MAX_PAGES = 50
# A failed lookup is retried at most this often per user, so a directory outage
# does not turn every request into a slow network timeout.
NEGATIVE_TTL_SECONDS = 30

_ID_RE = re.compile(r"^[A-Za-z0-9.\-]+$")


class DirectoryError(Exception):
    pass


class DirectoryInfo:
    def __init__(self, source, groups=(), roles=(), group_names=None,
                 error=None, fetched_at=None):
        self.source = source
        self.groups = tuple(groups)
        self.roles = tuple(roles)
        self.group_names = dict(group_names or {})
        self.error = error
        self.fetched_at = fetched_at

    def as_dict(self):
        return {"source": self.source, "groups": list(self.groups),
                "group_names": dict(self.group_names), "roles": list(self.roles),
                "error": self.error, "fetched_at": self.fetched_at}


def parse_source(raw, warn=None):
    value = (raw or "").strip().lower()
    if not value:
        return SOURCE_OFF
    if value in SOURCES:
        return value
    if warn:
        warn("unknown ADAPTER_DIRECTORY %r -> %s" % (raw, SOURCE_OFF))
    return SOURCE_OFF


class NullDirectory:
    source = SOURCE_OFF

    def lookup(self, identity):
        return DirectoryInfo(SOURCE_OFF)

    def settings(self):
        return {"source": SOURCE_OFF}


class TokenDirectory:
    """Memberships carried by the verified token. A header-only identity has
    none: an unsigned claim of group membership is worth nothing."""

    source = SOURCE_TOKEN

    def lookup(self, identity):
        if identity is None or identity.source != "token":
            return DirectoryInfo(SOURCE_TOKEN, error="no verified identity token")
        return DirectoryInfo(SOURCE_TOKEN, identity.groups, identity.roles,
                             fetched_at=time.time())

    def settings(self):
        return {"source": SOURCE_TOKEN}


def _read_secret(path):
    if not path:
        raise DirectoryError("no client secret file configured")
    try:
        with open(path, "r", encoding="utf-8") as handle:
            secret = handle.read().strip()
    except OSError as e:
        raise DirectoryError("client secret file unreadable: %s" % e.strerror)
    if not secret:
        raise DirectoryError("client secret file is empty")
    return secret


def _default_opener(request, timeout):
    return urllib.request.urlopen(request, timeout=timeout)


class EntraDirectory:
    """Microsoft Graph lookup of transitive group membership and app roles."""

    source = SOURCE_ENTRA

    def __init__(self, tenant_id, client_id, secret_file, resource_id=None,
                 ttl=900, timeout=10, authority=DEFAULT_AUTHORITY,
                 graph_url=DEFAULT_GRAPH_URL, opener=_default_opener,
                 clock=time.time):
        self.tenant_id = (tenant_id or "").strip()
        self.client_id = (client_id or "").strip()
        self.secret_file = secret_file
        self.resource_id = (resource_id or "").strip() or None
        self.ttl = max(0, int(ttl))
        self.timeout = max(1, float(timeout))
        self.authority = (authority or DEFAULT_AUTHORITY).rstrip("/")
        self.graph_url = (graph_url or DEFAULT_GRAPH_URL).rstrip("/")
        self._open = opener
        self._clock = clock
        # _lock guards the cache only and is never held across network I/O;
        # _meta_lock serialises the shared app token and role table refresh;
        # one in-flight lock per user dedupes concurrent lookups of the same id.
        self._lock = threading.Lock()
        self._meta_lock = threading.Lock()
        self._inflight = {}
        self._token = None
        self._token_expires = 0.0
        self._cache = {}
        self._role_values = None
        self._role_values_expires = 0.0

    def config_problem(self):
        if not _ID_RE.match(self.tenant_id or "-/"):
            return "ADAPTER_ENTRA_TENANT_ID missing or invalid"
        if not _ID_RE.match(self.client_id or "-/"):
            return "ADAPTER_ENTRA_CLIENT_ID missing or invalid"
        if not self.secret_file:
            return "ADAPTER_ENTRA_CLIENT_SECRET_FILE missing"
        if self.resource_id and not _ID_RE.match(self.resource_id):
            return "ADAPTER_ENTRA_RESOURCE_ID invalid"
        return None

    def settings(self):
        return {"source": SOURCE_ENTRA, "ttl": self.ttl,
                "roles": bool(self.resource_id),
                "config_error": self.config_problem(),
                "cached_users": len(self._cache)}

    # -- HTTP -----------------------------------------------------------------
    def _json(self, request):
        try:
            with self._open(request, self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raise DirectoryError("directory answered HTTP %s" % e.code)
        except (urllib.error.URLError, OSError) as e:
            raise DirectoryError("directory unreachable: %s" % getattr(e, "reason", e))
        except ValueError:
            raise DirectoryError("directory answered malformed JSON")

    def _access_token(self):
        with self._meta_lock:
            return self._access_token_locked()

    def _access_token_locked(self):
        now = self._clock()
        if self._token and now < self._token_expires:
            return self._token
        body = urllib.parse.urlencode({
            "client_id": self.client_id,
            "client_secret": _read_secret(self.secret_file),
            "scope": self.graph_url + "/.default",
            "grant_type": "client_credentials",
        }).encode("ascii")
        request = urllib.request.Request(
            "%s/%s/oauth2/v2.0/token" % (self.authority, self.tenant_id),
            data=body, method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"})
        payload = self._json(request)
        token = payload.get("access_token") if isinstance(payload, dict) else None
        if not token:
            raise DirectoryError("token endpoint returned no access_token")
        lifetime = payload.get("expires_in")
        try:
            lifetime = float(lifetime)
        except (TypeError, ValueError):
            lifetime = 300.0
        self._token = token
        self._token_expires = now + max(0.0, lifetime - 60.0)
        return token

    def _get_all(self, url):
        items = []
        for _ in range(MAX_PAGES):
            request = urllib.request.Request(url, method="GET", headers={
                "Authorization": "Bearer " + self._access_token(),
                "Accept": "application/json"})
            payload = self._json(request)
            if not isinstance(payload, dict):
                raise DirectoryError("directory answered a non-object")
            items.extend(v for v in payload.get("value") or [] if isinstance(v, dict))
            url = payload.get("@odata.nextLink")
            if not url:
                return items
            if not url.startswith(self.graph_url + "/"):
                raise DirectoryError("directory paging left the Graph endpoint")
        raise DirectoryError("directory paging exceeded %d pages" % MAX_PAGES)

    def _app_role_values(self):
        with self._meta_lock:
            now = self._clock()
            if self._role_values is not None and now < self._role_values_expires:
                return self._role_values
            request_url = "%s/v1.0/servicePrincipals/%s?$select=appRoles" % (
                self.graph_url, self.resource_id)
            request = urllib.request.Request(request_url, method="GET", headers={
                "Authorization": "Bearer " + self._access_token_locked(),
                "Accept": "application/json"})
            payload = self._json(request)
            roles = {}
            for role in (payload or {}).get("appRoles") or []:
                if isinstance(role, dict) and role.get("id") and role.get("value"):
                    roles[str(role["id"])] = str(role["value"])
            self._role_values = roles
            self._role_values_expires = now + self.ttl
            return roles

    def _fetch(self, key):
        user = urllib.parse.quote(key, safe="@")
        groups, names = [], {}
        for item in self._get_all("%s/v1.0/users/%s/transitiveMemberOf/microsoft.graph.group"
                                  "?$select=id,displayName&$top=999" % (self.graph_url, user)):
            group_id = item.get("id")
            if group_id and group_id not in names:
                groups.append(str(group_id))
                names[str(group_id)] = str(item.get("displayName") or "")
        roles = []
        if self.resource_id:
            values = self._app_role_values()
            for item in self._get_all("%s/v1.0/users/%s/appRoleAssignments"
                                      "?$select=appRoleId,resourceId&$top=999"
                                      % (self.graph_url, user)):
                if str(item.get("resourceId") or "") != self.resource_id:
                    continue
                value = values.get(str(item.get("appRoleId") or ""))
                if value and value not in roles:
                    roles.append(value)
        return groups, names, roles

    # -- lookup ---------------------------------------------------------------
    def lookup(self, identity):
        key = identity.directory_key if identity is not None else None
        if not key:
            return DirectoryInfo(SOURCE_ENTRA, error="no directory id (UPN/e-mail) for the user")
        problem = self.config_problem()
        if problem:
            return DirectoryInfo(SOURCE_ENTRA, error=problem)
        cache_key = key.lower()
        cached = self._cached(cache_key)
        if cached is not None:
            return cached
        with self._lock:
            inflight = self._inflight.setdefault(cache_key, threading.Lock())
        with inflight:
            # Another thread may have finished the same lookup meanwhile.
            cached = self._cached(cache_key)
            if cached is not None:
                return cached
            now = self._clock()
            try:
                groups, names, roles = self._fetch(key)
            except DirectoryError as e:
                info = DirectoryInfo(SOURCE_ENTRA, error=str(e), fetched_at=now)
                expires = now + min(self.ttl, NEGATIVE_TTL_SECONDS)
            else:
                info = DirectoryInfo(SOURCE_ENTRA, groups, roles, names, fetched_at=now)
                expires = now + self.ttl
            with self._lock:
                self._cache[cache_key] = (expires, info)
                self._inflight.pop(cache_key, None)
            return info

    def _cached(self, cache_key):
        with self._lock:
            cached = self._cache.get(cache_key)
            if cached and self._clock() < cached[0]:
                return cached[1]
            return None

    def forget(self, key):
        with self._lock:
            self._cache.pop((key or "").lower(), None)


def from_env(env=None, warn=None):
    env = env if env is not None else os.environ
    warn = warn or (lambda message: sys.stderr.write("[adapter] directory: %s\n" % message))
    source = parse_source(env.get("ADAPTER_DIRECTORY"), warn)
    if source == SOURCE_TOKEN:
        return TokenDirectory()
    if source != SOURCE_ENTRA:
        return NullDirectory()

    def number(name, default):
        raw = (env.get(name) or "").strip()
        if not raw:
            return default
        try:
            return int(raw)
        except ValueError:
            warn("invalid %s=%r -> %d" % (name, raw, default))
            return default

    directory = EntraDirectory(
        tenant_id=env.get("ADAPTER_ENTRA_TENANT_ID"),
        client_id=env.get("ADAPTER_ENTRA_CLIENT_ID"),
        secret_file=(env.get("ADAPTER_ENTRA_CLIENT_SECRET_FILE") or "").strip() or None,
        resource_id=env.get("ADAPTER_ENTRA_RESOURCE_ID"),
        ttl=number("ADAPTER_DIRECTORY_TTL_SECONDS", 900),
        timeout=number("ADAPTER_DIRECTORY_TIMEOUT_SECONDS", 10),
        authority=(env.get("ADAPTER_ENTRA_AUTHORITY") or "").strip() or DEFAULT_AUTHORITY,
        graph_url=(env.get("ADAPTER_ENTRA_GRAPH_URL") or "").strip() or DEFAULT_GRAPH_URL)
    problem = directory.config_problem()
    if problem:
        warn("entra directory misconfigured (%s): every lookup returns no groups" % problem)
    return directory
