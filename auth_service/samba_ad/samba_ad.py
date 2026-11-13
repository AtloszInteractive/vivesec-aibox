#!/usr/bin/env python3
"""Samba AD test domain controller on a development box (E27 S6).

    python3 samba_ad.py start           build the image, start vivesec-samba-ad, wait for LDAPS,
                                        write the credentials file, print the fingerprint
    python3 samba_ad.py env             AUTH_LDAP_* settings for the test domain (shell exports)
    python3 samba_ad.py map ROLE=DRIVE  the group map for the test domain (JSON), e.g.
                                        admin=/storage/drives/a/ analyst=/storage/drives/b/
    python3 samba_ad.py status | remove

Development and test only: the test domain AIBOX.TEST (see entrypoint.sh)
listens on loopback only (127.0.0.1:1636 LDAPS, 127.0.0.1:1389 LDAP +
StartTLS). The passwords are generated on the first start and kept in
AIBOX_SAMBA_DIR/credentials.json (0600, default ~/standalone/samba-ad); they
are also visible to `docker inspect`, which is acceptable for a test domain
only. `remove` deletes the container and its volume (the domain).
"""
import json
import os
import secrets
import string
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import ldap_client  # noqa: E402

CONTAINER = "vivesec-samba-ad"
IMAGE = "vivesec-samba-ad:test"
VOLUME = "vivesec-samba-ad"
LDAPS_PORT, LDAP_PORT = 1636, 1389
BASE_DN = "DC=aibox,DC=test"
SERVICE_DN = "CN=svc-aibox,OU=Service," + BASE_DN
GROUPS = {"admin": "CN=AIBox-Admins,OU=AIBox," + BASE_DN,
          "analyst": "CN=AIBox-Analysts,OU=AIBox," + BASE_DN}


def state_dir():
    return Path(os.environ.get("AIBOX_SAMBA_DIR") or Path.home() / "standalone" / "samba-ad")


def password():
    """Meets the AD complexity rule: upper, lower, digit and symbol."""
    alphabet = string.ascii_letters + string.digits
    core = "".join(secrets.choice(alphabet) for _ in range(20))
    return "Ab1-" + core


def credentials():
    path = state_dir() / "credentials.json"
    if path.exists():
        return json.loads(path.read_text())
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    data = {"admin_password": password(), "service_password": password(), "user_password": password()}
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as handle:
        json.dump(data, handle, indent=2)
    return data


def save(data):
    path = state_dir() / "credentials.json"
    fd = os.open(str(path), os.O_WRONLY | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        json.dump(data, handle, indent=2)


def docker(*args, check=True):
    result = subprocess.run(["docker", *args], text=True, capture_output=True)
    if check and result.returncode:
        raise SystemExit("docker %s failed:\n%s" % (args[0], result.stderr[-2000:]))
    return result


def start():
    data = credentials()
    print("BUILD " + IMAGE, flush=True)
    docker("build", "-q", "-t", IMAGE, str(HERE))
    if docker("inspect", CONTAINER, check=False).returncode == 0:
        docker("rm", "-f", CONTAINER)
    docker("run", "-d", "--name", CONTAINER, "--hostname", "dc01", "--restart", "unless-stopped",
           "--memory", "768m", "--pids-limit", "512",
           "-p", "127.0.0.1:%d:636" % LDAPS_PORT, "-p", "127.0.0.1:%d:389" % LDAP_PORT,
           "-v", VOLUME + ":/var/lib/samba/aibox",
           "-e", "SAMBA_ADMIN_PASSWORD=" + data["admin_password"],
           "-e", "SAMBA_SERVICE_PASSWORD=" + data["service_password"],
           "-e", "SAMBA_USER_PASSWORD=" + data["user_password"], IMAGE)
    url = "ldaps://127.0.0.1:%d" % LDAPS_PORT
    deadline = time.time() + 240
    while True:
        try:
            der = ldap_client.fetch_certificate(url, 5)
            break
        except (ldap_client.LdapError, OSError) as e:
            if time.time() > deadline:
                print(docker("logs", "--tail", "40", CONTAINER, check=False).stderr, file=sys.stderr)
                raise SystemExit("the test domain controller did not answer on %s: %s" % (url, e))
            time.sleep(3)
    data.update(ldaps_url=url, ldap_url="ldap://127.0.0.1:%d" % LDAP_PORT, base_dn=BASE_DN,
                service_dn=SERVICE_DN, pin=ldap_client.fingerprint(der), groups=GROUPS)
    save(data)
    print("SAMBA_AD_READY %s fingerprint %s" % (url, ldap_client.format_fingerprint(data["pin"])), flush=True)
    print("credentials: %s" % (state_dir() / "credentials.json"), flush=True)


def env():
    data = credentials()
    if "pin" not in data:
        raise SystemExit("start the test domain controller first")
    print("export AUTH_LDAP_URLS=%s" % data["ldaps_url"])
    print("export AUTH_LDAP_TLS_PINS=%s" % data["pin"])
    print("export AUTH_LDAP_BASE_DN='%s'" % data["base_dn"])
    print("export AUTH_LDAP_BIND_DN='%s'" % data["service_dn"])


def group_map(pairs):
    roles = {role: [] for role in GROUPS}
    for pair in pairs:
        role, _sep, drive = pair.partition("=")
        if role not in roles or not drive.startswith("/"):
            raise SystemExit("ROLE=DRIVE with ROLE in %s" % ", ".join(GROUPS))
        roles[role].append(drive)
    print(json.dumps({"groups": [{"dn": dn, "roles": [role]} for role, dn in GROUPS.items()],
                      "roles": roles}, indent=2))


def main(argv):
    if len(argv) == 2 and argv[1] == "start":
        start()
    elif len(argv) == 2 and argv[1] == "env":
        env()
    elif len(argv) >= 2 and argv[1] == "map":
        group_map(argv[2:])
    elif len(argv) == 2 and argv[1] == "status":
        result = docker("ps", "-a", "--filter", "name=^/%s$" % CONTAINER,
                        "--format", "{{.Status}} {{.Ports}}", check=False)
        print(result.stdout.strip() or "absent")
    elif len(argv) == 2 and argv[1] == "remove":
        docker("rm", "-f", CONTAINER, check=False)
        docker("volume", "rm", VOLUME, check=False)
        print("removed %s and its volume (the credentials file stays)" % CONTAINER)
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main(sys.argv)
