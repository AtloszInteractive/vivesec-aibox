"""Company profile proposals read from the organisation's own documents (E12).

An administrator names documents (articles of association, rules of
procedure, policies, a glossary); each is read whole, in parts, and the local
model lists the profile facts each part states, in the fixed schema
(company_profile.SCHEMA), each with a verbatim quote. A proposal whose quote
is not in the part, or whose field is unknown, is dropped. What is left
becomes a DRAFT with its source (path, page, quote) and, by default, the
document's own classification: the administrator approves, edits or drops it.
Nothing a document says becomes effective by itself.

The reading runs on the heavy slot (one job at a time on the GPU) and can be
cancelled; this module holds the pure parts: the prompt, the parts and the
checks. Pure stdlib.
"""
import json

import company_profile
import deep

PART_CHARS = 9000
MAX_DOCUMENTS = 20
MAX_PARTS = 40
MAX_PER_PART = 40
PART_PREDICT = 3000
PART_TIMEOUT = 600

_FACT = {"type": "object", "properties": {
    "group": {"type": "string"}, "field": {"type": "string"}, "label": {"type": "string"},
    "value": {"type": "string"}, "quote": {"type": "string"}},
    "required": ["group", "field", "value", "quote"]}
SCHEMA = {"type": "object", "properties": {"facts": {"type": "array", "items": _FACT}},
          "required": ["facts"]}


def system_prompt(lang=None):
    fields = []
    for group, glabel, items in company_profile.SCHEMA:
        for field, flabel, repeatable in items:
            fields.append("- %s/%s: %s - %s%s" % (
                group, field, glabel, flabel,
                " (one per item; `label` names the item)" if repeatable else ""))
    return (
        "You read ONE part of a document of an organisation and list what it states about "
        "the organisation itself, for its profile. Use only these fields:\n"
        + "\n".join(fields) + "\n"
        "Rules:\n"
        "- Only what the part states about the organisation that owns the document (its "
        "name, seat, units, their heads and tasks, roles, policies, the words it uses); not "
        "about the people it deals with, cases, events or other organisations.\n"
        "- `value` says it in one or two plain sentences, in the document's language; for a "
        "unit give its head, tasks and to whom it reports when stated; for a policy its key "
        "rules; for a term its meaning.\n"
        "- `label` names the item of a repeatable field (the unit, the role, the policy's "
        "title, the term); leave it empty for the other fields.\n"
        "- `quote` is the sentence of the part that states it, copied character for "
        "character.\n"
        "- Never add what the part does not say. When it states nothing of the kind, "
        "return an empty list.\n"
        "Return JSON only: {\"facts\": [...]}."
        + (" Write the values in %s." % lang if lang else ""))


def parts(chunks, budget=PART_CHARS):
    """The document's passages in consecutive parts of at most `budget` characters."""
    return deep.batches(chunks, budget)[:MAX_PARTS]


def part_text(group):
    return "\n\n".join((c.get("text") or "").strip() for c in group)


def user_prompt(name, group):
    return "DOCUMENT: %s\nPART:\n%s" % (name, part_text(group))


def _page_of(quote, group):
    for chunk in group:
        if deep.quoted_in(quote, chunk.get("text") or ""):
            return chunk.get("page_number")
    return group[0].get("page_number") if group else None


def proposals(content, group, path, classification):
    """[(fact, source)] the model's answer gives for one part, checked: the
    field exists, a repeatable item is named, the quote is in the part."""
    try:
        data = json.loads(content) if isinstance(content, str) else content
    except ValueError:
        return []
    items = data.get("facts") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return []
    text = part_text(group)
    out = []
    for item in items[:MAX_PER_PART]:
        if not isinstance(item, dict):
            continue
        quote = item.get("quote")
        if not isinstance(quote, str) or not quote.strip() or not deep.quoted_in(quote, text):
            continue
        raw = {"group": item.get("group"), "field": item.get("field"),
               "label": item.get("label") if isinstance(item.get("label"), str) else "",
               "value": item.get("value"), "classification": classification}
        key = (raw["group"], raw["field"])
        if key in company_profile.FIELDS and not company_profile.FIELDS[key]["repeatable"]:
            raw["label"] = ""
        try:
            fact = company_profile.clean_fact(raw)
        except company_profile.ProfileInvalid:
            continue
        out.append((fact, {"path": path, "page": _page_of(quote, group),
                           "quote": " ".join(quote.split())[:company_profile.MAX_QUOTE]}))
    return out
