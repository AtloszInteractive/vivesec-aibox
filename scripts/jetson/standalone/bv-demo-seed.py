#!/usr/bin/env python3
"""Load the BV demo dataset (graph-corpus/bv_demo.py) into a standalone AI Box.

    python3 bv-demo-seed.py status [bv-demo.tgz]
    python3 bv-demo-seed.py users --prefix tkrisztian      # the three demo accounts
    python3 bv-demo-seed.py load bv-demo.tgz               # rooms, uploads, structured import
    python3 bv-demo-seed.py reprocess bv-demo.tgz          # every document through the model
    python3 bv-demo-seed.py all bv-demo.tgz --prefix tkrisztian

Everything goes through the box's own API as an operator would, so the same
checks apply: rooms are created with the admin endpoint, files are uploaded
into the store (virus scan, index, graph), the periodic exports are imported
by a graph administrator (E28/E29), and a reprocessing run reads the documents
the graph only got rules and tables of during the bulk upload (the deferred
"partial" ones) with the language model. Each step is idempotent: run it again
after an interruption.

Like structured_import_run.py, it signs short-lived identity tokens with the
running adapter's own HS256 key (whoever runs this already controls docker, and
with it that key): the --admin user (default admin, who must be in
ADAPTER_GRAPH_ADMINS) with the admin role and the demo rooms. Standalone mode
(E27) only; Python 3.8+, stdlib only.

The demo accounts (`users`), all with every demo room and the analyst role:
    <prefix>-orszagos    group bv-orszagos: every institution's records
    <prefix>-fefo        groups bv-orszagos and bv-fefo: also the informants (E29)
    <prefix>-intezet01   group bv-intezet-01: only institution 01's records
Each gets a temporary password, printed once; the first login asks for a new
one and enrols the authenticator app (TOTP).
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import secrets
import subprocess
import sys
import tarfile
import time
import urllib.error
import urllib.parse
import urllib.request

SOURCES = ("fonix_base", "placement", "contacts", "intel_log", "odin")
# Unfinished upload states (adapter drivestore.py); the newest uploads come first.
PENDING = ("queued", "waiting_scanner", "indexing", "index_pending")
BATCH = 150
ACCOUNTS = (
    ("orszagos", "országos elemző", ["bv-orszagos"]),
    ("fefo", "FEFO", ["bv-orszagos", "bv-fefo"]),
    ("intezet01", "intézeti elemző, 01", ["bv-intezet-01"]),
)


def docker(*args, stdin=None):
    return subprocess.run(("docker",) + args, input=stdin, capture_output=True, text=True)


def b64url(raw):
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


class Box:
    def __init__(self, args):
        self.url = args.adapter.rstrip("/")
        self.admin = args.admin
        self.container = args.container
        out = docker("inspect", "-f", "{{json .Config.Env}}", self.container)
        if out.returncode:
            raise SystemExit("cannot inspect %s: %s" % (self.container, out.stderr.strip()))
        self.env = dict(e.partition("=")[::2] for e in json.loads(out.stdout) or [])
        if (self.env.get("ADAPTER_INTEGRATION") or "").strip().lower() != "standalone":
            raise SystemExit("the adapter is not in standalone mode")
        key = docker("exec", self.container, "cat", self.env["ADAPTER_IDENTITY_HS256_SECRET_FILE"])
        if key.returncode:
            raise SystemExit("cannot read the adapter's signing key: %s" % key.stderr.strip())
        self.key = key.stdout.strip().encode()
        self.prefix = (self.env.get("ADAPTER_DRIVE_PREFIX") or "/storage/drives").rstrip("/")
        self.rooms = []

    def room_path(self, room):
        return "%s/%s" % (self.prefix, room)

    def headers(self, drive=None, content_type="application/json"):
        now = int(time.time())
        claims = {"sub": self.admin, "upn": self.admin, "roles": ["admin"],
                  "drives": [self.room_path(r) + "/" for r in self.rooms],
                  "iat": now, "nbf": now, "exp": now + 120, "jti": b64url(os.urandom(9))}
        for claim, name in (("iss", "ADAPTER_IDENTITY_ISSUER"), ("aud", "ADAPTER_IDENTITY_AUDIENCE")):
            if self.env.get(name):
                claims[claim] = self.env[name]
        signing = (b64url(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
                   + "." + b64url(json.dumps(claims, separators=(",", ":")).encode()))
        token = signing + "." + b64url(hmac.new(self.key, signing.encode(), hashlib.sha256).digest())
        out = {"Content-Type": content_type, "VVS-User": self.admin,
               self.env.get("ADAPTER_IDENTITY_HEADER") or "VVS-Identity": token}
        if drive:
            out["VVS-Drive"] = base64.urlsafe_b64encode(
                (self.room_path(drive) + "/").encode("utf-8")).decode("ascii")
        return out

    def call(self, method, path, body=None, drive=None, raw=None, extra=None, timeout=600):
        data = raw if raw is not None else (json.dumps(body).encode("utf-8")
                                            if body is not None else None)
        headers = self.headers(drive, "application/octet-stream" if raw is not None
                               else "application/json")
        headers.update(extra or {})
        req = urllib.request.Request(self.url + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                text = resp.read().decode("utf-8", "replace")
                return resp.status, (json.loads(text) if text else {})
        except urllib.error.HTTPError as e:
            text = e.read().decode("utf-8", "replace")
            try:
                return e.code, json.loads(text)
            except ValueError:
                return e.code, {"error": text[:300]}


def open_bundle(path):
    tar = tarfile.open(path, "r:gz", encoding="utf-8")
    demo = json.load(tar.extractfile("demo.json"))
    return tar, demo


def step(text):
    print("== " + text, flush=True)


def ensure_rooms(box):
    status, out = box.call("GET", "/api/v1/ui/admin/rooms")
    if status != 200:
        raise SystemExit("admin rooms: %s %s" % (status, out))
    known = {r["name"]: r for r in out.get("rooms", [])}
    for room in box.rooms:
        have = known.get(room)
        if have and not have.get("stored"):
            raise SystemExit("room %r exists only as a synced corpus; pick other room names" % room)
        if have:
            print("room %s: exists (%d files)" % (room, have.get("files", 0)))
            continue
        status, out = box.call("POST", "/api/v1/ui/admin/rooms/create", {"name": room})
        if status != 200:
            raise SystemExit("create room %s: %s %s" % (room, status, out))
        print("room %s: created" % room)


def upload_all(box, tar):
    counts = {"sent": 0, "exists": 0, "failed": 0}
    for member in tar:
        parts = member.name.split("/")
        if not member.isfile() or len(parts) < 3 or parts[0] != "rooms":
            continue
        room, rel = parts[1], "/".join(parts[2:])
        target = "%s/%s" % (box.room_path(room), rel)
        data = tar.extractfile(member).read()
        status, out = box.call("POST", "/api/v1/ui/store/upload", raw=data, drive=room, extra={
            "X-Store-Path": urllib.parse.quote(target, safe="/")})
        if status == 202:
            counts["sent"] += 1
            if counts["sent"] % BATCH == 0:
                # The store keeps the last 2000 upload records: let each batch finish.
                wait_uploads(box, quiet=True)
        elif status == 409 and out.get("error") in ("exists", "pending"):
            counts["exists"] += 1
        else:
            counts["failed"] += 1
            print("upload %s: %s %s" % (target, status, out.get("error")), flush=True)
        done = sum(counts.values())
        if done % 200 == 0:
            print("  uploads: %s" % counts, flush=True)
    print("uploads: %s" % counts, flush=True)
    return counts


def upload_states(box):
    status, out = box.call("GET", "/api/v1/ui/store/uploads", drive=box.rooms[0])
    if status != 200:
        raise SystemExit("uploads: %s %s" % (status, out))
    states = {}
    for item in out.get("uploads", []):
        states[item.get("status")] = states.get(item.get("status"), 0) + 1
    return states


def wait_uploads(box, poll=15, quiet=False):
    while True:
        states = upload_states(box)
        if not quiet:
            print("  upload states: %s" % states, flush=True)
        if not any(states.get(s) for s in PENDING):
            return states
        time.sleep(poll)


def check_rooms(box, demo):
    status, out = box.call("GET", "/api/v1/ui/admin/rooms")
    files = {r["name"]: r.get("files", 0) for r in out.get("rooms", [])} if status == 200 else {}
    for room, expected in sorted(demo["documents_by_room"].items()):
        print("room %s: %d indexed files of %d" % (room, files.get(room, 0), expected))


def import_exports(box, tar, demo):
    room, _, folder_rel = demo["export_folder"].partition("/")
    folder = "%s/%s" % (box.room_path(room), folder_rel)
    status, out = box.call("POST", "/api/v1/ui/store/mkdir", {"path": folder}, drive=room)
    if status != 200:
        raise SystemExit("mkdir %s: %s %s" % (folder, status, out))
    for source in SOURCES:
        content = tar.extractfile("exports/%s.csv" % source).read()
        body = {"source": source, "as_of": demo["as_of"], "folder": folder,
                "filename": "%s_%s.csv" % (source, demo["as_of"]),
                "content_b64": base64.b64encode(content).decode("ascii"), "wait": 300}
        started = time.time()
        status, out = box.call("POST", "/api/v1/ui/graph/import", body, drive=room, timeout=900)
        item = out.get("import") or {}
        while status == 200 and item.get("status") not in ("done", "failed", "refused"):
            status, out = box.call("POST", "/api/v1/ui/graph/import/status",
                                   {"job": item.get("job"), "wait": 300}, drive=room, timeout=900)
            item = out.get("import") or item
        stats = item.get("stats") or {}
        print("import %s: %s %s in %ds %s rag=%s" % (
            source, status, item.get("status") or out.get("error"), time.time() - started,
            {k: stats.get(k) for k in ("records", "created", "updated", "unchanged", "problems",
                                       "protected", "review_new")},
            (out.get("rag") or {}).get("state")), flush=True)
        if status != 200 or item.get("status") != "done":
            raise SystemExit("import of %s failed: %s" % (source, out))


def reprocess(box):
    room = box.rooms[0]
    status, out = box.call("POST", "/api/v1/ui/graph/reprocess/status", {}, drive=room)
    if status != 200:
        raise SystemExit("reprocess status: %s %s" % (status, out))
    run = out.get("run") or {}
    print("graph: current %s, stale %s (partial %s), absent %s, source %s ready=%s, run %s" % (
        out.get("current"), out.get("stale_total"), out.get("partial_total"),
        out.get("absent_total"), out.get("source"), out.get("source_ready"), run.get("state")))
    if run.get("state") == "running":
        print("a run is going: %s/%s" % (run.get("done"), run.get("total")))
        return
    if not (out.get("stale_total") or out.get("absent_total")):
        print("nothing to reprocess")
        return
    status, out = box.call("POST", "/api/v1/ui/graph/reprocess/start", {}, drive=room)
    if status != 200:
        raise SystemExit("reprocess start: %s %s" % (status, out))
    print("reprocessing %s documents; follow it with `status` or in the UI "
          "(Relationships > Processing)" % (out.get("run") or {}).get("total"))


def status_report(box):
    status, out = box.call("GET", "/api/v1/ui/admin/rooms")
    rooms = {r["name"]: r for r in out.get("rooms", [])} if status == 200 else {}
    for room in box.rooms:
        r = rooms.get(room)
        print("room %s: %s" % (room, "%d indexed files" % r["files"] if r else "missing"))
    if all(room in rooms for room in box.rooms):
        print("uploads: %s" % upload_states(box))
        status, out = box.call("POST", "/api/v1/ui/graph/reprocess/status", {}, drive=box.rooms[0])
        run = out.get("run") or {}
        print("graph: counts %s, stale %s (partial %s), absent %s; run %s %s/%s" % (
            out.get("counts"), out.get("stale_total"), out.get("partial_total"),
            out.get("absent_total"), run.get("state"), run.get("done"), run.get("total")))


def users(box, prefix, reset):
    drives = [box.room_path(r) + "/" for r in box.rooms]
    passwords = {}
    for suffix, title, groups in ACCOUNTS:
        name = "%s-%s" % (prefix, suffix)
        common = ["--display-name", "%s (%s)" % (prefix, title)]
        for g in groups:
            common += ["--group", g]
        for d in drives:
            common += ["--drive", d]
        manage = ("exec", "-i", "vivesec-auth", "python", "/app/auth_service/manage.py")
        password = secrets.token_urlsafe(12)
        out = docker(*manage, "create-user", name, "--role", "analyst", *common,
                     "--temporary", "--password-stdin", stdin=password + "\n")
        if out.returncode == 0:
            passwords[name] = password
            print("user %s: created" % name)
            continue
        if "exists" not in (out.stdout + out.stderr):
            raise SystemExit("create %s: %s" % (name, (out.stdout + out.stderr).strip()))
        out = docker(*manage, "update-user", name, "--no-roles", "--role", "analyst",
                     "--no-groups", "--no-drives", *common)
        if out.returncode:
            raise SystemExit("update %s: %s" % (name, (out.stdout + out.stderr).strip()))
        print("user %s: exists, groups and rooms updated" % name)
        if reset:
            out = docker(*manage, "reset-password", name, "--temporary", "--password-stdin",
                         stdin=password + "\n")
            if out.returncode:
                raise SystemExit("reset %s: %s" % (name, (out.stdout + out.stderr).strip()))
            passwords[name] = password
    if passwords:
        print("\nTemporary passwords (shown once; the first login changes them):")
        for name, password in passwords.items():
            print("  %s  %s" % (name, password))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("command", choices=("status", "users", "load", "reprocess", "all"))
    ap.add_argument("bundle", nargs="?", help="bv-demo.tgz from graph-corpus/bv_demo.py")
    ap.add_argument("--adapter", default="http://127.0.0.1:8088")
    ap.add_argument("--admin", default="admin", help="a user in ADAPTER_GRAPH_ADMINS")
    ap.add_argument("--container", default="vivesec-adapter")
    ap.add_argument("--prefix", default="demo", help="demo account name prefix")
    ap.add_argument("--reset-passwords", action="store_true",
                    help="users: new temporary passwords for existing accounts too")
    ap.add_argument("--rooms", default="Nyilvántartás,Kommunikáció,Jelentések,Egyéb",
                    help="without a bundle: the demo rooms")
    args = ap.parse_args()
    box = Box(args)
    tar, demo = (open_bundle(args.bundle) if args.bundle else (None, None))
    box.rooms = list(demo["rooms"]) if demo else [r for r in args.rooms.split(",") if r]
    if args.command in ("load", "all") and tar is None:
        raise SystemExit("%s needs the bundle" % args.command)
    if args.command == "status":
        status_report(box)
        return
    if args.command in ("users", "all"):
        step("rooms")
        ensure_rooms(box)
        step("demo accounts")
        users(box, args.prefix, args.reset_passwords)
    if args.command in ("load", "all"):
        if args.command == "load":
            step("rooms")
            ensure_rooms(box)
        step("uploads (%d documents)" % demo["documents"])
        upload_all(box, tar)
        step("waiting for scan and index")
        wait_uploads(box)
        check_rooms(box, demo)
        tar.close()
        tar, demo = open_bundle(args.bundle)
        step("structured import: %s" % demo["records"])
        import_exports(box, tar, demo)
    if args.command in ("reprocess", "all"):
        step("graph reprocessing")
        reprocess(box)


if __name__ == "__main__":
    main()
