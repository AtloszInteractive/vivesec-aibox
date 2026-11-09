"""Configuration of the authentication service, from AUTH_* environment
variables. An invalid or out-of-range number falls back to its default with a
warning; the defaults are the strict values. A listen address other than
loopback stops the service: only the web UI server on the same box may talk
to it.
"""
import os
import sys


class ConfigError(Exception):
    """The configuration is not usable; the service must not start."""


# name: (default, minimum, maximum)
NUMBERS = {
    "AUTH_PORT": (8097, 1, 65535),
    "AUTH_TOKEN_TTL_SECONDS": (300, 30, 3600),
    "AUTH_SESSION_IDLE_SECONDS": (1800, 60, 86400),
    "AUTH_SESSION_MAX_SECONDS": (43200, 300, 604800),
    "AUTH_LOCKOUT_THRESHOLD": (5, 3, 20),
    "AUTH_LOCKOUT_SECONDS": (900, 60, 86400),
    "AUTH_PASSWORD_MIN_LENGTH": (12, 10, 128),
    "AUTH_CLIENT_MAX_FAILURES": (20, 5, 1000),
    "AUTH_CLIENT_WINDOW_SECONDS": (900, 60, 86400),
}

DEFAULT_DATA_DIR = "/data/auth"
DEFAULT_ISSUER = "vivesec-aibox-auth"
DEFAULT_AUDIENCE = "vivesec-aibox-adapter"
DEFAULT_TOTP_ISSUER = "ViVeSec AI Box"
# A login is two steps; the second (TOTP) must follow within this time and
# within this many wrong codes.
CHALLENGE_SECONDS = 300
CHALLENGE_ATTEMPTS = 5


def is_loopback(host):
    host = (host or "").strip().lower()
    return host == "localhost" or host == "::1" or host.startswith("127.")


class Settings:
    def __init__(self, **values):
        self.host = values.get("host", "127.0.0.1")
        self.port = values.get("port", NUMBERS["AUTH_PORT"][0])
        self.data_dir = values.get("data_dir", DEFAULT_DATA_DIR)
        self.service_key_file = values.get("service_key_file") or os.path.join(self.data_dir, "service.key")
        self.jwt_secret_file = values.get("jwt_secret_file") or os.path.join(self.data_dir, "identity-hs256.key")
        self.db_path = os.path.join(self.data_dir, "auth.db")
        self.jwt_issuer = values.get("jwt_issuer", DEFAULT_ISSUER)
        self.jwt_audience = values.get("jwt_audience", DEFAULT_AUDIENCE)
        self.totp_issuer = values.get("totp_issuer", DEFAULT_TOTP_ISSUER)
        for name, (default, _low, _high) in NUMBERS.items():
            attribute = name[len("AUTH_"):].lower()
            setattr(self, attribute, values.get(attribute, default))
        self.session_idle_seconds = min(self.session_idle_seconds, self.session_max_seconds)
        self.challenge_seconds = values.get("challenge_seconds", CHALLENGE_SECONDS)
        self.challenge_attempts = values.get("challenge_attempts", CHALLENGE_ATTEMPTS)

    @classmethod
    def from_env(cls, env=None, warn=None):
        env = env if env is not None else os.environ
        warn = warn or (lambda message: sys.stderr.write("[auth] config: %s\n" % message))

        def text(name, default):
            return (env.get(name) or "").strip() or default

        values = {}
        for name, (default, low, high) in NUMBERS.items():
            raw = (env.get(name) or "").strip()
            value = default
            if raw:
                try:
                    value = int(raw)
                except ValueError:
                    warn("invalid %s=%r, using %d" % (name, raw, default))
                else:
                    if not low <= value <= high:
                        warn("%s=%d outside %d..%d, using %d" % (name, value, low, high, default))
                        value = default
            values[name[len("AUTH_"):].lower()] = value
        host = text("AUTH_HOST", "127.0.0.1")
        if not is_loopback(host):
            raise ConfigError("AUTH_HOST=%r is not a loopback address; the authentication "
                              "service must only be reachable from the box itself" % host)
        values.update(
            host=host,
            data_dir=text("AUTH_DATA_DIR", DEFAULT_DATA_DIR),
            service_key_file=text("AUTH_SERVICE_KEY_FILE", ""),
            jwt_secret_file=text("AUTH_JWT_SECRET_FILE", ""),
            jwt_issuer=text("AUTH_JWT_ISSUER", DEFAULT_ISSUER),
            jwt_audience=text("AUTH_JWT_AUDIENCE", DEFAULT_AUDIENCE),
            totp_issuer=text("AUTH_TOTP_ISSUER", DEFAULT_TOTP_ISSUER),
        )
        return cls(**values)
