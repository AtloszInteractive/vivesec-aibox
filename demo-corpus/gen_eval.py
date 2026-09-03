"""Score ANSWER GENERATION against the computed fleet answer key.

Runs ON the Jetson. For every case in `fleet_answer_key.json` it asks the
question through the adapter exactly the way the UI does (VVS-Drive /
VVS-User), then checks whether the generated answer actually contains the
value computed from the fleet model.

This is the part a retrieval metric cannot tell you: whether the model reads
the right number out of the right document, or quietly invents one.

Usage:
    python3 gen_eval.py [--drive engineering] [--out gen_eval_report.json]
"""
from __future__ import print_function

import argparse
import base64
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

ADAPTER = os.environ.get("ADAPTER_URL", "http://127.0.0.1:8088")
USER = os.environ.get("VVS_USER", "lars.nygaard")

_WS = re.compile(r"\s+")
_NUM = re.compile(r"-?\d+(?:[.,]\d+)?")


def norm(text):
    t = (text or "").lower()
    t = t.replace("percent", "%").replace(" %", "%")
    t = t.replace("hours", "h").replace("hour", "h")
    t = t.replace("mwh", "mwh")
    t = _WS.sub(" ", t)
    return t


def numbers(text):
    """Numeric tokens, normalised so 6 == 6.0 and 99,79 == 99.79."""
    out = set()
    for m in _NUM.finditer(text or ""):
        v = m.group(0).replace(",", ".")
        try:
            f = float(v)
        except ValueError:
            continue
        out.add(("%f" % f).rstrip("0").rstrip("."))
    return out


def matches(expected, answer):
    """Is the expected value present in the generated answer?"""
    exp_n, ans_n = norm(expected), norm(answer)
    if exp_n and exp_n in ans_n:
        return True
    exp_nums = numbers(expected)
    if exp_nums and exp_nums <= numbers(answer):
        # a bare figure counts only if the unit or symbol is there too
        unit = None
        for u in ("%", "mwh", "h"):
            if u in exp_n:
                unit = u
                break
        return unit is None or unit in ans_n
    return False


def drive_header(name):
    path = ("/storage/drives/%s/" % name).encode("utf-8")
    return base64.urlsafe_b64encode(path).decode("ascii").rstrip("=")


def ask(drive, question, top_k, timeout=420):
    payload = json.dumps({"query": question, "top_k": top_k}).encode("utf-8")
    req = urllib.request.Request(
        ADAPTER + "/api/v1/ui/query", data=payload, method="POST",
        headers={"Content-Type": "application/json",
                 "VVS-Drive": drive_header(drive), "VVS-User": USER})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8")), None
    except urllib.error.HTTPError as exc:
        return None, "HTTP %s %s" % (exc.code, exc.read().decode("utf-8", "replace")[:300])
    except Exception as exc:  # noqa: BLE001
        return None, str(exc)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    here = os.path.dirname(os.path.abspath(__file__))
    ap.add_argument("--key", default=os.path.join(here, "out", "fleet_answer_key.json"))
    ap.add_argument("--drive", default="engineering")
    ap.add_argument("--top-k", type=int, default=8)
    ap.add_argument("--out", default=os.path.join(here, "gen_eval_report.json"))
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()

    with open(args.key) as f:
        key = json.load(f)
    cases = key["cases"][:args.limit] if args.limit else key["cases"]

    print("corpus totals: %s" % key["totals"])
    print("cases: %d   drive: %s   top_k: %d" % (len(cases), args.drive, args.top_k))
    print("=" * 100)

    results, t0 = [], time.time()
    for i, c in enumerate(cases, 1):
        expected = c["answer"]
        accept = c.get("accept") or [expected]
        res, err = ask(args.drive, c["question"], args.top_k)
        if err:
            results.append({"question": c["question"], "kind": c["kind"], "expected": expected,
                            "ok": False, "answer": "", "error": err, "hits": 0})
            print("[ERR ] %-70s %s" % (c["question"][:70], err[:60]))
            continue
        answer = (res.get("answer") or "").strip()
        # the audit footer repeats source names; score only the answer body
        body = answer.split("\n---\n")[0]
        ok = any(matches(a, body) for a in accept)
        conf = (res.get("confidence") or {})
        results.append({"question": c["question"], "kind": c["kind"], "expected": expected,
                        "accept": accept, "aggregation": c.get("aggregation"),
                        "ok": ok, "answer": body,
                        "confidence": conf.get("score"), "band": conf.get("band"),
                        "hits": len(res.get("hits") or [])})
        print("[%s] %-70s" % ("PASS" if ok else "FAIL", c["question"][:70]))
        print("       expected: %-28s conf=%s%% (%s) hits=%d"
              % (" | ".join(accept), conf.get("score"), conf.get("band"),
                 len(res.get("hits") or [])))
        first = body.splitlines()[0] if body.splitlines() else ""
        print("       got     : %s" % first[:150])
        if i % 5 == 0:
            print("       ... %d/%d, %.0f s elapsed" % (i, len(cases), time.time() - t0))

    passed = sum(1 for r in results if r["ok"])
    by_kind = {}
    for r in results:
        k = r["kind"]
        by_kind.setdefault(k, [0, 0])
        by_kind[k][1] += 1
        by_kind[k][0] += 1 if r["ok"] else 0

    print("=" * 100)
    print("GENERATION ACCURACY: %d/%d = %.1f%%   (%.0f s)"
          % (passed, len(results), 100.0 * passed / max(1, len(results)), time.time() - t0))
    for k, (ok, n) in sorted(by_kind.items()):
        print("  %-8s %d/%d" % (k, ok, n))
    graded = [r for r in results if r.get("confidence") is not None]
    if graded:
        good = [r["confidence"] for r in graded if r["ok"]]
        bad = [r["confidence"] for r in graded if not r["ok"]]
        if good:
            print("  mean confidence when correct  : %.1f%%" % (sum(good) / len(good)))
        if bad:
            print("  mean confidence when incorrect: %.1f%%" % (sum(bad) / len(bad)))
    if passed < len(results):
        print("\nFAILED CASES:")
        for r in results:
            if not r["ok"]:
                print("  - %s" % r["question"])
                print("      expected %r" % r["expected"])
                print("      got      %s" % (r["answer"] or r.get("error", ""))[:200])

    with open(args.out, "w") as f:
        json.dump({"totals": key["totals"], "passed": passed, "count": len(results),
                   "results": results}, f, indent=2, ensure_ascii=False)
    print("\nreport: %s" % args.out)
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
