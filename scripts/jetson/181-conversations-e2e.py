#!/usr/bin/env python3
"""Live F1 verification against the adapter on this box (read/write, but only
under its own throwaway test identities -- it never touches a real user's
conversations).

Checks: create/list/get/rename/delete, a real grounded exchange recorded into a
named thread, the model-made title, ACL isolation (other user / other drive /
other profile see nothing), and that a request WITHOUT conversation_id still
behaves like the pre-thread default session.
"""
import base64
import json
import sys
import time
import urllib.error
import urllib.request

ADAPTER = "http://127.0.0.1:8088"
DRIVE = "/storage/drives/engineering/"
OTHER_DRIVE = "/storage/drives/finance/"
USER = "f1-verify-primary"
OTHER_USER = "f1-verify-other"

PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  ok   %s" % name)
    else:
        FAIL += 1
        print("  FAIL %s -> %s" % (name, detail))


def call(path, body, user=USER, drive=DRIVE, timeout=420):
    data = json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        ADAPTER + path, data=data, method="POST",
        headers={"Content-Type": "application/json", "VVS-User": user,
                 "VVS-Drive": base64.urlsafe_b64encode(drive.encode()).decode().rstrip("=")})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read().decode("utf-8") or "{}")


def main():
    print("=== create / list ===")
    # No title on purpose: a user-given title is never replaced by the model's,
    # and the automatic titling is what this run has to prove.
    code, created = call("/api/v1/ui/conversations/create", {})
    check("create 200", code == 200, (code, created))
    cid = created.get("conversation", {}).get("id", "")
    check("thread has an id", bool(cid), created)
    code, listed = call("/api/v1/ui/conversations/list", {})
    check("listed for the owner", any(c["id"] == cid for c in listed.get("conversations", [])), listed)

    print("=== a real exchange lands in the thread ===")
    started = time.time()
    code, answer = call("/api/v1/ui/query",
                        {"query": "Melyik telephelyen volt a legtobb beavatkozas 2026 elso feleveben?",
                         "conversation_id": cid})
    check("query 200", code == 200, (code, str(answer)[:200]))
    check("echoes the thread", answer.get("conversation_id") == cid, answer.get("conversation_id"))
    check("real answer text", len((answer.get("answer") or "")) > 40, (answer.get("answer") or "")[:120])
    print("       %.1fs, backend=%s" % (time.time() - started, answer.get("backend")))
    print("       %s" % " ".join((answer.get("answer") or "").split())[:180])

    code, got = call("/api/v1/ui/conversations/get", {"conversation_id": cid})
    turns = got.get("conversation", {}).get("turns", [])
    check("one exchange stored", len(turns) == 2, len(turns))
    check("history is footer-free", "Audit ID" not in (turns[1]["content"] if len(turns) > 1 else "x"))

    print("=== title ===")
    title = ""
    for _ in range(40):
        code, got = call("/api/v1/ui/conversations/get", {"conversation_id": cid})
        conversation = got.get("conversation", {})
        if conversation.get("title_source") == "auto":
            title = conversation.get("title", "")
            break
        time.sleep(2)
    check("model-made title arrived", bool(title), "still %r" % conversation.get("title_source"))
    print("       title=%r" % (title or conversation.get("title")))

    print("=== follow-up uses the thread history ===")
    code, follow = call("/api/v1/ui/query", {"query": "Es a masodik helyen?", "conversation_id": cid})
    check("follow-up 200", code == 200, code)
    code, got = call("/api/v1/ui/conversations/get", {"conversation_id": cid})
    check("two exchanges stored", len(got.get("conversation", {}).get("turns", [])) == 4,
          len(got.get("conversation", {}).get("turns", [])))

    print("=== isolation ===")
    check("other user: list empty",
          call("/api/v1/ui/conversations/list", {}, user=OTHER_USER)[1].get("conversations") == [],
          "leaked")
    check("other user: get 404",
          call("/api/v1/ui/conversations/get", {"conversation_id": cid}, user=OTHER_USER)[0] == 404)
    check("other drive: get 404",
          call("/api/v1/ui/conversations/get", {"conversation_id": cid}, drive=OTHER_DRIVE)[0] == 404)
    # Under a locked chat policy the foreign profile is refused before the
    # thread lookup (403); under a selectable one the thread simply does not
    # exist in that namespace (404). Both mean "not reachable from there".
    other_profile = call("/api/v1/ui/conversations/get", {"conversation_id": cid, "profile": "grounded"})[0]
    check("other profile: not reachable", other_profile in (403, 404), other_profile)
    check("other user cannot post into it",
          call("/api/v1/ui/query", {"query": "Hi", "conversation_id": cid}, user=OTHER_USER)[0] == 404)
    check("unknown id 404",
          call("/api/v1/ui/query", {"query": "Hi", "conversation_id": "0123456789abcdef"})[0] == 404)

    print("=== the default thread still behaves like before ===")
    code, plain = call("/api/v1/ui/query", {"query": "Mi a flotta rendelkezesre allasa 2026 aprilisban?"})
    check("no conversation_id -> 200", code == 200, code)
    check("no conversation_id echoed", "conversation_id" not in plain, plain.get("conversation_id"))
    code, listed = call("/api/v1/ui/conversations/list", {})
    ids = [c["id"] for c in listed.get("conversations", [])]
    check("default thread now listed", "default" in ids, ids)

    print("=== rename / delete ===")
    check("rename 200", call("/api/v1/ui/conversations/rename",
                             {"conversation_id": cid, "title": "Renamed by verify"})[0] == 200)
    code, got = call("/api/v1/ui/conversations/get", {"conversation_id": cid})
    check("rename stuck", got.get("conversation", {}).get("title") == "Renamed by verify",
          got.get("conversation", {}).get("title"))
    check("delete 200", call("/api/v1/ui/conversations/delete", {"conversation_id": cid})[0] == 200)
    check("gone afterwards",
          call("/api/v1/ui/conversations/get", {"conversation_id": cid})[0] == 404)
    check("delete the test default thread",
          call("/api/v1/ui/conversations/delete", {"conversation_id": "default"})[0] == 200)

    print()
    print("%d passed, %d failed" % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
