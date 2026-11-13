"""Time-based one-time passwords (RFC 6238, HMAC-SHA1, 6 digits, 30 s).

These are the parameters every authenticator app supports. A code is accepted
in the current time step and one step either side (+-30 s of clock drift), and
never again once used: the caller stores the last accepted step and passes it
back as `last_step`.
"""
import base64
import hashlib
import hmac
import secrets
import struct
import urllib.parse

PERIOD = 30
DIGITS = 6
SECRET_BYTES = 20
WINDOW = 1


def new_secret():
    """A fresh shared secret, base32 without padding (what the apps expect)."""
    return base64.b32encode(secrets.token_bytes(SECRET_BYTES)).decode("ascii").rstrip("=")


def secret_bytes(secret):
    text = secret.strip().replace(" ", "").upper()
    return base64.b32decode(text + "=" * (-len(text) % 8))


def hotp(key, counter, digits=DIGITS, digest=hashlib.sha1):
    """RFC 4226 HOTP value of `counter` for the raw `key` bytes."""
    mac = hmac.new(key, struct.pack(">Q", counter), digest).digest()
    offset = mac[-1] & 0x0F
    value = struct.unpack(">I", mac[offset:offset + 4])[0] & 0x7FFFFFFF
    return "%0*d" % (digits, value % 10 ** digits)


def step_at(timestamp):
    return int(timestamp // PERIOD)


def code_at(secret, timestamp):
    return hotp(secret_bytes(secret), step_at(timestamp))


def normalize_code(code):
    if not isinstance(code, str):
        return None
    code = code.replace(" ", "").replace("-", "")
    # isdigit() alone also accepts non-ASCII digits, which compare_digest refuses.
    if len(code) != DIGITS or not (code.isascii() and code.isdigit()):
        return None
    return code


def match(secret, code, now, last_step=None, window=WINDOW):
    """The time step `code` belongs to, or None. Steps at or before
    `last_step` are refused, so a code cannot be replayed."""
    code = normalize_code(code)
    if code is None:
        return None
    key = secret_bytes(secret)
    current = step_at(now)
    found = None
    # Every candidate step is computed, so the time taken does not depend on
    # which one matched.
    for step in range(current - window, current + window + 1):
        if step < 0:
            continue
        if hmac.compare_digest(hotp(key, step), code) and found is None:
            found = step
    if found is None or (last_step is not None and found <= last_step):
        return None
    return found


def provisioning_uri(secret, account, issuer):
    """The otpauth:// URI an authenticator app reads from the QR code."""
    label = urllib.parse.quote(issuer, safe="") + ":" + urllib.parse.quote(account, safe="@.")
    query = urllib.parse.urlencode({"secret": secret, "issuer": issuer, "algorithm": "SHA1",
                                    "digits": DIGITS, "period": PERIOD},
                                   quote_via=urllib.parse.quote)
    return "otpauth://totp/%s?%s" % (label, query)
