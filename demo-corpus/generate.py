"""Generate the ViVeSec demo corpus from company_bible.yaml.

Everything written here is derived from the bible, which is the single source of
truth. Re-running the generator is idempotent: the same bible produces the same
files (fixed random seeds), so the corpus can be regenerated after a fact change.

Output layout mirrors the ViVeSecBox drive convention:

    out/drives/<drive>/<relative path>      the files themselves
    out/manifest.jsonl                      one record per document
    out/summary.md                          human-readable generation report

Usage:
    python generate.py [--out out] [--tenant gaphopper] [--clean]
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import mimetypes
import os
import re
import shutil
import sys
from collections import Counter

import yaml

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lib import build_engineering, build_finance, build_fleet, build_hr, build_legal, build_public  # noqa: E402
from lib import probes  # noqa: E402
from lib.common import Bible, Doc  # noqa: E402
from lib import writers  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
DRIVE_PREFIX = "/storage/drives"

EXT = {"md": ".md", "txt": ".txt", "csv": ".csv", "xlsx": ".xlsx",
       "docx": ".docx", "pdf": ".pdf", "pdf_scanned": ".pdf"}


# ---------------------------------------------------------------------------
# identity — must match adapter/corpus.py and rag_service doc_id derivation
# ---------------------------------------------------------------------------

def slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return s or "drive"


def corpus_id_of_drive_root(drive_root: str) -> str:
    root = drive_root.rstrip("/") or drive_root
    name = root.rsplit("/", 1)[-1]
    digest = hashlib.sha1(root.encode("utf-8")).hexdigest()[:8]
    return "%s-%s" % (slug(name), digest)


def make_doc_id(tenant_id: str, corpus_id: str, source_path: str) -> str:
    payload = "\0".join([tenant_id, corpus_id, source_path]).encode("utf-8")
    return hashlib.sha1(payload).hexdigest()[:12]


# ---------------------------------------------------------------------------


def load_bible(path: str) -> Bible:
    with io.open(path, encoding="utf-8") as f:
        return Bible(yaml.safe_load(f))


def render(doc: Doc, abs_path: str) -> int:
    if doc.fmt in ("md", "txt", "csv"):
        return writers.write_text(abs_path, doc.body or "")
    if doc.fmt == "xlsx":
        return writers.write_xlsx(abs_path, doc.sheets)
    if doc.fmt == "docx":
        return writers.write_docx(abs_path, doc.title, doc.blocks)
    if doc.fmt == "pdf":
        return writers.write_pdf(abs_path, doc.title, doc.blocks, footer=doc.title)
    if doc.fmt == "pdf_scanned":
        return writers.write_scanned_pdf(abs_path, pages=doc.scanned_pages)
    raise ValueError("unknown format: %s" % doc.fmt)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bible", default=os.path.join(HERE, "company_bible.yaml"))
    ap.add_argument("--out", default=os.path.join(HERE, "out"))
    ap.add_argument("--tenant", default="default",
                    help="must match ADAPTER_TENANT_ID on the box (the AI Box pins one "
                         "tenant per physical appliance; production runs 'default')")
    ap.add_argument("--clean", action="store_true", help="remove the output directory first")
    args = ap.parse_args()

    b = load_bible(args.bible)
    out_root = os.path.abspath(args.out)
    if args.clean and os.path.isdir(out_root):
        shutil.rmtree(out_root)

    docs: list[Doc] = []
    for module in (build_public, build_engineering, build_finance, build_hr, build_legal,
                   build_fleet):
        docs.extend(module.build(b))

    # sanity: every drive referenced must exist, every path unique
    seen: set[str] = set()
    for doc in docs:
        if doc.drive not in b.drives:
            raise SystemExit("unknown drive %r on %s" % (doc.drive, doc.rel))
        key = doc.drive + "/" + doc.rel
        if key in seen:
            raise SystemExit("duplicate document path: %s" % key)
        seen.add(key)

    manifest: list[dict] = []
    per_drive: Counter = Counter()
    per_fmt: Counter = Counter()
    per_lang: Counter = Counter()
    per_tag: Counter = Counter()
    total_bytes = 0

    for doc in docs:
        drive = b.drives[doc.drive]
        rel = doc.rel
        expected_ext = EXT[doc.fmt]
        if not rel.endswith(expected_ext):
            raise SystemExit("extension mismatch: %s expected %s" % (rel, expected_ext))

        source_path = "%s/%s/%s" % (DRIVE_PREFIX, drive["name"], rel)
        corpus_id = corpus_id_of_drive_root("%s/%s" % (DRIVE_PREFIX, drive["name"]))
        local_rel = os.path.join("drives", drive["name"], *rel.split("/"))
        abs_path = os.path.join(out_root, local_rel)

        size = render(doc, abs_path)
        mtime = int(os.path.getmtime(abs_path) * 1_000_000_000)
        content_type = mimetypes.guess_type(abs_path)[0] or "application/octet-stream"

        manifest.append({
            "doc_id": make_doc_id(args.tenant, corpus_id, source_path),
            "tenant_id": args.tenant,
            "corpus_id": corpus_id,
            "drive": drive["name"],
            "acl": [drive["acl"]],
            "source_path": source_path,
            "local_path": local_rel.replace(os.sep, "/"),
            "title": doc.title,
            "file_type": doc.fmt.replace("pdf_scanned", "pdf"),
            "content_type": content_type,
            "size": size,
            "mtime": mtime,
            "language": doc.language,
            "metadata": {
                "drive_display_name": drive["name"],
                "tags": doc.tags,
                "expect_empty_extraction": doc.fmt == "pdf_scanned",
            },
        })
        per_drive[drive["name"]] += 1
        per_fmt[doc.fmt] += 1
        per_lang[doc.language] += 1
        for t in doc.tags:
            per_tag[t] += 1
        total_bytes += size

    manifest_path = os.path.join(out_root, "manifest.jsonl")
    with io.open(manifest_path, "w", encoding="utf-8", newline="\n") as f:
        for row in manifest:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    # calibration probes: in-corpus questions + the bible's negative cases
    probe = probes.build(b)
    drive_to_corpus = {d["name"]: corpus_id_of_drive_root("%s/%s" % (DRIVE_PREFIX, d["name"]))
                       for d in b.drives.values()}
    for item in probe["in_corpus"]:
        item["expect_corpus"] = drive_to_corpus[item["drive"]]
    probe["corpora"] = drive_to_corpus
    probe["tenant_id"] = args.tenant
    with io.open(os.path.join(out_root, "probe_questions.json"), "w",
                 encoding="utf-8", newline="\n") as f:
        json.dump(probe, f, ensure_ascii=False, indent=2)

    # answer key for the fleet data: questions with a computed correct answer
    key = build_fleet.answer_key(b)
    key["corpus"] = drive_to_corpus["engineering"]
    key["tenant_id"] = args.tenant
    with io.open(os.path.join(out_root, "fleet_answer_key.json"), "w",
                 encoding="utf-8", newline="\n") as f:
        json.dump(key, f, ensure_ascii=False, indent=2)

    # ---------------------------------------------------------- summary
    commands = sorted(t for t in per_tag if t.startswith("/"))
    planned = set(b.d["document_plan"].keys())
    missing = sorted(planned - set(commands))

    lines = ["# Demo corpus generation report", "",
             f"- Bible: `{os.path.relpath(args.bible, HERE)}`",
             f"- Tenant: `{args.tenant}`",
             f"- Documents: **{len(manifest)}**",
             f"- Total size: {total_bytes / 1_048_576:.2f} MB", "",
             "## Documents per drive", ""]
    for name, count in sorted(per_drive.items()):
        cid = corpus_id_of_drive_root(f"{DRIVE_PREFIX}/{name}")
        lines.append(f"- `{name}` — {count} documents, corpus_id `{cid}`")
    lines += ["", "## Formats", ""]
    for fmt, count in sorted(per_fmt.items()):
        lines.append(f"- `{fmt}` — {count}")
    lines += ["", "## Languages", ""]
    for lang, count in sorted(per_lang.items()):
        lines.append(f"- `{lang}` — {count}")
    lines += ["", "## Slash-command coverage", ""]
    for cmd in sorted(planned):
        lines.append(f"- `{cmd}` — {per_tag.get(cmd, 0)} documents")
    if missing:
        lines += ["", f"**WARNING — commands with no source document: {', '.join(missing)}**"]
    lines += ["", "## Difficulty injections", ""]
    for tag, count in sorted(t for t in per_tag.items() if t[0].startswith("difficulty:")):
        lines.append(f"- `{tag}` — {count}")
    lines += ["", "## Largest documents", ""]
    for row in sorted(manifest, key=lambda r: -r["size"])[:8]:
        lines.append(f"- {row['size'] / 1024:.0f} kB — `{row['source_path']}`")
    lines.append("")
    writers.write_text(os.path.join(out_root, "summary.md"), "\n".join(lines))

    print("generated %d documents (%.2f MB) in %s"
          % (len(manifest), total_bytes / 1_048_576, out_root))
    print("  probes: %d in-corpus, %d out-of-corpus questions"
          % (len(probe["in_corpus"]), len(probe["out_of_corpus"])))
    print("  fleet answer key: %d cases over %d sites / %d service events"
          % (len(key["cases"]), key["totals"]["sites"], key["totals"]["events"]))
    for name, count in sorted(per_drive.items()):
        print("  %-14s %3d docs   corpus_id=%s"
              % (name, count, corpus_id_of_drive_root(f"{DRIVE_PREFIX}/{name}")))
    if missing:
        print("WARNING: no document tagged for: %s" % ", ".join(missing))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
