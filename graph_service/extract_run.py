"""Run the extraction pipeline over a manifest and store the payloads (E26).

    python graph_service/extract_run.py --corpus graph-corpus/out --profile bv \
        --limit 30 --out extracted.jsonl [--llm-url http://127.0.0.1:11434]

The manifest is the graph-corpus / demo-corpus format (doc_id, corpus_id,
source_path, local_path, title, kind). Finished documents are cached in the
output file, so an interrupted run resumes; review candidates (possible
same-person pairs) and the learned table mappings are written next to it.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import random
import sys
import time
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import domain_profile  # noqa: E402
from llm_client import LLMClient  # noqa: E402
from pipeline import extract_document  # noqa: E402
from resolution import Resolver  # noqa: E402
from table_mapping import TableMapper  # noqa: E402


def pick(rows: list[dict], limit: int, seed: int, kinds: set[str] | None) -> list[dict]:
    rows = [r for r in rows if not kinds or r.get("kind") in kinds]
    if not limit or limit >= len(rows):
        return rows
    by_kind: dict[str, list[dict]] = {}
    for r in rows:
        by_kind.setdefault(r.get("kind", ""), []).append(r)
    rng = random.Random(seed)
    out = []
    while len(out) < limit:
        for kind in sorted(by_kind):
            if by_kind[kind] and len(out) < limit:
                out.append(by_kind[kind].pop(rng.randrange(len(by_kind[kind]))))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--corpus", required=True)
    ap.add_argument("--profile", default="bv")
    ap.add_argument("--out", required=True)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--kinds", default="")
    ap.add_argument("--llm-url", default=None)
    ap.add_argument("--tables", choices=("on", "off"), default="on",
                    help="map table layouts once and read rows by rule (default on)")
    args = ap.parse_args()

    profile = domain_profile.load(args.profile)
    llm = LLMClient(url=args.llm_url)
    resolver = Resolver(profile)
    tables_path = os.path.splitext(args.out)[0] + ".tables.json"
    tables = None
    if args.tables == "on":
        pinned = {}
        if os.path.exists(tables_path):
            with io.open(tables_path, encoding="utf-8") as f:
                pinned = json.load(f)
        tables = TableMapper(profile, llm, pinned)
    with io.open(os.path.join(args.corpus, "manifest.jsonl"), encoding="utf-8") as f:
        rows = [json.loads(line) for line in f]
    todo = pick(rows, args.limit, args.seed, set(filter(None, args.kinds.split(","))))

    done = {}
    if os.path.exists(args.out):
        with io.open(args.out, encoding="utf-8") as f:
            for line in f:
                item = json.loads(line)
                done[item["payload"]["document"]["doc_id"]] = item
    # Rebuild the resolver from cached results so resumed runs resolve the same way.
    strong = resolver.strong_key_subtypes()
    for item in done.values():
        keys: dict[str, set[str]] = {}
        for r in item["payload"]["relations"]:
            if r["type"] in resolver.holding and r["b"].startswith("id:"):
                _, sub, value = r["b"].split(":", 2)
                if sub in strong:
                    keys.setdefault(r["a"], set()).add("%s:%s" % (sub, value))
        for e in item["payload"]["entities"]:
            if e["type"] == "person":
                names = {m["text"] for m in item["payload"]["mentions"] if m["entity_id"] == e["id"]}
                resolver.remember_person(e["id"], names, {a["name"]: a["value"]
                                                          for a in e.get("attributes", [])},
                                         keys.get(e["id"], ()))

    totals = Counter()
    started = time.time()
    with io.open(args.out, "a", encoding="utf-8", newline="\n") as out:
        for i, row in enumerate(todo, start=1):
            if row["doc_id"] in done:
                continue
            with open(os.path.join(args.corpus, *row["local_path"].split("/")), "rb") as f:
                raw = f.read()
            meta = {k: row[k] for k in ("doc_id", "corpus_id", "source_path") if k in row}
            meta.update({"title": row.get("title", ""), "kind": row.get("kind", "")})
            payload, stats = extract_document(raw, meta, profile, llm, resolver, tables)
            out.write(json.dumps({"key": row.get("key"), "stats": stats, "payload": payload},
                                 ensure_ascii=False) + "\n")
            out.flush()
            totals.update({k: v for k, v in stats.items() if isinstance(v, (int, float))})
            totals["text:" + stats["text"]] += 1
            print("[%d/%d] %-22s %-18s %4.0fs ents=%s rels=%s" % (
                i, len(todo), row.get("kind", ""), stats["text"], stats.get("seconds", 0),
                stats.get("entities", 0), stats.get("relations", 0)), flush=True)
    review_path = os.path.splitext(args.out)[0] + ".review.json"
    with io.open(review_path, "w", encoding="utf-8") as f:
        json.dump(resolver.review, f, ensure_ascii=False, indent=1)
    if tables is not None:
        with io.open(tables_path, "w", encoding="utf-8") as f:
            json.dump(tables.export(), f, ensure_ascii=False, indent=1)
    print("done in %.0fs; llm calls %d (%.0fs); %s; review pairs %d" % (
        time.time() - started, llm.calls, llm.seconds,
        {k: v for k, v in totals.items() if k.startswith("text:")}, len(resolver.review)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
