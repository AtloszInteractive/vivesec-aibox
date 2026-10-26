"""Document -> graph payload: text, rules, model, co-mentions, resolution (E26)."""
from __future__ import annotations

import hashlib
import os
import re
import time

from extraction import (LocalEntity, LocalRelation, chunk_pages, co_mentions, find_identifiers,
                        fold, llm_extract, squash)
from llm_client import LLMError
from table_mapping import apply_mapping, blank_tables, find_form, find_tables, form_spans
from text_source import document_pages

MIN_NAME_CHARS = 3
MIN_PROSE_CHARS = 20  # a chunk with less text than this is not worth a model call
# "Label: value" forms read by a mapping learnt once per layout (like tables).
# On the synthetic intake sheets: 37% faster and more precise than the model alone.
FORMS = os.environ.get("GRAPH_EXTRACT_FORMS", "on").strip().lower() != "off"
LLM_COUNTERS = ("calls", "seconds", "prompt_tokens", "output_tokens", "prompt_seconds",
                "eval_seconds", "load_seconds", "truncated")


def _llm_counters(llm) -> dict:
    return {k: getattr(llm, k, 0) for k in LLM_COUNTERS}


class _Union:
    def __init__(self):
        self.parent = {}

    def find(self, x):
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[max(ra, rb)] = min(ra, rb)


def _consolidate(chunk_results, id_hits) -> tuple[dict[str, LocalEntity], list[LocalRelation]]:
    """Merge chunk-local entities that share a spelling (same type) into one per document."""
    ents: dict[str, LocalEntity] = {}
    rels: list[LocalRelation] = []
    value_of = {fold(surface): (sub, norm) for sub, surface, norm, *_ in id_hits}
    for ci, (entities, relations) in enumerate(chunk_results):
        for e in entities:
            ref = "c%d.%s" % (ci, e.ref)
            e = LocalEntity(ref, e.type, e.subtype, list(e.names), dict(e.attrs), e.value, e.method)
            if e.type == "identifier":
                hit = next((value_of[fold(n)] for n in e.names if fold(n) in value_of), None)
                if hit:
                    e.subtype, e.value = hit
            ents[ref] = e
        for r in relations:
            rels.append(LocalRelation(r.type, "c%d.%s" % (ci, r.a), "c%d.%s" % (ci, r.b), r.quote,
                                      r.page, r.method, r.start, r.end, r.raw_label, r.confidence))
    for sub, surface, norm, *_ in id_hits:
        ref = "id.%s.%s" % (sub, norm)
        if ref not in ents:
            ents[ref] = LocalEntity(ref, "identifier", sub, [surface], value=norm)
        elif surface not in ents[ref].names:
            ents[ref].names.append(surface)

    uf = _Union()
    seen: dict[tuple, str] = {}
    for ref, e in ents.items():
        keys = [("id", e.subtype, e.value)] if e.type == "identifier" and e.value else \
            [(e.type, fold(n)) for n in e.names]
        for key in keys:
            if key in seen:
                uf.union(seen[key], ref)
            else:
                seen[key] = ref
    merged: dict[str, LocalEntity] = {}
    for ref, e in ents.items():
        root = uf.find(ref)
        m = merged.setdefault(root, LocalEntity(root, e.type, e.subtype, [], {}, e.value,
                                                e.method))
        m.subtype = m.subtype or e.subtype
        if e.method == "llm":
            m.method = "llm"
        m.names += [n for n in e.names if n not in m.names]
        for k, v in e.attrs.items():
            m.attrs.setdefault(k, v)
    out_rels = [LocalRelation(r.type, uf.find(r.a), uf.find(r.b), r.quote, r.page, r.method,
                              r.start, r.end, r.raw_label, r.confidence)
                for r in rels if uf.find(r.a) != uf.find(r.b)]
    return merged, out_rels


def _spans(pages, ents: dict[str, LocalEntity]) -> dict[str, list[tuple[int, int, int, str]]]:
    out = {}
    for ref, e in ents.items():
        for name in e.names:
            if len(name.strip()) < MIN_NAME_CHARS:
                continue
            rx = re.compile(re.escape(name), re.IGNORECASE)
            for page, text in pages:
                for m in rx.finditer(text):
                    out.setdefault(ref, []).append((page, m.start(), m.end(), m.group(0)))
    return out


# Places, organizations and objects named without a capital letter ("intézet",
# "gépjármű", "102. számú zárka"): a common noun, not a name, so each document
# keeps its own -- the same words in two documents are not the same thing. A
# cell the model typed as such and placed in an institution is the exception.
GENERIC_TYPES = ("place", "organization", "object")



def _generic(e: LocalEntity) -> bool:
    return e.type in GENERIC_TYPES and not e.value and bool(e.names) and all(
        not any(c.isupper() for c in name) for name in e.names)


# How far apart (characters, same page) a place and its container may be named
# for the text between them to serve as the evidence that one is inside the other.
CONTAINER_WINDOW = 300


def _containers(profile: dict, pages, ents: dict[str, LocalEntity], rels: list[LocalRelation],
                spans) -> dict[str, tuple[str | None, LocalRelation | None]]:
    """ref of a place that only exists inside a larger one (profile `within`, e.g. a
    cell) -> (ref of its container in this document or None, a PART_OF relation
    to add). The container is the one a PART_OF relation names, else the only
    one the document names, else the one named last before the place on its page."""
    within = {s["id"]: set(s["within"]) for s in profile["entity_subtypes"] if s.get("within")}
    text = dict(pages)
    out: dict[str, tuple[str | None, LocalRelation | None]] = {}
    for ref, e in ents.items():
        allowed = within.get(e.subtype)
        if not allowed:
            continue
        holders = [x for x, c in ents.items() if x != ref and c.subtype in allowed and x in spans]
        explicit = {r.b if r.a == ref else r.a for r in rels if r.type == "PART_OF"
                    and ref in (r.a, r.b) and (r.b if r.a == ref else r.a) in holders}
        container = None
        if len(explicit) == 1:
            container = explicit.pop()
        elif not explicit and len(holders) == 1:
            container = holders[0]
        elif not explicit and holders:
            for page, start, _, _ in sorted(spans.get(ref, ())):
                before = [(h_end, h) for h in holders for h_page, _, h_end, _ in spans[h]
                          if h_page == page and h_end <= start and start - h_end <= CONTAINER_WINDOW]
                if before:
                    container = max(before)[1]
                    break
        evidence = None
        if container is not None and not explicit and container in holders:
            near = sorted((max(s, hs) - min(e_, he), page, min(s, hs), max(e_, he))
                          for page, s, e_, _ in spans.get(ref, ())
                          for h_page, hs, he, _ in spans[container] if h_page == page)
            if near and near[0][0] <= CONTAINER_WINDOW:
                _, page, start, end = near[0]
                evidence = LocalRelation("PART_OF", ref, container,
                                         squash(text[page][start:end]), page, "rule",
                                         confidence=1.0)
        out[ref] = (container, evidence)
    return out


def extract_document(raw: bytes, meta: dict, profile: dict, llm, resolver, tables=None
                     ) -> tuple[dict, dict]:
    """(ingest payload, stats). meta: doc_id, corpus_id, source_path, title?, kind?, acl?

    tables: a TableMapper; tables it can map are read row by row by rule, the
    rest of the text goes to the model in chunks. Without it every table is
    read by the model.
    """
    started = time.time()
    before = _llm_counters(llm)
    pages, how = document_pages(raw, meta["source_path"])
    stats = {"doc_id": meta["doc_id"], "text": how, "pages": len(pages), "chunks": 0,
             "llm_errors": 0}
    document = {k: meta[k] for k in ("doc_id", "corpus_id", "source_path") if k in meta}
    document.update({"title": meta.get("title", ""), "kind": meta.get("kind", ""),
                     "pages": len(pages)})
    if meta.get("acl") is not None:
        document["acl"] = meta["acl"]
    if not pages:
        stats["seconds"] = round(time.time() - started, 2)
        return {"document": document, "entities": [], "mentions": [], "relations": []}, stats

    id_hits = find_identifiers(pages, profile)
    results = []
    prose_pages = pages
    if tables is not None:
        prose_pages = []
        stats.update({"tables": 0, "table_rows": 0})
        for page, text in pages:
            mapped = []
            for t in find_tables(page, text):
                try:
                    mapping = tables.mapping_for(t, meta.get("title", ""))
                except LLMError as e:
                    stats["llm_errors"] += 1
                    stats.setdefault("llm_error_detail", []).append(str(e)[:200])
                    mapping = None
                if mapping:
                    results.append(apply_mapping(t, mapping, profile, "t%d" % len(results)))
                    mapped.append(t)
                    stats["tables"] += 1
                    stats["table_rows"] += len(t.rows)
            rest = blank_tables(text, mapped)
            form = find_form(page, rest) if FORMS else None
            mapping = None
            if form is not None:
                try:
                    mapping = tables.mapping_for(form, meta.get("title", ""))
                except LLMError as e:
                    stats["llm_errors"] += 1
                    stats.setdefault("llm_error_detail", []).append(str(e)[:200])
            if mapping:
                results.append(apply_mapping(form, mapping, profile, "f%d" % len(results)))
                form.spans = form_spans(form, mapping)
                rest = blank_tables(rest, [form])
                stats["forms"] = stats.get("forms", 0) + 1
                stats["form_fields"] = stats.get("form_fields", 0) + len(form.spans)
            prose_pages.append((page, rest))
    chunks = [c for c in chunk_pages(prose_pages) if len(c.text.strip()) >= MIN_PROSE_CHARS]
    stats["chunks"] = len(chunks)
    for chunk in chunks:
        try:
            results.append(llm_extract(chunk, profile, llm, meta.get("title", "")))
        except LLMError as e:
            stats["llm_errors"] += 1
            stats.setdefault("llm_error_detail", []).append(str(e)[:200])
    ents, rels = _consolidate(results, id_hits)
    spans = _spans(pages, ents)
    ents = {ref: e for ref, e in ents.items() if ref in spans}
    rels = [r for r in rels if r.a in ents and r.b in ents]
    rels += co_mentions(pages, {ref: [(p, s, e) for p, s, e, _ in v] for ref, v in spans.items()
                                if ents[ref].type != "event"},
                        profile["cooccurrence"]["max_entities"])

    doc_id = meta["doc_id"]
    scoped = _containers(profile, pages, ents, rels, spans)
    for ref, (container, evidence) in scoped.items():
        if container is not None:
            first = min(spans[ref])[0] if spans.get(ref) else None
            ents[ref].attrs.setdefault("within", (ents[container].names[0], first))
        if evidence is not None:
            rels.append(evidence)
    strong = resolver.strong_key_subtypes()
    held: dict[str, set[str]] = {}
    for r in rels:
        if r.type in resolver.holding and ents[r.a].type == "person":
            e = ents[r.b]
            if e.type == "identifier" and e.subtype in strong and e.value:
                held.setdefault(r.a, set()).add("%s:%s" % (e.subtype, e.value))
    gid = {ref: resolver.resolve(doc_id, e, frozenset(held.get(ref, ())))
           for ref, e in ents.items()}
    for ref, e in ents.items():
        # Cell 102 of one prison is not cell 102 of another; with no container
        # named, the place stays this document's own (no cross-document link).
        container = scoped.get(ref, (None, None))[0]
        if container is not None:
            gid[ref] = "%s@%s" % (gid[ref], gid[container])
        elif ref in scoped or _generic(e):
            gid[ref] = "%s@doc:%s" % (gid[ref], doc_id)
    entities, mentions, relations = {}, [], {}
    for ref, e in ents.items():
        g = gid[ref]
        item = entities.setdefault(g, {"id": g, "type": e.type, "subtype": e.subtype,
                                       "label": e.names[0], "attributes": []})
        for key, (value, page) in e.attrs.items():
            item["attributes"].append({"name": key, "value": value, "page": page})
        for page, s, end, text in spans[ref]:
            mentions.append({"id": "%s:m%d" % (doc_id, len(mentions)), "entity_id": g,
                             "text": text, "page": page, "start": s, "end": end,
                             "method": "rule" if e.type == "identifier" and e.value else e.method})
    spec = {r["id"]: r for r in profile["relation_types"]}
    for r in rels:
        a, b = gid[r.a], gid[r.b]
        if a == b:
            continue
        if spec[r.type]["symmetric"] and b < a:
            a, b = b, a
        key = (r.type, a, b, r.start, r.end)
        # The id follows the relation's key: a relationship keeps the id of the
        # document that created it while others add evidence, so a positional id
        # could meet a different relationship when that document is read again.
        rid = "%s:r%s" % (doc_id, hashlib.sha1(repr(key).encode("utf-8")).hexdigest()[:12])
        rel = relations.setdefault(key, {"id": rid, "type": r.type,
                                         "a": a, "b": b, "start": r.start, "end": r.end,
                                         "raw_label": r.raw_label, "evidence": []})
        if all(ev["quote"] != r.quote for ev in rel["evidence"]):
            rel["evidence"].append({"quote": r.quote, "page": r.page, "method": r.method,
                                    "confidence": r.confidence})
    after = _llm_counters(llm)
    stats.update({"entities": len(entities), "mentions": len(mentions),
                  "relations": sum(1 for r in relations.values() if r["type"] != "CO_MENTIONED"),
                  "co_mentions": sum(1 for r in relations.values() if r["type"] == "CO_MENTIONED"),
                  "llm_calls": after["calls"] - before["calls"],
                  "llm_seconds": round(after["seconds"] - before["seconds"], 1),
                  "llm_prompt_tokens": after["prompt_tokens"] - before["prompt_tokens"],
                  "llm_output_tokens": after["output_tokens"] - before["output_tokens"],
                  "llm_prompt_seconds": round(after["prompt_seconds"] - before["prompt_seconds"], 2),
                  "llm_eval_seconds": round(after["eval_seconds"] - before["eval_seconds"], 2),
                  "llm_load_seconds": round(after["load_seconds"] - before["load_seconds"], 2),
                  "llm_truncated": after["truncated"] - before["truncated"],
                  "seconds": round(time.time() - started, 1)})
    return {"document": document, "entities": list(entities.values()), "mentions": mentions,
            "relations": list(relations.values())}, stats
