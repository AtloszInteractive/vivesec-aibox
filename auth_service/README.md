# Authentication service (E27 S3, standalone mode)

In standalone mode (`ADAPTER_INTEGRATION=standalone`) the box has no ViVeSecBox
to vouch for its users, so it authenticates them itself: local users with a
password and a TOTP authenticator app, and Active Directory users (S6) whose
password the directory checks, also with a TOTP app on the box.

```text
Browser ──HTTPS──▶ nginx ──▶ web UI server (login gate, S4) ──loopback, service key──▶ auth_service
                                      │                                                    │
                                      └──loopback, JWT (VVS-Identity)──▶ adapter ◀── same HS256 key file
```

Pure standard-library Python (`ThreadingHTTPServer`, `sqlite3`, `hashlib.scrypt`,
`ssl`), like the adapter. The LDAP client is a small one of our own
(`ldap_client.py`), so the service has no `requirements.txt` and installs
offline as it is.

| File | Purpose |
| --- | --- |
| `service.py` | HTTP front: loopback only, `X-Auth-Service-Key` required, JSON in/out |
| `auth.py` | Login, sessions, identity tokens, password change, administration |
| `store.py` | SQLite: users, sessions, login challenges, event log |
| `passwords.py` | scrypt hashing (N=2^15, r=8, p=1) and the password policy |
| `totp.py` | RFC 6238 (SHA-1, 6 digits, 30 s, ±1 step), replay protection |
| `tokens.py` | HS256 JWT signing, key files |
| `directory.py` | Active Directory login: lookup, nested groups, user bind, group map (S6) |
| `ldap_client.py` | Minimal LDAP v3 client: LDAPS / StartTLS, certificate pinning, bind, search |
| `config.py` | `AUTH_*` settings |
| `manage.py` | Administration from the box's shell |
| `samba_ad/` | Samba AD test domain controller (development and tests only, not in the image) |

## Login

1. `POST /auth/login {username, password, client}`: the password step. The
   answer is a challenge for the second step: `step: "totp"` for an enrolled
   user, or `step: "enroll"` with a new `totp_secret` and its `otpauth_uri` (the
   QR code) on the first login and after an administrator reset. There is no
   password-only login.
2. `POST /auth/login/totp {challenge, code, client}`: the code. The answer
   carries the session secret (for the `HttpOnly` cookie the web UI server
   sets) and `password_change_required` when the password is temporary.
3. `POST /auth/token {session}`: a short-lived identity token for the adapter
   (default 5 minutes, never beyond the session). The web UI server caches it
   until it expires and sends it in `VVS-Identity` with `VVS-User` = `sub`.
   While the password is temporary only `/auth/me`, `/auth/password` and
   `/auth/logout` work.

Token claims: `iss`, `aud`, `sub` and `upn` (the username), `name`, `groups`,
`roles`, `drives` (the rooms the user may search), `sid` (the public session
handle, never the cookie secret), `iat`, `nbf`, `exp`, `jti`. They are minted
from the current user record, so a change of roles or rooms applies at the next
token.

The full endpoint list is in the `service.py` docstring.

## Directory login: Active Directory (S6)

With `AUTH_LDAP_URLS` set, a name that is not a local user is looked up in
Active Directory. The box never stores or sees more of the directory than it
needs:

1. It binds with a **read-only service account** (`AUTH_LDAP_BIND_DN`, password
   in `AUTH_LDAP_BIND_PASSWORD_FILE`) and finds the user by `sAMAccountName`
   (or `userPrincipalName`) below `AUTH_LDAP_BASE_DN`. A disabled account is
   refused.
2. For every group of the group map it asks whether the user is a member,
   **directly or through nested groups** (`member:1.2.840.113556.1.4.1941:=`,
   `LDAP_MATCHING_RULE_IN_CHAIN`).
3. It **binds as the user** with the typed password: the directory checks it,
   with its own lockout, expiry and "must change" rules. An empty password is
   never sent (LDAP would take it as an anonymous bind that "succeeds").
4. Groups map to roles, roles to rooms (`AUTH_LDAP_MAP_FILE`). A user in none of
   the mapped groups cannot sign in.

Then the TOTP step follows exactly as for a local user (enrolment on the first
login, the secret stays on the box). The box keeps a `source: "ldap"` record
for the TOTP secret, the lockout counter and the sessions; it has no password.
Its token carries the mapped group names, roles and rooms.

| Rule | Behaviour |
| --- | --- |
| Encryption | `ldaps://` or `ldap://` with StartTLS (always); TLS 1.2 or newer; no plain-text bind exists |
| Server trust | `AUTH_LDAP_TLS_PINS` (SHA-256 of the DC certificate, self-signed is fine) and/or `AUTH_LDAP_CA_FILE` (chain + host name). Checked right after the handshake, before the first bind. An unpinned certificate is refused and logged with its fingerprint. |
| Name clash | A local user always wins; a directory account cannot take over a local name. |
| Refusals | Unknown, disabled, wrong password, locked/expired in AD, no mapped group: `invalid_credentials`, with the reason in the event log (`directory_*`). A wrong password counts towards the box's lockout once the record exists; while locked locally the directory is not asked. |
| Outage | A new login needs the directory: `directory_unavailable` (503). Running sessions are re-checked every `AUTH_LDAP_REFRESH_SECONDS` (300); during an outage they keep the last answer for `AUTH_LDAP_MAX_STALE_SECONDS` (900), then fail closed (503) until the directory answers. |
| Removal | A user the refresh finds disabled, deleted or in no mapped group loses every session at once (`directory_access_ended`). Changed groups change the roles and rooms of the next token. |
| Configuration | An unusable setting (no pin and no CA, bad URL, missing base or service DN) switches the directory off as `invalid`: directory users get 503, **local users, including the emergency administrator, still sign in**. A missing or invalid group map or service password fails the directory logins closed. `/auth/health` shows `directory: off / ready / invalid`. |
| Administration | Password, display name, roles, groups and rooms of a directory user are owned by AD and the group map (`managed_by_directory`). The box's administrator can suspend, delete, unlock and reset the TOTP. Directory switched off: its users cannot sign in, their sessions end at the next refresh. |

Group map (`manage.py ldap-map import FILE`, validated before it is written):

```json
{
  "groups": [
    {"dn": "CN=AIBox-Admins,OU=Groups,DC=bv,DC=local", "name": "aibox-admins", "roles": ["admin"]},
    {"dn": "CN=AIBox-Analysts,OU=Groups,DC=bv,DC=local", "roles": ["analyst"]}
  ],
  "roles": {
    "admin": ["/storage/drives/cases/", "/storage/drives/hr/"],
    "analyst": ["/storage/drives/cases/"]
  }
}
```

`name` is the group name in the token (the adapter's `group:` ACL principals);
it defaults to the group's CN and must be letters, digits, space and `_.@-`.
Every role a group grants must be listed under `roles` (its rooms may be
empty). The `admin` role makes the user a box administrator.

Setup on the box (the service account needs read access only):

```bash
docker exec vivesec-auth python /app/auth_service/manage.py ldap-cert ldaps://dc01.bv.local
#   compare the fingerprint with the certificate on the domain controller
export AUTH_LDAP_URLS=ldaps://dc01.bv.local,ldaps://dc02.bv.local
export AUTH_LDAP_TLS_PINS=<fingerprint dc01>,<fingerprint dc02>
export AUTH_LDAP_BASE_DN="DC=bv,DC=local" AUTH_LDAP_BIND_DN="CN=svc-aibox,OU=Service,DC=bv,DC=local"
python3 auth_deploy.py deploy <source-dir>       # passes the AUTH_* settings to the container
docker exec -i vivesec-auth python /app/auth_service/manage.py ldap-password --password-stdin
docker exec -i vivesec-auth python /app/auth_service/manage.py ldap-map import - < ldap-groups.json
docker exec vivesec-auth python /app/auth_service/manage.py ldap-check some.user
```

`ldap-check` binds with the service account, reports whether every mapped group
exists and what a user would get, without any user password. The DC must be
reachable from the box (TCP 636, or 389 for StartTLS); the box's clock must be
synchronised anyway (TOTP).

## Login gate in the web UI server (S4)

With `ADAPTER_INTEGRATION=standalone` the box-hosted web UI server
(`src/lib/auth/gate.server.ts`, in front of every request in `src/server.ts`)
is the only caller of this service:

| Browser | Gate |
| --- | --- |
| `GET /auth/session` | who is signed in; issues the CSRF cookie |
| `POST /auth/login {username, password}` | password step; the challenge goes into an `HttpOnly` cookie, never to the page. On the first login the answer carries the TOTP secret and the QR code as a module matrix (drawn as SVG). |
| `POST /auth/login/totp {code}` | code step; sets the session cookie |
| `POST /auth/password {current_password, new_password}` | password change |
| `POST /auth/logout` | ends the session here and in the service |
| pages `/login`, `/password` | sign-in, TOTP enrolment, password change (EN/HU/DA/DE) |

- Cookies: `__Host-aibox_session` (session secret) and `__Host-aibox_login`
  (pending challenge, 5 minutes) are `HttpOnly; Secure; SameSite=Strict;
  Path=/`. `__Host-aibox_csrf` is readable by the page: every state-changing call
  (`/auth/*` and `/api/v1/*`) must send it back in `X-AIBox-CSRF`, and a foreign
  `Origin` or `Sec-Fetch-Site` is refused. A new CSRF token is issued at login and
  logout.
- Without a session every page redirects to `/login` and every `/api/v1` call
  answers `401 login_required` (`X-AIBox-Auth: login`, which sends the page to
  the login form). While the password is temporary, pages redirect to
  `/password` and the API answers `403 password_change_required`.
- `/api/v1` calls are relayed with `VVS-Identity` (the cached token, renewed 30 s
  before it expires), `VVS-User` = `sub`, and `VVS-Drive` = the room the page
  picked (`X-Demo-Drive`) if the token grants it, else the first granted room;
  without rooms no `VVS-Drive` is sent. Every identity header, cookie and
  `Authorization` header from the browser is dropped. The room picker of the UI
  lists the token's rooms (`/api/v1/ui/demo-config`).
- `client`: the last `X-Forwarded-For` entry when the peer is loopback (nginx
  overwrites it, the Tailscale relay appends), else the peer address.
- `AUTH_URL` must be a loopback `http://` URL and `AUTH_SERVICE_KEY_FILE`
  readable, otherwise nobody can sign in. An unknown `ADAPTER_INTEGRATION`
  keeps the gate on.

In `vivesecbox` mode the gate only answers `GET /auth/session` with
`{"standalone": false}`; the embedded UI bundle never calls it.

## Security properties

- Listens on loopback only (`AUTH_HOST` must be a loopback address or the
  service does not start), and answers only callers that present the service
  key. Request bodies are never logged.
- Session and challenge secrets are stored as SHA-256 hashes; the session
  secret never appears in a token or in the event log.
- An unknown user, a wrong password and a locked account all answer
  `invalid_credentials` after the same scrypt work. The name typed for an
  unknown user is not logged, because people sometimes type their password there.
- `AUTH_LOCKOUT_THRESHOLD` failures in a row (wrong passwords and wrong codes
  together) lock the account for `AUTH_LOCKOUT_SECONDS`; while locked the
  password is not even checked. The per-client throttle
  (`AUTH_CLIENT_MAX_FAILURES` per `AUTH_CLIENT_WINDOW_SECONDS`) stops password
  spraying across accounts.
- A TOTP code is accepted once: the last used time step is stored and the update
  is conditional, so two parallel requests cannot both use one code. A challenge
  is single-use, expires after 5 minutes and is dropped after 5 wrong codes.
- Sessions end after `AUTH_SESSION_IDLE_SECONDS` without activity and after
  `AUTH_SESSION_MAX_SECONDS` in any case. Suspension, deletion, a TOTP reset or
  a password reset ends every session of the user; a password change ends the
  user's other sessions.
- The last active administrator cannot be deleted, suspended or demoted.
- The event log (`/auth/admin/events`, `manage.py events`) records logins,
  failures with their reason, lockouts, enrolments, resets, password changes and
  every administrative change with its actor and client address. It holds no
  password, code, secret or token.

Known limits, by decision (plan 1.1): no recovery codes yet. A user who loses
the phone needs an administrator (`reset-totp`). The TOTP secrets are stored as
they are in `auth.db`, so the data directory belongs on the encrypted volume
(E05). After logout an already issued token stays valid until it expires (at
most `AUTH_TOKEN_TTL_SECONDS`); the gate drops its cached copy at logout, so
this matters only for a token copied out of the box. A suspension or reset
reaches the gate's cached token within the same time. A directory user disabled
in AD keeps a running session until the next refresh (at most
`AUTH_LDAP_REFRESH_SECONDS`, plus the token lifetime). The answer time of a
directory login may tell an existing directory account from an unknown one
(the directory is asked one more time); the answer itself does not.

## Setup on the box

On a box with docker, `scripts/jetson/standalone/auth_deploy.py` does all of
it (see the [standalone README](../scripts/jetson/standalone/README.md)):

```bash
python3 auth_deploy.py deploy <source-dir>    # tests, image vivesec-auth, /data/auth, container
docker exec -it vivesec-auth python /app/auth_service/manage.py create-user admin --role admin --temporary
python3 auth_deploy.py enable                 # adapter + web UI -> standalone
```

By hand, outside docker:

```bash
python auth_service/manage.py init                      # /data/auth: keys (0600) and the database
python auth_service/manage.py create-user admin --role admin
python auth_service/service.py                          # 127.0.0.1:8097
```

The adapter verifies the tokens with:

```text
ADAPTER_INTEGRATION=standalone
ADAPTER_IDENTITY_MODE=require
ADAPTER_DIRECTORY=token
ADAPTER_IDENTITY_ALGORITHMS=HS256
ADAPTER_IDENTITY_HS256_SECRET_FILE=/data/auth/identity-hs256.key   # mounted read-only
ADAPTER_IDENTITY_ISSUER=vivesec-aibox-auth
ADAPTER_IDENTITY_AUDIENCE=vivesec-aibox-adapter
```

Administration from the shell (`manage.py --help`): `create-user`,
`update-user`, `reset-totp`, `unlock`, `reset-password`, `suspend`,
`activate`, `delete-user`, `list-users`, `events`; for the directory
`ldap-cert`, `ldap-password`, `ldap-map import|show` and `ldap-check`.
Passwords are read from the terminal or with `--password-stdin`, never from the
command line.

Every parameter is in [`CONFIGURATION.md`](../CONFIGURATION.md)
(Authentication service section).

## Tests

```powershell
Push-Location auth_service; python -m unittest discover -p "*_test.py"; Pop-Location
```

`auth_test.py` checks the issued tokens with the adapter's own verifier
(`adapter/identity.py`), so a contract change on either side fails the tests.

`gate_e2e_test.py` runs the login gate end to end: this service, the built web
UI server (`node .output/server/index.mjs`) and a stub adapter that verifies the
token with the adapter's verifier. It covers redirects, CSRF and origin checks,
cookie flags, enrolment with the QR code, the temporary password, the relayed
identity and room, dropped browser headers, logout and session replay. It needs
Node.js and `npm run build`, so it is skipped unless asked for:

```powershell
npm run build
$env:AIBOX_GATE_E2E = "1"
Push-Location auth_service; python -m unittest gate_e2e_test; Pop-Location
```

`ldap_client_test.py` runs the protocol against a scripted server and the TLS
checks (StartTLS, LDAPS, pin, CA + host name) against a local TLS server with
certificates made by `openssl` (skipped without it). `directory_test.py` covers
the directory login and the refresh with a fake domain.

`samba_ad_test.py` runs against a real Samba Active Directory: LDAPS and
StartTLS, the pin, nested groups, a non-ASCII DN, disabled and ungrouped
accounts, a wrong password, and a full login with TOTP and a token the adapter
accepts. It needs the test domain controller (docker; development box):

```bash
python3 samba_ad/samba_ad.py start     # AIBOX.TEST on 127.0.0.1:1636 (LDAPS) and :1389 (StartTLS)
AIBOX_SAMBA_CREDENTIALS=~/standalone/samba-ad/credentials.json python3 -m unittest samba_ad_test
python3 samba_ad/samba_ad.py env       # AUTH_LDAP_* for pointing a test box at it
python3 samba_ad/samba_ad.py map admin=/storage/drives/a/ analyst=/storage/drives/b/
python3 samba_ad/samba_ad.py remove    # container and domain
```

The test domain: `alice` (in `Team-A`, which is in `AIBox-Analysts`: nested),
`bob` (`AIBox-Admins`), `carol` (disabled), `dave` (no group), service account
`svc-aibox`; the generated passwords are in the credentials file (0600). It
runs unprivileged (`xattr_tdb` instead of security xattrs) and only on
loopback. Never point a production box at it.
