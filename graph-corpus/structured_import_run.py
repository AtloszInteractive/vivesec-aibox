"""Import periodic exports into the graph through a live adapter (E28).

A graph administrator's call to /api/v1/ui/graph/import for each file: the
adapter checks the caller and the folder, the graph reads the rows by the
profile's mapping (structured_sources) and answers with what changed. Without
files, the synthetic exports of structured_samples.py are imported (the box
check of the contact linking).

    python3 structured_import_run.py --adapter http://127.0.0.1:8088 --user <graph admin> \
        --drive /storage/drives/<drive> --folder /storage/drives/<drive>/<folder> \
        [--file contacts=export.csv --as-of 2026-11-01] [--mode full] [--force]

The user must be in ADAPTER_GRAPH_ADMINS; the records are visible to callers who
may read the folder and whom each record's own rule admits (bv: the group of
its institution, group:bv-intezet-<code>, or group:bv-orszagos).

In standalone mode (E27) the adapter accepts only a signed identity token, so
--identity auto (the default) reads the running adapter container's settings
and, when it is standalone, signs a two-minute token for the user and the drive
with the adapter's own HS256 key, as 172-release-box.py verify does (whoever
runs this already controls docker, and with it that key). --identity off sends
the plain VVS headers (ViVeSecBox mode).
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import structured_samples  # noqa: E402


def b64(text):
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii")


def _docker(*args):
    return subprocess.check_output(("docker",) + args, text=True)


def adapter_env(container):
    entries = json.loads(_docker("inspect", "-f", "{{json .Config.Env}}", container))
    return dict(entry.partition("=")[::2] for entry in entries or [])


def _b64url(raw):
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def headers_for(user, drive, env=None, container="vivesec-adapter", ttl=120):
    """Request headers for `user` on `drive`; with a standalone adapter's env, a
    short-lived token signed with its key and naming only that drive."""
    drive = drive.rstrip("/") + "/"
    headers = {"Content-Type": "application/json", "VVS-User": user, "VVS-Drive": b64(drive)}
    if not env or (env.get("ADAPTER_INTEGRATION") or "").strip().lower() != "standalone":
        return headers
    key = _docker("exec", container, "cat", env["ADAPTER_IDENTITY_HS256_SECRET_FILE"]).strip().encode()
    now = int(time.time())
    claims = {"sub": user, "upn": user, "drives": [drive], "iat": now, "nbf": now, "exp": now + ttl,
              "jti": _b64url(os.urandom(9))}
    for claim, name in (("iss", "ADAPTER_IDENTITY_ISSUER"), ("aud", "ADAPTER_IDENTITY_AUDIENCE")):
        if env.get(name):
            claims[claim] = env[name]
    signing_input = (_b64url(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
                     + "." + _b64url(json.dumps(claims, separators=(",", ":")).encode()))
    signature = _b64url(hmac.new(key, signing_input.encode(), hashlib.sha256).digest())
    headers[env.get("ADAPTER_IDENTITY_HEADER") or "VVS-Identity"] = signing_input + "." + signature
    return headers


def post(adapter, path, headers, body, timeout=600):
    req = urllib.request.Request(
        adapter.rstrip("/") + path, data=json.dumps(body).encode("utf-8"), method="POST",
        headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, {"ok": False, "error": e.read().decode("utf-8", "replace")[:500]}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--adapter", default="http://127.0.0.1:8088")
    ap.add_argument("--user", required=True)
    ap.add_argument("--drive", required=True)
    ap.add_argument("--folder", required=True)
    ap.add_argument("--file", action="append", default=[],
                    help="source=path (CSV/XLSX); repeatable. None: the synthetic samples")
    ap.add_argument("--as-of", default=structured_samples.AS_OF)
    ap.add_argument("--mode", choices=("delta", "full"))
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--identity", choices=("auto", "off"), default="auto",
                    help="auto: sign a token when the adapter container runs standalone")
    ap.add_argument("--container", default="vivesec-adapter")
    args = ap.parse_args()
    env = adapter_env(args.container) if args.identity == "auto" else None
    jobs = []
    for item in args.file:
        source, _, path = item.partition("=")
        with open(path, "rb") as f:
            jobs.append((source, os.path.basename(path), f.read()))
    if not jobs:
        for source, (header, rows) in structured_samples.exports().items():
            jobs.append((source, source + ".csv", structured_samples.to_csv(header, rows)))
    failed = False
    for source, name, content in jobs:
        body = {"source": source, "as_of": args.as_of, "folder": args.folder, "filename": name,
                "content_b64": base64.b64encode(content).decode("ascii"), "force": args.force,
                "wait": 300}
        if args.mode:
            body["mode"] = args.mode
        # A fresh token per file: an import may outlast one token's lifetime.
        status, out = post(args.adapter, "/api/v1/ui/graph/import",
                           headers_for(args.user, args.drive, env, args.container), body)
        print(json.dumps({"source": source, "file": name, "status": status,
                          "result": out.get("import") or out}, ensure_ascii=False))
        failed = failed or status != 200
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
