"""Who is asking: verification of the identity the ViVeSecBox asserts (E02).

The ViVeSecBox owns users and login; the AI Box never authenticates anyone
itself. What it can do is refuse an identity it cannot trust. Today the box
sends an opaque `VVS-User` header, which is only as trustworthy as the channel
it arrived on. Once the ViVeSecBox signs a short-lived assertion (a JWT in the
`VVS-Identity` header by default), this module verifies it:

* signature: HS256 (shared secret file) or RS256 (public keys from a JWKS
  file), restricted to an allowlist -- `alg=none` and anything unlisted is
  rejected, and each algorithm only ever uses its own key material, so an RSA
  public key can never be replayed as an HMAC secret;
* lifetime: `exp` is mandatory, `nbf`/`iat` are honoured, and a token valid for
  longer than the configured maximum is refused;
* issuer, audience and scope, when configured;
* binding: the asserted user must match `VVS-User`, a `drive` claim must match
  `VVS-Drive` and a `sid` claim must match `VVS-Session` when those are sent;
  a `drives` claim replaces the unsigned `VVS-Other-Drives` header.

Modes (`ADAPTER_IDENTITY_MODE`):
  off      -- the header identity is used as today (the default until the
              ViVeSecBox issues tokens);
  verify   -- a token, when present, must be valid; without one the header
              identity is accepted;
  require  -- every user request must carry a valid token.
An unrecognised mode falls back to `require`: a typo must not open the box.

Stdlib only: the RSA check is a PKCS#1 v1.5 verification done with `pow`.
"""
import base64
import hashlib
import hmac
import json
import os
import sys
import threading
import time

import corpus

MODE_OFF = "off"
MODE_VERIFY = "verify"
MODE_REQUIRE = "require"
MODES = (MODE_OFF, MODE_VERIFY, MODE_REQUIRE)

SOURCE_HEADER = "header"
SOURCE_TOKEN = "token"

SUPPORTED_ALGORITHMS = ("HS256", "RS256")
DEFAULT_HEADER = "VVS-Identity"
MIN_HS256_SECRET_BYTES = 32
MIN_RSA_BITS = 2048
MAX_TOKEN_BYTES = 16384

# ASN.1 DigestInfo prefix for SHA-256 (RFC 8017 sec 9.2, note 1).
_SHA256_DIGEST_INFO = bytes.fromhex("3031300d060960864801650304020105000420")


class IdentityError(Exception):
    """The request's identity cannot be trusted. `reason` is for the log only;
    the client gets a generic message so a probe learns nothing."""

    def __init__(self, reason, status=401):
        super().__init__(reason)
        self.reason = reason
        self.status = status


class Identity:
    """The caller of one request, as far as the box can tell."""

    def __init__(self, user, source, upn=None, groups=None, roles=None,
                 session=None, expires=None, drives=None):
        self.user = user or ""
        self.source = source
        self.upn = upn or None
        self.groups = tuple(groups or ())
        self.roles = tuple(roles or ())
        self.session = session or None
        self.expires = expires
        # Drives the signed assertion grants besides the active one; None when
        # the assertion does not speak about drives at all.
        self.drives = None if drives is None else tuple(drives)

    @property
    def directory_key(self):
        """The id the directory knows the user by: the UPN, or the user id
        itself when it already has the UPN/e-mail shape."""
        if self.upn:
            return self.upn
        return self.user if "@" in self.user else None

    def keys(self):
        """Every id this user can be addressed by (lifecycle lookups)."""
        return tuple(k for k in dict.fromkeys((self.user, self.upn)) if k)

    def as_dict(self):
        return {"user": self.user, "upn": self.upn, "source": self.source,
                "session": self.session, "expires": self.expires}

    def __repr__(self):
        return "Identity(%r, source=%s)" % (self.user, self.source)


def parse_mode(raw, warn=None):
    value = (raw or "").strip().lower()
    if not value:
        return MODE_OFF
    if value in MODES:
        return value
    if warn:
        warn("unknown ADAPTER_IDENTITY_MODE %r -> %s" % (raw, MODE_REQUIRE))
    return MODE_REQUIRE


def parse_algorithms(raw, warn=None):
    if raw is None or not raw.strip():
        return SUPPORTED_ALGORITHMS
    algorithms = []
    for item in raw.replace(";", ",").split(","):
        name = item.strip().upper()
        if not name:
            continue
        if name not in SUPPORTED_ALGORITHMS:
            if warn:
                warn("ignoring unsupported identity algorithm %r" % item.strip())
            continue
        if name not in algorithms:
            algorithms.append(name)
    return tuple(algorithms)


def b64url_decode(text):
    if not isinstance(text, str):
        raise ValueError("not a string")
    padded = text + "=" * ((-len(text)) % 4)
    try:
        return base64.urlsafe_b64decode(padded.encode("ascii"))
    except Exception as e:  # noqa: BLE001
        raise ValueError("bad base64url: %s" % e)


def b64url_encode(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_int(text):
    return int.from_bytes(b64url_decode(text), "big")


def rsa_pkcs1_sha256_verify(n, e, message, signature):
    """RSASSA-PKCS1-v1_5 verification with SHA-256 (RFC 8017 sec 8.2.2)."""
    k = (n.bit_length() + 7) // 8
    if len(signature) != k:
        return False
    s = int.from_bytes(signature, "big")
    if s >= n:
        return False
    em = pow(s, e, n).to_bytes(k, "big")
    t = _SHA256_DIGEST_INFO + hashlib.sha256(message).digest()
    if k < len(t) + 11:
        return False
    expected = b"\x00\x01" + b"\xff" * (k - len(t) - 3) + b"\x00" + t
    return hmac.compare_digest(em, expected)


class _FileCache:
    """A small file whose parsed content is reloaded when its mtime changes.
    An unreadable or malformed file yields None, never stale content."""

    def __init__(self, path, parse):
        self.path = path
        self._parse = parse
        self._mtime = None
        self._value = None
        self._lock = threading.Lock()

    def get(self):
        if not self.path:
            return None
        with self._lock:
            try:
                mtime = os.path.getmtime(self.path)
            except OSError:
                self._mtime, self._value = None, None
                return None
            if mtime != self._mtime:
                self._mtime = mtime
                try:
                    with open(self.path, "rb") as handle:
                        self._value = self._parse(handle.read())
                except (OSError, ValueError):
                    self._value = None
            return self._value


def _parse_secret(raw):
    secret = raw.strip()
    if len(secret) < MIN_HS256_SECRET_BYTES:
        raise ValueError("HS256 secret shorter than %d bytes" % MIN_HS256_SECRET_BYTES)
    return secret


def _parse_jwks(raw):
    data = json.loads(raw.decode("utf-8"))
    keys = data.get("keys") if isinstance(data, dict) else None
    if not isinstance(keys, list):
        raise ValueError("JWKS without a keys list")
    parsed = []
    for key in keys:
        if not isinstance(key, dict) or key.get("kty") != "RSA":
            continue
        if key.get("use") not in (None, "sig"):
            continue
        if key.get("alg") not in (None, "RS256"):
            continue
        try:
            n, e = _b64url_int(key["n"]), _b64url_int(key["e"])
        except (KeyError, ValueError):
            continue
        if n.bit_length() < MIN_RSA_BITS or e < 3 or e % 2 == 0:
            continue
        parsed.append({"kid": key.get("kid"), "n": n, "e": e})
    return parsed


def _as_list(value):
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value if item not in (None, "")]
    return [str(value)]


class Verifier:
    """Resolves the identity of one request according to the configured mode."""

    def __init__(self, mode=MODE_OFF, header=DEFAULT_HEADER,
                 algorithms=SUPPORTED_ALGORITHMS, hs256_secret_file=None,
                 jwks_file=None, issuer=None, audience=None, scope=None,
                 user_claim="sub", leeway=60, max_ttl=3600, clock=time.time):
        self.mode = mode
        self.header = header or DEFAULT_HEADER
        self.algorithms = tuple(algorithms)
        self.issuer = issuer or None
        self.audience = audience or None
        self.scope = scope or None
        self.user_claim = user_claim or "sub"
        self.leeway = max(0, int(leeway))
        self.max_ttl = max(1, int(max_ttl))
        self.clock = clock
        self._secret = _FileCache(hs256_secret_file, _parse_secret)
        self._jwks = _FileCache(jwks_file, _parse_jwks)

    @classmethod
    def from_env(cls, env=None, warn=None):
        env = env if env is not None else os.environ
        warn = warn or (lambda message: sys.stderr.write("[adapter] identity: %s\n" % message))

        def number(name, default):
            raw = (env.get(name) or "").strip()
            if not raw:
                return default
            try:
                return int(raw)
            except ValueError:
                warn("invalid %s=%r -> %d" % (name, raw, default))
                return default

        return cls(
            mode=parse_mode(env.get("ADAPTER_IDENTITY_MODE"), warn),
            header=(env.get("ADAPTER_IDENTITY_HEADER") or "").strip() or DEFAULT_HEADER,
            algorithms=parse_algorithms(env.get("ADAPTER_IDENTITY_ALGORITHMS"), warn),
            hs256_secret_file=(env.get("ADAPTER_IDENTITY_HS256_SECRET_FILE") or "").strip() or None,
            jwks_file=(env.get("ADAPTER_IDENTITY_JWKS_FILE") or "").strip() or None,
            issuer=(env.get("ADAPTER_IDENTITY_ISSUER") or "").strip() or None,
            audience=(env.get("ADAPTER_IDENTITY_AUDIENCE") or "").strip() or None,
            scope=(env.get("ADAPTER_IDENTITY_SCOPE") or "").strip() or None,
            user_claim=(env.get("ADAPTER_IDENTITY_USER_CLAIM") or "").strip() or "sub",
            leeway=number("ADAPTER_IDENTITY_LEEWAY_SECONDS", 60),
            max_ttl=number("ADAPTER_IDENTITY_MAX_TTL_SECONDS", 3600),
        )

    # -- configuration view ----------------------------------------------------
    def key_status(self):
        """Which configured algorithms have usable key material right now."""
        usable = []
        if "HS256" in self.algorithms and self._secret.get():
            usable.append("HS256")
        if "RS256" in self.algorithms and self._jwks.get():
            usable.append("RS256")
        return usable

    def settings(self):
        return {"mode": self.mode, "header": self.header,
                "algorithms": list(self.algorithms), "keys": self.key_status(),
                "issuer": bool(self.issuer), "audience": bool(self.audience),
                "scope": bool(self.scope), "max_ttl": self.max_ttl}

    # -- token verification ----------------------------------------------------
    def verify_token(self, token):
        """Return the claims of a valid token or raise IdentityError."""
        if not token or len(token) > MAX_TOKEN_BYTES:
            raise IdentityError("token missing or oversized")
        parts = token.split(".")
        if len(parts) != 3:
            raise IdentityError("token is not a compact JWS")
        try:
            header = json.loads(b64url_decode(parts[0]).decode("utf-8"))
            claims = json.loads(b64url_decode(parts[1]).decode("utf-8"))
            signature = b64url_decode(parts[2])
        except (ValueError, UnicodeDecodeError) as e:
            raise IdentityError("token not decodable: %s" % e)
        if not isinstance(header, dict) or not isinstance(claims, dict):
            raise IdentityError("token header/claims are not objects")
        if header.get("crit"):
            raise IdentityError("token uses unsupported critical headers")
        alg = header.get("alg")
        if alg not in self.algorithms:
            raise IdentityError("token algorithm %r not allowed" % (alg,))
        signing_input = (parts[0] + "." + parts[1]).encode("ascii")
        if alg == "HS256":
            self._verify_hs256(signing_input, signature)
        else:
            self._verify_rs256(header.get("kid"), signing_input, signature)
        self._check_claims(claims)
        return claims

    def _verify_hs256(self, signing_input, signature):
        secret = self._secret.get()
        if not secret:
            raise IdentityError("HS256 token but no usable secret configured")
        expected = hmac.new(secret, signing_input, hashlib.sha256).digest()
        if not hmac.compare_digest(expected, signature):
            raise IdentityError("bad HS256 signature")

    def _verify_rs256(self, kid, signing_input, signature):
        keys = self._jwks.get()
        if not keys:
            raise IdentityError("RS256 token but no usable JWKS configured")
        if kid is not None:
            candidates = [key for key in keys if key["kid"] == kid]
        else:
            candidates = keys if len(keys) == 1 else []
        if not candidates:
            raise IdentityError("no JWKS key for kid %r" % (kid,))
        for key in candidates:
            if rsa_pkcs1_sha256_verify(key["n"], key["e"], signing_input, signature):
                return
        raise IdentityError("bad RS256 signature")

    def _check_claims(self, claims):
        now = self.clock()

        def number(name):
            value = claims.get(name)
            if value is None:
                return None
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise IdentityError("claim %s is not numeric" % name)
            return float(value)

        exp, nbf, iat = number("exp"), number("nbf"), number("iat")
        if exp is None:
            raise IdentityError("token without exp")
        if now > exp + self.leeway:
            raise IdentityError("token expired")
        if nbf is not None and now + self.leeway < nbf:
            raise IdentityError("token not yet valid")
        if iat is not None and now + self.leeway < iat:
            raise IdentityError("token issued in the future")
        # Short-lived only: neither the declared lifetime nor what is left of it
        # may exceed the maximum, so a token without iat cannot dodge the cap.
        if iat is not None and exp - iat > self.max_ttl:
            raise IdentityError("token lifetime exceeds the maximum")
        if exp - now > self.max_ttl + self.leeway:
            raise IdentityError("token lifetime exceeds the maximum")
        if self.issuer and claims.get("iss") != self.issuer:
            raise IdentityError("unexpected issuer")
        if self.audience and self.audience not in _as_list(claims.get("aud")):
            raise IdentityError("unexpected audience")
        if self.scope:
            granted = set()
            for name in ("scope", "scp"):
                value = claims.get(name)
                if isinstance(value, str):
                    granted.update(value.split())
                else:
                    granted.update(_as_list(value))
            if self.scope not in granted:
                raise IdentityError("token scope does not grant %r" % self.scope)

    # -- request resolution ----------------------------------------------------
    def resolve(self, headers, drive=None):
        """The identity of a request, or IdentityError.

        `headers` needs only `.get`. `drive` is the decoded VVS-Drive of a
        drive-scoped endpoint; a token that names a different drive is refused.
        """
        header_user = (headers.get("VVS-User") or "").strip()
        header_session = (headers.get("VVS-Session") or "").strip() or None
        if self.mode == MODE_OFF:
            return Identity(header_user, SOURCE_HEADER, session=header_session)
        token = (headers.get(self.header) or "").strip()
        if token.lower().startswith("bearer "):
            token = token[7:].strip()
        if not token:
            if self.mode == MODE_REQUIRE:
                raise IdentityError("identity assertion required but missing")
            return Identity(header_user, SOURCE_HEADER, session=header_session)
        claims = self.verify_token(token)
        user = claims.get(self.user_claim)
        if not isinstance(user, str) or not user.strip():
            raise IdentityError("token without a %s claim" % self.user_claim)
        user = user.strip()
        if header_user and header_user != user:
            raise IdentityError("token user does not match VVS-User")
        token_drive = claims.get("drive")
        if token_drive is not None and drive is not None \
                and corpus.norm(str(token_drive)) != corpus.norm(drive):
            raise IdentityError("token drive does not match VVS-Drive", status=403)
        token_session = claims.get("sid")
        if token_session is not None and header_session and str(token_session) != header_session:
            raise IdentityError("token session does not match VVS-Session")
        upn = None
        for name in ("upn", "preferred_username", "email"):
            value = claims.get(name)
            if isinstance(value, str) and value.strip():
                upn = value.strip()
                break
        drives = claims.get("drives")
        if drives is not None and not isinstance(drives, list):
            raise IdentityError("token drives claim is not a list")
        return Identity(user, SOURCE_TOKEN, upn=upn,
                        groups=_as_list(claims.get("groups")),
                        roles=_as_list(claims.get("roles")),
                        session=str(token_session) if token_session is not None else header_session,
                        expires=claims.get("exp"),
                        drives=None if drives is None else _as_list(drives))


def sign_hs256(claims, secret, header=None):
    """Mint an HS256 token. Used by tests and the simulator, never by the box."""
    head = dict({"alg": "HS256", "typ": "JWT"}, **(header or {}))
    signing_input = (b64url_encode(json.dumps(head, separators=(",", ":")).encode("utf-8"))
                     + "." + b64url_encode(json.dumps(claims, separators=(",", ":")).encode("utf-8")))
    signature = hmac.new(secret, signing_input.encode("ascii"), hashlib.sha256).digest()
    return signing_input + "." + b64url_encode(signature)
