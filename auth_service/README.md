# Authentication service (E27 S3, standalone mode)

In standalone mode (`ADAPTER_INTEGRATION=standalone`) the box has no ViVeSecBox
to vouch for its users, so it authenticates them itself: local users with a
password and a TOTP authenticator app. AD/LDAP users follow in S6.

```text
Browser ──HTTPS──▶ nginx ──▶ web UI server (login gate, S4) ──loopback, service key──▶ auth_service
                                      │                                                    │
                                      └──loopback, JWT (VVS-Identity)──▶ adapter ◀── same HS256 key file
```

Pure standard-library Python (`ThreadingHTTPServer`, `sqlite3`, `hashlib.scrypt`),
like the adapter; no `requirements.txt` until the LDAP client arrives in S6.

| File | Purpose |
| --- | --- |
| `service.py` | HTTP front: loopback only, `X-Auth-Service-Key` required, JSON in/out |
| `auth.py` | Login, sessions, identity tokens, password change, administration |
| `store.py` | SQLite: users, sessions, login challenges, event log |
| `passwords.py` | scrypt hashing (N=2^15, r=8, p=1) and the password policy |
| `totp.py` | RFC 6238 (SHA-1, 6 digits, 30 s, ±1 step), replay protection |
| `tokens.py` | HS256 JWT signing, key files |
| `config.py` | `AUTH_*` settings |
| `manage.py` | Administration from the box's shell |

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
most `AUTH_TOKEN_TTL_SECONDS`).

## Setup on the box

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
`activate`, `delete-user`, `list-users`, `events`. Passwords are read from the
terminal or with `--password-stdin`, never from the command line.

Every parameter is in [`CONFIGURATION.md`](../CONFIGURATION.md)
(Authentication service section).

## Tests

```powershell
Push-Location auth_service; python -m unittest discover -p "*_test.py"; Pop-Location
```

`auth_test.py` checks the issued tokens with the adapter's own verifier
(`adapter/identity.py`), so a contract change on either side fails the tests.
