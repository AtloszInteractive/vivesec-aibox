"""Directory (Active Directory) login of a standalone box (E27 S6).

The box never stores a directory password. A login:

1. binds with the read-only service account (AUTH_LDAP_BIND_DN, password in
   AUTH_LDAP_BIND_PASSWORD_FILE) and finds the user by `sAMAccountName` (or
   `userPrincipalName`) below AUTH_LDAP_BASE_DN; a disabled account is refused;
2. asks, for every group in the group map, whether the user is a member,
   directly or through nested groups (LDAP_MATCHING_RULE_IN_CHAIN);
3. binds as the user with the typed password: the directory checks it, with
   its own lockout and expiry rules;
4. maps groups -> roles -> rooms (drives) with the group map file. A user in
   none of the mapped groups cannot sign in.

A running session re-reads steps 1, 2 and 4 every AUTH_LDAP_REFRESH_SECONDS:
a user who was disabled, deleted or removed from every mapped group loses the
sessions at once.

Fail closed: without a reachable, pinned server, a usable service account and
a valid group map nobody signs in through the directory. A configuration that
cannot be used switches the directory off (`state == "invalid"`) instead of
stopping the service, so the local emergency administrator can still sign in.

Group map (AUTH_LDAP_MAP_FILE, JSON, re-read when it changes):

    {"groups": [{"dn": "CN=AIBox-Admins,OU=Groups,DC=bv,DC=local",
                 "name": "aibox-admins", "roles": ["admin"]},
                {"dn": "CN=AIBox-Analysts,OU=Groups,DC=bv,DC=local", "roles": ["analyst"]}],
     "roles": {"admin": ["/storage/drives/cases/", "/storage/drives/hr/"],
               "analyst": ["/storage/drives/cases/"]}}

`name` is the group name in the identity token (`groups` claim, the adapter's
`group:` ACL principals); it defaults to the first CN of the DN. Every role a
group grants must be listed under `roles`, with its rooms (possibly none).
"""
import hashlib
import json
import os
import re
import sys
import threading

import ldap_client

USER_ATTRIBUTES = {"samaccountname": "sAMAccountName", "userprincipalname": "userPrincipalName"}
ACCOUNT_DISABLED = 0x2
MAX_DN_LENGTH = 1024
MAX_MAP_BYTES = 256 * 1024
MAX_GROUPS = 64
MAX_ROLES = 64
# Active Directory puts the reason of a refused bind into the diagnostic
# message: "... AcceptSecurityContext error, data 52e, v4563".
_AD_DATA_RE = re.compile(r"\bdata ([0-9a-f]{3,8})\b", re.IGNORECASE)
AD_REFUSALS = {"52e": "bad_password", "525": "unknown_user", "530": "logon_hours",
               "531": "logon_workstation", "532": "password_expired", "533": "disabled",
               "701": "account_expired", "773": "password_must_change", "775": "locked"}


class Unavailable(Exception):
    """The directory cannot answer: unreachable, untrusted, misconfigured."""

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


class Refused(Exception):
    """The directory answered no. `user_found` tells whether the account
    exists (then its name may be logged)."""

    def __init__(self, reason, user_found=False):
        super().__init__(reason)
        self.reason = reason
        self.user_found = user_found


class DirectoryUser:
    def __init__(self, username, dn, display_name, groups, roles, drives):
        self.username = username
        self.dn = dn
        self.display_name = display_name
        self.groups = groups
        self.roles = roles
        self.drives = drives

    def fields(self):
        return {"display_name": self.display_name, "groups": list(self.groups),
                "roles": list(self.roles), "drives": list(self.drives), "directory_dn": self.dn}


def _clean(text, limit):
    return isinstance(text, str) and 0 < len(text) <= limit \
        and not any(ord(char) < 32 or ord(char) == 127 for char in text)


def first_cn(dn):
    head = dn.split(",", 1)[0].strip()
    key, _sep, value = head.partition("=")
    return value.strip() if key.strip().lower() == "cn" and "\\" not in value else ""


class GroupMap:
    def __init__(self, groups, roles):
        self.groups = groups    # [(dn, name, [roles])]
        self.roles = roles      # {role: [drives]}

    def as_dict(self):
        """The normalised JSON form: what the box writes and the admin page shows."""
        return {"groups": [{"dn": dn, "name": name, "roles": list(roles)}
                           for dn, name, roles in self.groups],
                "roles": {role: list(drives) for role, drives in self.roles.items()}}

    def resolve(self, member_dns):
        """(group names, roles, drives) for the mapped groups in `member_dns`."""
        names, roles, drives = [], [], []
        for dn, name, granted in self.groups:
            if dn not in member_dns:
                continue
            if name not in names:
                names.append(name)
            for role in granted:
                if role not in roles:
                    roles.append(role)
        for role in roles:
            for drive in self.roles[role]:
                if drive not in drives:
                    drives.append(drive)
        return names, roles, drives


def parse_group_map(data):
    """A GroupMap from the decoded JSON; ValueError says what is wrong."""
    import auth  # the same validation as for local users; imported late (auth imports us)

    if not isinstance(data, dict) or set(data) - {"groups", "roles"}:
        raise ValueError("the group map is an object with `groups` and `roles`")
    raw_roles = data.get("roles")
    if not isinstance(raw_roles, dict) or len(raw_roles) > MAX_ROLES:
        raise ValueError("`roles` must be an object of at most %d roles" % MAX_ROLES)
    roles, every_drive = {}, []
    for role, drives in raw_roles.items():
        name = role.strip().lower() if isinstance(role, str) else ""
        if not auth.ROLE_RE.match(name) or name in roles:
            raise ValueError("invalid or repeated role %r" % role)
        try:
            roles[name] = auth.normalize_drives(drives)
        except auth.AuthError:
            raise ValueError("invalid rooms for role %r" % role)
        every_drive.extend(drive for drive in roles[name] if drive not in every_drive)
    if len(every_drive) > auth.MAX_LIST_ITEMS:
        raise ValueError("more than %d rooms in total" % auth.MAX_LIST_ITEMS)
    raw_groups = data.get("groups")
    if not isinstance(raw_groups, list) or not raw_groups or len(raw_groups) > MAX_GROUPS:
        raise ValueError("`groups` must be a list of 1..%d groups" % MAX_GROUPS)
    groups, seen_dns, seen_names = [], set(), set()
    for entry in raw_groups:
        if not isinstance(entry, dict) or set(entry) - {"dn", "name", "roles"}:
            raise ValueError("a group is an object with `dn`, `roles` and an optional `name`")
        dn = entry.get("dn")
        if not _clean(dn, MAX_DN_LENGTH) or "=" not in dn:
            raise ValueError("invalid group dn %r" % dn)
        dn = dn.strip()
        name = entry.get("name", first_cn(dn))
        name = name.strip() if isinstance(name, str) else ""
        if not auth.GROUP_RE.match(name):
            raise ValueError("group %r needs a `name` of letters, digits, space and _.@-" % dn)
        granted = entry.get("roles")
        if not isinstance(granted, list) or not granted:
            raise ValueError("group %r grants no role" % dn)
        granted_roles = []
        for role in granted:
            role = role.strip().lower() if isinstance(role, str) else ""
            if role not in roles:
                raise ValueError("group %r grants role %r, which is not under `roles`" % (dn, role))
            if role not in granted_roles:
                granted_roles.append(role)
        if dn.lower() in seen_dns or name.lower() in seen_names:
            raise ValueError("group %r is listed twice" % dn)
        seen_dns.add(dn.lower())
        seen_names.add(name.lower())
        groups.append((dn, name, granted_roles))
    return GroupMap(groups, roles)


def _decode_group_map(raw):
    if len(raw) > MAX_MAP_BYTES:
        raise ValueError("the group map is larger than %d bytes" % MAX_MAP_BYTES)
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as e:
        raise ValueError("the group map is not valid JSON: %s" % e)
    return parse_group_map(data)


def read_group_map(path):
    with open(path, "rb") as handle:
        raw = handle.read(MAX_MAP_BYTES + 1)
    return _decode_group_map(raw)


def map_version(raw):
    """The version of a group map file's content (`none` for no file): the
    administration page sends it back, so a save never overwrites a change it
    has not seen."""
    return "none" if raw is None else hashlib.sha256(raw).hexdigest()


def group_map_snapshot(path):
    """(GroupMap or None, error or None, version) of the file as it is now."""
    try:
        with open(path, "rb") as handle:
            raw = handle.read(MAX_MAP_BYTES + 1)
    except FileNotFoundError:
        return None, None, map_version(None)
    except OSError as e:
        return None, "unreadable: %s" % e.strerror, "unreadable"
    try:
        return _decode_group_map(raw), None, map_version(raw)
    except ValueError as e:
        return None, str(e), map_version(raw)


def write_private(path, data):
    """Write `data` (bytes) atomically, readable by the owner only."""
    directory = os.path.dirname(os.path.abspath(path))
    temp = os.path.join(directory, ".%s.tmp" % os.path.basename(path))
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    except BaseException:
        try:
            os.unlink(temp)
        except OSError:
            pass
        raise


def ad_refusal(error):
    """The reason of a refused user bind (ResultError 49)."""
    match = _AD_DATA_RE.search(error.diagnostic or "")
    return AD_REFUSALS.get(match.group(1).lower(), "refused_" + match.group(1).lower()) \
        if match else "bad_password"


class Directory:
    """The directory of the settings, or `state` "off" / "invalid"."""

    def __init__(self, settings, log=None, connection_factory=ldap_client.Connection):
        self.settings = settings
        self.log = log or (lambda message: sys.stderr.write("[auth] directory: %s\n" % message))
        self.connect_to = connection_factory
        self.state, self.problem = "off", ""
        self.tls = None
        self._map_lock = threading.Lock()
        self._map_key, self._map, self._map_error = None, None, None
        if not settings.ldap_urls:
            return
        try:
            for url in settings.ldap_urls:
                ldap_client.parse_url(url)
            self.tls = ldap_client.TlsPolicy(settings.ldap_tls_pins, settings.ldap_ca_file or None)
            if settings.ldap_ca_file and not os.path.isfile(settings.ldap_ca_file):
                raise ValueError("AUTH_LDAP_CA_FILE %s does not exist" % settings.ldap_ca_file)
            if not _clean(settings.ldap_base_dn, MAX_DN_LENGTH):
                raise ValueError("AUTH_LDAP_BASE_DN is required")
            if not _clean(settings.ldap_bind_dn, MAX_DN_LENGTH):
                raise ValueError("AUTH_LDAP_BIND_DN (the read-only service account) is required")
            if settings.ldap_user_attribute.lower() not in USER_ATTRIBUTES:
                raise ValueError("AUTH_LDAP_USER_ATTRIBUTE must be sAMAccountName or userPrincipalName")
        except ValueError as e:
            self.state, self.problem = "invalid", str(e)
            self.log("switched off, the configuration is not usable: %s" % e)
            return
        self.state = "ready"

    @property
    def enabled(self):
        return self.state == "ready"

    # -- configuration files ---------------------------------------------------
    def group_map(self):
        """The current group map; Unavailable when it is missing or invalid."""
        path = self.settings.ldap_map_file
        try:
            info = os.stat(path)
        except OSError:
            raise Unavailable("group_map_missing")
        key = (info.st_mtime_ns, info.st_size, info.st_ino)
        with self._map_lock:
            if key != self._map_key:
                try:
                    self._map, self._map_error = read_group_map(path), None
                except (OSError, ValueError) as e:
                    self._map, self._map_error = None, str(e)
                    self.log("group map %s refused: %s" % (path, e))
                self._map_key = key
            if self._map is None:
                raise Unavailable("group_map_invalid")
            return self._map

    def _service_password(self):
        try:
            with open(self.settings.ldap_bind_password_file, "r", encoding="utf-8") as handle:
                password = handle.read(4096).rstrip("\r\n")
        except (OSError, UnicodeDecodeError):
            raise Unavailable("service_password_missing")
        if not password:
            raise Unavailable("service_password_missing")
        return password

    # -- directory access ------------------------------------------------------
    def _connect(self):
        """An open, trusted connection bound as the service account."""
        if not self.enabled:
            raise Unavailable("not_configured" if self.state == "off" else "invalid_configuration")
        password = self._service_password()
        last = "unreachable"
        for url in self.settings.ldap_urls:
            connection = self.connect_to(url, self.tls, self.settings.ldap_timeout_seconds)
            try:
                connection.open()
            except ldap_client.TlsPinError as e:
                self.log("%s presented an unpinned certificate %s"
                         % (url, ldap_client.format_fingerprint(e.fingerprint)))
                last = "certificate_not_pinned"
                continue
            except (ldap_client.LdapError, OSError) as e:
                self.log("%s: %s" % (url, e))
                last = "unreachable"
                continue
            try:
                connection.bind(self.settings.ldap_bind_dn, password)
            except ldap_client.ResultError as e:
                connection.close()
                self.log("%s: the service account bind failed (result %d)" % (url, e.code))
                raise Unavailable("service_bind_failed")
            except (ldap_client.LdapError, OSError) as e:
                connection.close()
                self.log("%s: %s" % (url, e))
                last = "unreachable"
                continue
            return connection
        raise Unavailable(last)

    def _find(self, connection, username):
        """(dn, display name) of the enabled account `username`; Refused otherwise."""
        attribute = USER_ATTRIBUTES[self.settings.ldap_user_attribute.lower()]
        query = ldap_client.all_of(ldap_client.equal("objectClass", "user"),
                                   ldap_client.equal("objectCategory", "person"),
                                   ldap_client.equal(attribute, username))
        try:
            entries = connection.search(self.settings.ldap_base_dn, ldap_client.SCOPE_SUBTREE, query,
                                        (attribute, "displayName", "userAccountControl"), size_limit=2)
        except ldap_client.ResultError as e:
            if e.code == ldap_client.RESULT_SIZE_LIMIT:
                raise Refused("ambiguous_user")
            raise Unavailable("search_failed")
        if not entries:
            raise Refused("unknown_user")
        if len(entries) > 1:
            raise Refused("ambiguous_user")
        dn, attributes = entries[0]
        names = [value.decode("utf-8", "replace").strip().lower()
                 for value in attributes.get(attribute.lower(), [])]
        if names != [username]:
            raise Refused("unknown_user")
        try:
            control = int((attributes.get("useraccountcontrol") or [b"0"])[0])
        except ValueError:
            control = ACCOUNT_DISABLED
        if control & ACCOUNT_DISABLED:
            raise Refused("disabled", user_found=True)
        display = (attributes.get("displayname") or [b""])[0].decode("utf-8", "replace").strip()
        if not _clean(display, 128):
            display = ""
        return dn, display

    def _member_of(self, connection, user_dn, group_map):
        """The mapped group DNs `user_dn` belongs to, directly or nested."""
        found = set()
        for dn, _name, _roles in group_map.groups:
            try:
                entries = connection.search(dn, ldap_client.SCOPE_BASE,
                                            ldap_client.chain("member", user_dn), size_limit=1)
            except ldap_client.ResultError:
                raise Unavailable("group_search_failed")
            if entries:
                found.add(dn)
        return found

    def _resolve(self, connection, username):
        group_map = self.group_map()
        dn, display = self._find(connection, username)
        names, roles, drives = group_map.resolve(self._member_of(connection, dn, group_map))
        return DirectoryUser(username, dn, display, names, roles, drives)

    def authenticate(self, username, password):
        """The DirectoryUser for a correct password; Refused or Unavailable."""
        if not isinstance(password, str) or not password:
            raise Refused("empty_password")
        if len(password.encode("utf-8")) > 1024:
            raise Refused("password_too_long")
        connection = self._connect()
        try:
            user = self._resolve(connection, username)
            try:
                connection.bind(user.dn, password)
            except ldap_client.ResultError as e:
                if e.code == ldap_client.RESULT_INVALID_CREDENTIALS:
                    raise Refused(ad_refusal(e), user_found=True)
                self.log("user bind answered result %d" % e.code)
                raise Unavailable("user_bind_failed")
        except (ldap_client.LdapError, OSError) as e:
            self.log("login lookup failed: %s" % e)
            raise Unavailable("unreachable")
        finally:
            connection.close()
        if not user.roles:
            raise Refused("not_authorized", user_found=True)
        return user

    def lookup(self, username):
        """The DirectoryUser as the directory sees it now (no password);
        Refused when it may no longer sign in, Unavailable when unknown."""
        connection = self._connect()
        try:
            user = self._resolve(connection, username)
        except (ldap_client.LdapError, OSError) as e:
            self.log("refresh lookup failed: %s" % e)
            raise Unavailable("unreachable")
        finally:
            connection.close()
        if not user.roles:
            raise Refused("not_authorized", user_found=True)
        return user

    def check(self, username=None):
        """For `manage.py ldap-check`: the service account bind, whether every
        mapped group exists, and optionally what `username` would get (no
        password involved). Raises Unavailable."""
        group_map = self.group_map()
        connection = self._connect()
        report = {"service_bind": "ok", "groups": []}
        try:
            for dn, name, roles in group_map.groups:
                entries = connection.search(dn, ldap_client.SCOPE_BASE, ldap_client.present("objectClass"),
                                            size_limit=1)
                report["groups"].append({"dn": dn, "name": name, "roles": roles, "found": bool(entries)})
            if username:
                try:
                    user = self._resolve(connection, username)
                except Refused as e:
                    report["user"] = {"username": username, "refused": e.reason}
                else:
                    report["user"] = dict(user.fields(), username=username,
                                          may_sign_in=bool(user.roles))
        except (ldap_client.LdapError, OSError) as e:
            raise Unavailable("check failed: %s" % e)
        finally:
            connection.close()
        return report
