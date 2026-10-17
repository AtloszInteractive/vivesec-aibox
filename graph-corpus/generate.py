"""Mixed synthetic corpus + answer key for relationship analysis (E26).

    python graph-corpus/generate.py --out graph-corpus/out --persons 120 --clean

Output:
    out/drives/<drive>/...     the documents (pdf, scanned pdf, docx, txt, csv, xlsx)
    out/manifest.jsonl         one record per document (same shape as demo-corpus)
    out/answer_key.json        true entities, mentions, relations with evidence and
                               gold questions (path, co-presence, shared identifiers,
                               timeline, communities, entity resolution)

Everything is fictional and derived from world.py; the answer key lists only
relations that at least one document actually states.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import itertools
import json
import mimetypes
import os
import random
import re
import shutil
import sys
import textwrap
from collections import Counter, deque

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
# Appended, not prepended: demo-corpus has its own generate.py.
sys.path.append(os.path.join(os.path.dirname(HERE), "demo-corpus"))

import world as world_mod  # noqa: E402
from lib import writers  # noqa: E402
from render import FOOTER, Doc, Renderer  # noqa: E402

DRIVE_PREFIX = "/storage/drives"
CREATOR = "Szintetikus tesztadat"
FONT_CANDIDATES = [
    r"C:\Windows\Fonts\arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
]


# identity — must match adapter/corpus.py and rag_service doc_id derivation
def corpus_id_of_drive_root(drive_root: str) -> str:
    root = drive_root.rstrip("/") or drive_root
    name = root.rsplit("/", 1)[-1]
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "drive"
    return "%s-%s" % (slug, hashlib.sha1(root.encode("utf-8")).hexdigest()[:8])


def make_doc_id(tenant_id: str, corpus_id: str, source_path: str) -> str:
    payload = "\0".join([tenant_id, corpus_id, source_path]).encode("utf-8")
    return hashlib.sha1(payload).hexdigest()[:12]


def scan_pages(doc: Doc, seed: int) -> list:
    """Render the document as noisy, slightly rotated grayscale page images."""
    from PIL import Image, ImageDraw, ImageFilter, ImageFont

    font_path = next((p for p in FONT_CANDIDATES if os.path.exists(p)), None)
    if not font_path:
        raise SystemExit("no TrueType font found for scanned pages")
    rng = random.Random(seed)
    width, height, top, leading = 1240, 1754, 130, 30
    body_font = ImageFont.truetype(font_path, 23)
    head_font = ImageFont.truetype(font_path, 30)
    lines: list[tuple[str, object]] = [(doc.title, head_font), ("", body_font)]
    for kind, text in doc.blocks:
        font = head_font if kind in ("h1", "h2") else body_font
        for line in textwrap.wrap(text, 78) or [""]:
            lines.append((line, font))
    per_page = (height - top - 140) // leading
    chunks = [lines[i:i + per_page] for i in range(0, len(lines), per_page)] or [[]]
    pages = []
    for no, chunk in enumerate(chunks, start=1):
        img = Image.new("L", (width, height), 246)
        draw = ImageDraw.Draw(img)
        y = top
        for text, font in chunk:
            draw.text((110, y), text, fill=rng.randint(18, 45), font=font)
            y += leading
        draw.text((110, height - 90), "%s | %d/%d" % (FOOTER, no, len(chunks)), fill=60,
                  font=body_font)
        for _ in range(1800):
            img.putpixel((rng.randrange(width), rng.randrange(height)), rng.randint(90, 200))
        img = img.rotate(rng.uniform(-0.8, 0.8), fillcolor=246, resample=Image.BICUBIC)
        pages.append(img.filter(ImageFilter.GaussianBlur(0.5)))
    return pages


def write_scanned_text_pdf(path: str, doc: Doc, seed: int) -> int:
    """Image-only PDF whose pages carry the real text as pixels (needs OCR)."""
    pages = scan_pages(doc, seed)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    pages[0].save(path, "PDF", resolution=150, save_all=True, append_images=pages[1:])
    return os.path.getsize(path)


def write_doc(doc: Doc, abs_path: str, seed: int) -> int:
    if doc.fmt in ("txt", "csv"):
        return writers.write_text(abs_path, doc.body or "")
    if doc.fmt == "xlsx":
        return writers.write_xlsx(abs_path, doc.sheets)
    if doc.fmt == "docx":
        return writers.write_docx(abs_path, doc.title, doc.blocks, creator=CREATOR)
    if doc.fmt == "pdf":
        return writers.write_pdf(abs_path, doc.title, doc.blocks, footer=FOOTER)
    if doc.fmt == "pdf_scanned":
        return write_scanned_text_pdf(abs_path, doc, seed)
    raise ValueError(doc.fmt)


# --------------------------------------------------------------------------
# answer key
# --------------------------------------------------------------------------

def person_edges(w: world_mod.World) -> dict[tuple[str, str], dict]:
    """Undirected person-person connections the documents support, with evidence."""
    edges: dict[tuple[str, str], dict] = {}

    def add(a: str, b: str, kind: str, evidence) -> None:
        if a == b or not evidence:
            return
        key = tuple(sorted((a, b)))
        e = edges.setdefault(key, {"types": set(), "evidence": set()})
        e["types"].add(kind)
        e["evidence"].update(evidence)

    rel = [r for r in w.relations if r.evidence]
    for r in rel:
        if r.type in ("RELATIVE_OF", "VISITED", "MET"):
            add(r.a, r.b, r.type, r.evidence)
    by_obj: dict[tuple[str, str], list] = {}
    for r in rel:
        if r.type in ("USES_PHONE", "USES_VEHICLE", "RESIDES_AT", "WORKS_FOR", "INVOLVED_IN"):
            by_obj.setdefault((r.type, r.b), []).append(r)
    names = {"USES_PHONE": "SHARED_PHONE", "USES_VEHICLE": "SHARED_VEHICLE",
             "RESIDES_AT": "SHARED_ADDRESS", "WORKS_FOR": "CO_WORKER", "INVOLVED_IN": "CO_INVOLVED"}
    for (type_, _), group in by_obj.items():
        for x, y in itertools.combinations(group, 2):
            add(x.a, y.a, names[type_], x.evidence + y.evidence)
    phone_users: dict[str, list] = {}
    for r in rel:
        if r.type == "USES_PHONE" and not r.attrs.get("seized"):
            phone_users.setdefault(r.b, []).append(r)
    for r in rel:
        if r.type == "CALLED":
            for owner in phone_users.get(r.b, []):
                add(r.a, owner.a, "CALLED_OWNER", r.evidence + owner.evidence)
    placed = [r for r in rel if r.type == "PLACED_IN"]
    by_cell: dict[str, list] = {}
    for r in placed:
        by_cell.setdefault(r.b, []).append(r)
    for group in by_cell.values():
        for x, y in itertools.combinations(group, 2):
            if world_mod.placements_overlap(x, y):
                add(x.a, y.a, "CO_PLACED", x.evidence + y.evidence)
    return edges


def build_answer_key(w: world_mod.World, docs: list[Doc], seed: int) -> dict:
    rng = random.Random(seed + 99)
    edges = person_edges(w)
    adj: dict[str, set[str]] = {}
    for a, b in edges:
        adj.setdefault(a, set()).add(b)
        adj.setdefault(b, set()).add(a)

    def bfs(src: str) -> dict[str, str | None]:
        prev = {src: None}
        q = deque([src])
        while q:
            cur = q.popleft()
            for nxt in sorted(adj.get(cur, ())):
                if nxt not in prev:
                    prev[nxt] = cur
                    q.append(nxt)
        return prev

    paths = []
    ids = sorted(adj)
    for _ in range(400):
        if len(paths) >= 12:
            break
        a, b = rng.sample(ids, 2)
        if w.persons[a].group == w.persons[b].group:
            continue
        prev = bfs(a)
        if b not in prev:
            continue
        path = [b]
        while prev[path[-1]] is not None:
            path.append(prev[path[-1]])
        path.reverse()
        if 3 <= len(path) <= 6 and not any(p["from"] == a and p["to"] == b for p in paths):
            paths.append({"from": a, "to": b, "distance": len(path) - 1, "example_path": path,
                          "example_edges": [sorted(edges[tuple(sorted(x))]["types"])
                                            for x in zip(path, path[1:])]})

    copresence = []
    placed = [r for r in w.relations if r.type == "PLACED_IN" and r.evidence]
    for r in placed:
        mates = []
        for o in placed:
            if o.a != r.a and (span := world_mod.placements_overlap(r, o)):
                mates.append({"person": o.a, "cell": r.b, "from": span[0].isoformat(),
                              "to": span[1].isoformat()})
        if mates:
            copresence.append({"person": r.a, "cell": r.b, "cell_label": w.places[r.b].label,
                               "cellmates": mates})
    copresence = copresence[:15]

    shared = []
    users: dict[str, set[str]] = {}
    for r in w.relations:
        if r.type in ("USES_PHONE", "USES_VEHICLE", "RESIDES_AT") and r.evidence:
            users.setdefault(r.b, set()).add(r.a)
    for value, who in sorted(users.items()):
        if len(who) > 1:
            shared.append({"value": value, "kind": w.identifiers.get(value, "address"),
                           "persons": sorted(who)})

    degree = Counter()
    for a, b in edges:
        degree[a] += 1
        degree[b] += 1
    timelines = []
    for pid, _ in degree.most_common(6):
        events = []
        for r in w.relations:
            if r.evidence and pid in (r.a, r.b) and r.start:
                events.append({"date": r.start.isoformat(), "type": r.type,
                               "other": r.b if r.a == pid else r.a, "evidence": sorted(r.evidence)})
        timelines.append({"person": pid, "events": sorted(events, key=lambda e: e["date"])})

    communities = []
    for g, members in sorted(w.groups.items()):
        inside = {m for m in members}
        deg = Counter()
        for a, b in edges:
            if a in inside and b in inside:
                deg[a] += 1
                deg[b] += 1
        communities.append({"group": g, "members": sorted(inside),
                            "central": deg.most_common(1)[0][0] if deg else None})

    surfaces: dict[str, dict[str, set[str]]] = {}
    for d in docs:
        for pid, s in d.mentions:
            surfaces.setdefault(pid, {}).setdefault(s, set()).add(d.key)

    return {
        "seed": w.seed,
        "persons": [{
            "id": p.id, "name": p.name, "dob": p.dob.isoformat(), "birthplace": p.birthplace,
            "mother": p.mother, "id_number": p.id_number, "address": p.address, "role": p.role,
            "group": p.group, "phones": p.phones, "plates": p.plates, "homonym_of": p.homonym_of,
            "surfaces": {s: sorted(k) for s, k in sorted(surfaces.get(p.id, {}).items())},
        } for p in w.persons.values()],
        "organisations": [{"id": o.id, "name": o.name, "address": o.address} for o in w.orgs.values()],
        "places": [{"id": p.id, "kind": p.kind, "label": p.label, "parent": p.parent}
                   for p in w.places.values()],
        "incidents": [{"id": i.id, "date": i.date.isoformat(), "institution": i.institution,
                       "cell": i.cell, "persons": i.persons, "kind": i.kind, "phone": i.phone}
                      for i in w.incidents],
        "relations": [{
            "id": r.id, "type": r.type, "a": r.a, "b": r.b,
            "start": r.start.isoformat() if r.start else None,
            "end": r.end.isoformat() if r.end else None, "attrs": r.attrs,
            "evidence": sorted(r.evidence),
        } for r in w.relations if r.evidence],
        "person_edges": [{"a": a, "b": b, "types": sorted(e["types"]),
                          "evidence": sorted(e["evidence"])} for (a, b), e in sorted(edges.items())],
        "questions": {
            "path": paths,
            "copresence": copresence,
            "shared_identifiers": shared,
            "timeline": timelines,
            "communities": communities,
            "must_not_merge": [[p.id, p.homonym_of] for p in w.persons.values() if p.homonym_of],
        },
    }


# --------------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=os.path.join(HERE, "out"))
    ap.add_argument("--persons", type=int, default=120)
    ap.add_argument("--seed", type=int, default=26)
    ap.add_argument("--tenant", default="default")
    ap.add_argument("--clean", action="store_true")
    args = ap.parse_args()

    out_root = os.path.abspath(args.out)
    if args.clean and os.path.isdir(out_root):
        shutil.rmtree(out_root)

    w = world_mod.build(args.persons, args.seed)
    docs = Renderer(w, args.seed).render_all()

    problems = []
    for d in docs:
        text = d.text()
        for pid, s in d.mentions:
            if s not in text:
                problems.append("%s: mention %r of %s not in text" % (d.key, s, pid))
    if problems:
        raise SystemExit("integrity check failed:\n" + "\n".join(problems[:20]))

    manifest, per_fmt, per_kind, per_drive, total = [], Counter(), Counter(), Counter(), 0
    for i, d in enumerate(docs):
        source_path = "%s/%s/%s" % (DRIVE_PREFIX, d.drive, d.rel)
        corpus_id = corpus_id_of_drive_root("%s/%s" % (DRIVE_PREFIX, d.drive))
        local_rel = "/".join(["drives", d.drive, d.rel])
        abs_path = os.path.join(out_root, *local_rel.split("/"))
        size = write_doc(d, abs_path, args.seed * 1000 + i)
        manifest.append({
            "doc_id": make_doc_id(args.tenant, corpus_id, source_path),
            "key": d.key, "tenant_id": args.tenant, "corpus_id": corpus_id, "drive": d.drive,
            "source_path": source_path, "local_path": local_rel, "title": d.title,
            "kind": d.kind, "file_type": d.fmt.replace("pdf_scanned", "pdf"),
            "content_type": mimetypes.guess_type(abs_path)[0] or "application/octet-stream",
            "size": size, "language": "hu",
            "metadata": {"scanned": d.fmt == "pdf_scanned", "noise_list": d.noise_list,
                         "mentions": len(d.mentions), "evidence": len(d.evidence)},
        })
        per_fmt[d.fmt] += 1
        per_kind[d.kind] += 1
        per_drive[d.drive] += 1
        total += size

    key = build_answer_key(w, docs, args.seed)
    key["documents"] = [{"key": m["key"], "doc_id": m["doc_id"], "source_path": m["source_path"],
                         "kind": m["kind"], "scanned": m["metadata"]["scanned"],
                         "noise_list": m["metadata"]["noise_list"]} for m in manifest]
    unevidenced = Counter(r.type for r in w.relations if not r.evidence)

    with io.open(os.path.join(out_root, "manifest.jsonl"), "w", encoding="utf-8", newline="\n") as f:
        for row in manifest:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    with io.open(os.path.join(out_root, "answer_key.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(key, f, ensure_ascii=False, indent=1)

    q = key["questions"]
    print("documents: %d (%.1f MB) -> %s" % (len(docs), total / 1_048_576, out_root))
    print("  drives:  %s" % dict(per_drive))
    print("  formats: %s" % dict(per_fmt))
    print("  kinds:   %s" % dict(per_kind))
    print("persons: %d (name twins: %d), relations with evidence: %d, person edges: %d"
          % (len(w.persons), len(q["must_not_merge"]), len(key["relations"]),
             len(key["person_edges"])))
    print("questions: path %d, copresence %d, shared identifiers %d, timeline %d, communities %d"
          % (len(q["path"]), len(q["copresence"]), len(q["shared_identifiers"]),
             len(q["timeline"]), len(q["communities"])))
    if unevidenced:
        print("relations without evidence (excluded from the key): %s" % dict(unevidenced))
    return 0


if __name__ == "__main__":
    sys.exit(main())
