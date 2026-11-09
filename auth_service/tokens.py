"""Identity tokens for the adapter: compact HS256 JWTs.

The adapter verifies them with `ADAPTER_IDENTITY_HS256_SECRET_FILE` pointing at
the same key file (adapter/identity.py). The key is the stripped file content,
exactly as the adapter reads it, and must be at least 32 bytes long.
"""
import base64
import hashlib
import hmac
import json
import os
import secrets

MIN_SECRET_BYTES = 32


class KeyFileError(Exception):
    """A key file is missing or unusable."""


def b64url(data):
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def sign_hs256(claims, secret):
    header = {"alg": "HS256", "typ": "JWT"}
    signing_input = (b64url(json.dumps(header, separators=(",", ":")).encode("utf-8")) + "."
                     + b64url(json.dumps(claims, separators=(",", ":")).encode("utf-8")))
    signature = hmac.new(secret, signing_input.encode("ascii"), hashlib.sha256).digest()
    return signing_input + "." + b64url(signature)


def read_key(path, what):
    """The stripped content of a key file; KeyFileError when missing or short."""
    try:
        with open(path, "rb") as handle:
            key = handle.read().strip()
    except OSError as e:
        raise KeyFileError("%s %s is not readable: %s" % (what, path, e.strerror or e))
    if len(key) < MIN_SECRET_BYTES:
        raise KeyFileError("%s %s is shorter than %d bytes" % (what, path, MIN_SECRET_BYTES))
    return key


def create_key(path):
    """Write a new random key (64 hex characters) readable by the owner only.
    Never overwrites: returns False when the file already exists."""
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return False
    with os.fdopen(fd, "w", encoding="ascii") as handle:
        handle.write(secrets.token_hex(32) + "\n")
    return True
