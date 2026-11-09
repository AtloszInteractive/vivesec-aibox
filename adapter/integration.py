"""Integration mode (E27): who owns users, login, files and permissions.

`ADAPTER_INTEGRATION`:
  vivesecbox  -- the default and today's behaviour. The ViVeSecBox owns users,
                 login, files and drive permissions; it pairs with the box
                 (`/api/v1/init/*`, mutual TLS), syncs the index
                 (`/api/v1/index/*`), serves files over ws-fs and finds the box
                 by SSDP.
  standalone  -- the box works without a ViVeSecBox (offline deployment). Users
                 log in through the box's own login gate, which hands the
                 adapter a short-lived signed token; none of the ViVeSecBox
                 surfaces exist.

An unknown value stops the adapter. Falling back to either mode is unsafe:
`vivesecbox` trusts the identity it is told in the headers, and `standalone`
would silently drop the integration a paired box depends on.

Standalone mode has preconditions, checked before the adapter starts:
  * `ADAPTER_IDENTITY_MODE=require` -- every user request carries a verified
    token; there is no trusted ViVeSecBox to vouch for a bare `VVS-User`.
  * `ADAPTER_DIRECTORY=token` -- groups and roles come from that token.
  * `ADAPTER_SCOPE_ALL_DRIVES` off -- the demo switch grants every drive to
    every user; in standalone mode the token's `drives` claim decides.
"""
import os

import corpus
import directory
import identity

ENV_NAME = "ADAPTER_INTEGRATION"
MODE_VIVESECBOX = "vivesecbox"
MODE_STANDALONE = "standalone"
MODES = (MODE_VIVESECBOX, MODE_STANDALONE)
DEFAULT_MODE = MODE_VIVESECBOX

# Endpoints that exist only for the ViVeSecBox: pairing and the ws-fs channel
# (exact paths) and the index sync (everything under the prefix).
VIVESECBOX_PATHS = frozenset([
    "/api/v1/init/prepare",
    "/api/v1/init/commit",
    "/api/v1/ws/fs",
])
VIVESECBOX_PREFIXES = ("/api/v1/init/", "/api/v1/index/")

_TRUE_STRINGS = ("1", "true", "yes", "on")


class ConfigError(Exception):
    """The integration configuration is not usable; the adapter must not start."""


def parse_mode(raw):
    """The mode named by `raw`; unset or empty is the default. Anything else
    raises ConfigError -- there is no safe mode to fall back to."""
    value = (raw or "").strip().lower()
    if not value:
        return DEFAULT_MODE
    if value in MODES:
        return value
    raise ConfigError("unknown %s=%r (expected one of: %s)"
                      % (ENV_NAME, raw, ", ".join(MODES)))


def _normalized(env, name):
    return (env.get(name) or "").strip().lower()


def standalone_problems(env):
    """Why `env` cannot run in standalone mode; empty when it can."""
    problems = []
    if _normalized(env, "ADAPTER_IDENTITY_MODE") != identity.MODE_REQUIRE:
        problems.append("ADAPTER_IDENTITY_MODE must be %r in standalone mode (is %r)"
                        % (identity.MODE_REQUIRE, env.get("ADAPTER_IDENTITY_MODE") or ""))
    if _normalized(env, "ADAPTER_DIRECTORY") != directory.SOURCE_TOKEN:
        problems.append("ADAPTER_DIRECTORY must be %r in standalone mode (is %r)"
                        % (directory.SOURCE_TOKEN, env.get("ADAPTER_DIRECTORY") or ""))
    if _normalized(env, "ADAPTER_SCOPE_ALL_DRIVES") in _TRUE_STRINGS:
        problems.append("ADAPTER_SCOPE_ALL_DRIVES must be off in standalone mode "
                        "(the token's drives claim decides the scope)")
    return problems


def from_env(env=None):
    """The configured mode, or ConfigError listing every problem found."""
    env = env if env is not None else os.environ
    mode = parse_mode(env.get(ENV_NAME))
    if mode == MODE_STANDALONE:
        problems = standalone_problems(env)
        if problems:
            raise ConfigError("; ".join(problems))
    return mode


def is_vivesecbox_path(path):
    """True for an endpoint that only the ViVeSecBox integration serves."""
    path = (path or "").rstrip("/")
    if path in VIVESECBOX_PATHS:
        return True
    return any(path == prefix.rstrip("/") or path.startswith(prefix)
               for prefix in VIVESECBOX_PREFIXES)


def vivesecbox_surfaces(mode):
    """Whether pairing, index sync, ws-fs, SSDP and the mutual-TLS listener
    are served in `mode`."""
    return mode == MODE_VIVESECBOX


def drive_granted(mode, drive, token_drives):
    """Whether a request may name `drive` as its active VVS-Drive.

    The scope always includes the active drive, so in standalone mode it must
    be one of the rooms the signed token grants; a token without a `drives`
    claim grants none. In vivesecbox mode the ViVeSecBox picks the drive.
    """
    if mode != MODE_STANDALONE or drive is None:
        return True
    if token_drives is None:
        return False
    wanted = corpus.norm(drive)
    return any(corpus.norm(str(granted)) == wanted for granted in token_drives if granted)
