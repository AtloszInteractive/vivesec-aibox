"""Sign-in through the ViVeSecBox tunnel (Matrix next to the Dokumentum AI).

On a shared DGX Spark the box is not paired with the ViVeSecBox, but the
ViVeSecBox embeds its UI and relays the requests over its proxy tunnel. The
tunnel front on the box checks the ViVeSecBox client certificate and passes the
request on to the tunnel gateway, which asks this module for an identity token
on behalf of the `VVS-User` the ViVeSecBox asserted.

A `VVS-User` signs in only as the local user an administrator mapped it to
(AUTH_TUNNEL_USERS_FILE, a JSON object `{"<VVS-User>": "<username>"}`, managed
with `manage.py tunnel-map`). An unmapped, malformed or suspended identity gets
no token; a missing or broken map maps nobody. With AUTH_TUNNEL_AUTOCREATE=analyst
an unmapped identity gets a local user of its own on its first visit
(`vvs-<10 hex of its sha256>`, role analyst, no rooms, no password: it signs in
through the tunnel only) and the mapping is written; an administrator gives it
rooms. A user the administrator deleted or suspended stays refused. With
AUTH_TUNNEL_OWNER_ADMIN=on the ViVeSecBox's owner (the gateway saw
`VVS-Permissions: ...,owner`) gets a user the same way, also when
AUTH_TUNNEL_AUTOCREATE is off, and holds the admin role on every visit: an
administrator cannot take it away while the switch is on. A former owner keeps
the role until an administrator removes it. The token is the same as the one
the web login gets, minted from the current user record, so the roles, groups
and rooms are the box's own -- the tunnel only says who the user is. The
ViVeSecBox's own sign-in stands in for the password and the TOTP code.
"""
import hashlib
import json
import os
import re
import secrets
import threading

import directory as directory_module
import tokens

VVS_USER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._@=+-]{0,127}$")
MAX_SESSION_LENGTH = 256
MAX_ENTRIES = 10000


class MapError(ValueError):
    """The map is not usable."""


def normalize_vvs_user(raw):
    if not isinstance(raw, str):
        return None
    value = raw.strip()
    return value if VVS_USER_RE.match(value) else None


def parse_map(data, normalize_username):
    """`{"<VVS-User>": "<username>"}` -> a validated dict. Raises MapError."""
    if not isinstance(data, dict):
        raise MapError("the tunnel map must be a JSON object")
    if len(data) > MAX_ENTRIES:
        raise MapError("the tunnel map has more than %d entries" % MAX_ENTRIES)
    mapping = {}
    for raw_user, raw_name in data.items():
        vvs_user = normalize_vvs_user(raw_user)
        if vvs_user is None:
            raise MapError("invalid VVS-User %r" % (raw_user,))
        name = normalize_username(raw_name)
        if name is None:
            raise MapError("invalid username %r for %s" % (raw_name, vvs_user))
        mapping[vvs_user] = name
    return mapping


class TunnelMap:
    """The map file, read again when it changes. A missing file maps nobody;
    an unreadable or invalid one maps nobody and says why in `problem`."""

    def __init__(self, path, normalize_username):
        self.path = path
        self._normalize = normalize_username
        self._lock = threading.Lock()
        self._stamp = None
        self._mapping = {}
        self.problem = ""

    def _load(self):
        try:
            stat = os.stat(self.path)
        except FileNotFoundError:
            return None, {}, ""
        except OSError as e:
            return None, {}, "cannot read the tunnel map: %s" % e.strerror
        stamp = (stat.st_mtime_ns, stat.st_size, stat.st_ino)
        if stamp == self._stamp:
            return stamp, self._mapping, self.problem
        try:
            with open(self.path, encoding="utf-8") as handle:
                return stamp, parse_map(json.load(handle), self._normalize), ""
        except (OSError, ValueError) as e:
            return stamp, {}, "tunnel map %s ignored: %s" % (self.path, e)

    def mapping(self):
        with self._lock:
            self._stamp, self._mapping, self.problem = self._load()
            return dict(self._mapping)

    def username_for(self, vvs_user):
        return self.mapping().get(vvs_user)


def write_map(path, mapping):
    directory_module.write_private(
        path, (json.dumps(dict(sorted(mapping.items())), indent=2, ensure_ascii=False) + "\n")
        .encode("utf-8"))


AUTO_PREFIX = "vvs-"


def auto_username(vvs_user):
    """The local user a ViVeSecBox identity gets on its own: stable, and it does
    not show the identity itself."""
    return AUTO_PREFIX + hashlib.sha256(vvs_user.encode("utf-8")).hexdigest()[:10]


def session_id(vvs_user, vvs_session):
    """A stable public handle (`sid`) for one tunnel session that reveals
    neither the ViVeSecBox session nor the user."""
    digest = hashlib.sha256(("%s\n%s" % (vvs_user, vvs_session)).encode("utf-8")).hexdigest()
    return "tunnel-" + digest[:24]


def token_claims(settings, user, sid, now):
    issued = int(now)
    return {"iss": settings.jwt_issuer, "aud": settings.jwt_audience,
            "sub": user["username"], "upn": user["username"],
            "name": user["display_name"], "groups": list(user["groups"]),
            "roles": list(user["roles"]), "drives": list(user["drives"]),
            "sid": sid, "amr": ["vivesecbox-tunnel"],
            "iat": issued, "nbf": issued, "exp": issued + settings.token_ttl_seconds,
            "jti": secrets.token_hex(8)}


def sign(claims, secret):
    return tokens.sign_hs256(claims, secret)
