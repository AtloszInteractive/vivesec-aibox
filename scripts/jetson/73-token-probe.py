#!/usr/bin/env python3
"""Replay the ViVeSecBox's exact check call for the real drive file and compare
the token it hands back with the one the box keeps retrying."""
import base64
import json
import urllib.error
import urllib.request

BASE = "http://127.0.0.1"
PATH = "/storage/drives/aiboxdev/Decision_Memo_Alternative_Cell_Supplier.md"
SIZE = 1777
MTIME = 1785925123155229614
BOX_TOKEN = "9d60db21a8eef7e039055bdd529c0c0499529912"


def post(url, data, ctype):
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", ctype)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


head = base64.b64encode(b"# Decision Memo\n").decode("ascii")
st, out = post(BASE + "/api/v1/index/upsert/file/check",
               json.dumps({"path": PATH, "size": SIZE, "mtime": MTIME,
                           "head": head}).encode(), "application/json")
print("check ->", st, out[:300])
tok = json.loads(out).get("token") if st == 200 else None
print("token from check :", tok)
print("token box retries:", BOX_TOKEN)
print("MATCH" if tok == BOX_TOKEN else "DIFFERENT")

if tok:
    st, out = post(BASE + "/api/v1/index/upsert/file/content/" + tok,
                   b"# Decision Memo\n\nplaceholder body for the token test.\n",
                   "application/octet-stream")
    print("content(fresh token) ->", st, out[:300])
