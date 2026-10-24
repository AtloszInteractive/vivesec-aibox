"""Entity dossier (E26): one person's (or place's, number's ...) relations as a document.

Built from the graph within the caller's scope and access, rendered by
docgen (DOCX/PDF). Every relation line carries [n] references into a source
list (document, page, verbatim quote), so the dossier can be checked against
the documents. Evidence from a document the adapter's policy hides is left
out; a relation left without evidence is left out.
"""
import re
import time

TEXT = {
    "title": {"en": "Dossier", "hu": "Dosszié", "da": "Dossier", "de": "Dossier"},
    "generated": {"en": "Generated", "hu": "Készült", "da": "Udarbejdet", "de": "Erstellt"},
    "by": {"en": "for", "hu": "kérte", "da": "for", "de": "für"},
    "scope": {"en": "Scope", "hu": "Hatókör", "da": "Omfang", "de": "Umfang"},
    "profile": {"en": "Domain profile", "hu": "Szakterületi profil", "da": "Domæneprofil",
                "de": "Fachprofil"},
    "basics": {"en": "Basic data", "hu": "Alapadatok", "da": "Grunddata", "de": "Grunddaten"},
    "kind": {"en": "Kind", "hu": "Fajta", "da": "Type", "de": "Art"},
    "aliases": {"en": "Also known as", "hu": "Más néven", "da": "Også kendt som",
                "de": "Auch bekannt als"},
    "seen": {"en": "Mentioned", "hu": "Említve", "da": "Nævnt", "de": "Erwähnt"},
    "times_in": {"en": "times in %d documents", "hu": "alkalommal, %d dokumentumban",
                 "da": "gange i %d dokumenter", "de": "Mal in %d Dokumenten"},
    "relations": {"en": "Relations", "hu": "Kapcsolatok", "da": "Relationer",
                  "de": "Beziehungen"},
    "none": {"en": "No relations in the documents you may see.",
             "hu": "Az Ön számára látható dokumentumokban nincs kapcsolat.",
             "da": "Ingen relationer i de dokumenter, du har adgang til.",
             "de": "Keine Beziehungen in den Dokumenten, die Sie sehen dürfen."},
    "timeline": {"en": "Timeline", "hu": "Idővonal", "da": "Tidslinje", "de": "Zeitleiste"},
    "sources": {"en": "Sources", "hu": "Források", "da": "Kilder", "de": "Quellen"},
    "page": {"en": "p.", "hu": "o.", "da": "s.", "de": "S."},
    "note": {"en": "Compiled by the AI Box from the documents visible to the requester. Each "
                   "statement refers to its source [n]; relations found only by a model are to "
                   "be verified against the source.",
             "hu": "Az AI Box állította össze a kérő számára látható dokumentumokból. Minden "
                   "állítás a forrására hivatkozik [n]; a modell által talált kapcsolatokat a "
                   "forrással kell ellenőrizni.",
             "da": "Udarbejdet af AI Boxen ud fra de dokumenter, rekvirenten har adgang til. "
                   "Hvert udsagn henviser til sin kilde [n]; relationer fundet af en model skal "
                   "kontrolleres mod kilden.",
             "de": "Von der AI Box aus den für den Anfragenden sichtbaren Dokumenten erstellt. "
                   "Jede Aussage verweist auf ihre Quelle [n]; von einem Modell gefundene "
                   "Beziehungen sind an der Quelle zu prüfen."},
    "attr": {"birth_date": {"en": "Date of birth", "hu": "Születési dátum",
                            "da": "Fødselsdato", "de": "Geburtsdatum"},
             "birth_place": {"en": "Place of birth", "hu": "Születési hely",
                             "da": "Fødested", "de": "Geburtsort"},
             "mother_name": {"en": "Mother's name", "hu": "Anyja neve",
                             "da": "Moders navn", "de": "Name der Mutter"},
             "id_number": {"en": "ID number", "hu": "Azonosító szám",
                           "da": "ID-nummer", "de": "Ausweisnummer"},
             "registry_number": {"en": "Registry number", "hu": "Nyilvántartási szám",
                                 "da": "Registreringsnummer", "de": "Registernummer"}},
}
LANGS = ("en", "hu", "da", "de")
MAX_QUOTE = 300


def _t(key, lang):
    return TEXT[key].get(lang) or TEXT[key]["en"]


def build(client, scope_body, visible, entity_id, lang="en", user="", scope_names=()):
    """(markdown, title) of one entity's dossier; None when the entity is not visible."""
    lang = lang if lang in LANGS else "en"
    hood = client.post("/query/neighborhood", dict(scope_body, id=entity_id, hops=1, limit=500))
    nodes = {n["id"]: n for n in hood.get("nodes") or [] if n}
    node = nodes.get(entity_id)
    if node is None:
        return None
    profile = client.post("/profile/get", {}).get("profile") or {}
    rel_labels = {r["id"]: r.get("label") or {} for r in profile.get("relation_types") or []}
    sub_labels = {s["id"]: s.get("label") or {} for s in profile.get("entity_subtypes") or []}
    documents = hood.get("documents") or {}

    def shown(doc_id):
        doc = documents.get(doc_id)
        return doc is not None and visible(doc.get("source_path") or "")

    sources, source_no = [], {}

    def ref(ev):
        key = (ev["doc_id"], ev.get("page"), ev.get("quote") or "")
        if key not in source_no:
            source_no[key] = len(sources) + 1
            sources.append(key)
        return source_no[key]

    def label(labels, key):
        lab = labels.get(key) or {}
        return lab.get(lang) or lab.get("en") or key

    title = "%s: %s" % (_t("title", lang), node["label"])
    lines = ["# " + title, ""]
    meta = "%s %s" % (_t("generated", lang), time.strftime("%Y-%m-%d %H:%M"))
    if user:
        meta += " · %s %s" % (_t("by", lang), user)
    lines.append(meta)
    if scope_names:
        lines.append("%s: %s" % (_t("scope", lang), ", ".join(scope_names)))
    if profile:
        lines.append("%s: %s v%s" % (_t("profile", lang), profile.get("id"),
                                     profile.get("version")))
    lines += ["", "## " + _t("basics", lang)]
    kind = label(sub_labels, node["subtype"]) if node.get("subtype") else node["type"]
    lines.append("- %s: %s" % (_t("kind", lang), kind))
    others = [a for a in node.get("aliases") or [] if a != node["label"]]
    if others:
        lines.append("- %s: %s" % (_t("aliases", lang), "; ".join(others)))
    lines.append("- %s: %d %s" % (_t("seen", lang), node.get("mentions") or 0,
                                  _t("times_in", lang) % len(node.get("documents") or [])))
    for attr in node.get("attributes") or []:
        if not shown(attr.get("doc_id")):
            continue
        name = TEXT["attr"].get(attr["name"], {}).get(lang) or attr["name"]
        n = ref({"doc_id": attr["doc_id"], "page": attr.get("page"), "quote": ""})
        lines.append("- %s: %s [%d]" % (name, attr["value"], n))

    edges = []
    for edge in hood.get("edges") or []:
        if entity_id not in (edge["a"], edge["b"]):
            continue
        evidence = [ev for ev in edge.get("evidence") or [] if shown(ev.get("doc_id"))]
        if evidence:
            edges.append((edge, evidence))
    lines += ["", "## " + _t("relations", lang)]
    if not edges:
        lines.append(_t("none", lang))
    by_type = {}
    for edge, evidence in edges:
        by_type.setdefault(edge["type"], []).append((edge, evidence))
    for rtype in sorted(by_type, key=lambda t: label(rel_labels, t)):
        lines += ["", "### " + label(rel_labels, rtype)]
        rows = sorted(by_type[rtype], key=lambda x: (x[0].get("start") or "", x[0]["id"]))
        for edge, evidence in rows:
            other = nodes.get(edge["b"] if edge["a"] == entity_id else edge["a"]) or {}
            name = other.get("label") or "?"
            when = edge.get("start") or ""
            if edge.get("end") and edge["end"] != when:
                when = "%s – %s" % (when, edge["end"])
            if when and when in name:
                when = ""
            raw = edge.get("raw_label") or ""
            # A raw label that is a type code (USES_PHONE) says nothing to a reader.
            extra = raw if raw != edge["type"] and not re.fullmatch(r"[A-Z0-9_]+", raw) else ""
            detail = ", ".join(x for x in (when, extra) if x)
            refs = "".join("[%d]" % ref(ev) for ev in evidence[:5])
            lines.append("- %s%s %s" % (name, " (%s)" % detail if detail else "", refs))

    dated = sorted(((e, ev) for e, ev in edges if e.get("start")),
                   key=lambda x: (x[0]["start"], x[0]["type"]))
    if dated:
        lines += ["", "## " + _t("timeline", lang)]
        for edge, evidence in dated:
            other = nodes.get(edge["b"] if edge["a"] == entity_id else edge["a"]) or {}
            lines.append("- %s · %s · %s [%d]" % (edge["start"], label(rel_labels, edge["type"]),
                                                  other.get("label") or "?", ref(evidence[0])))

    lines += ["", "## " + _t("sources", lang)]
    for n, (doc_id, page, quote) in enumerate(sources, 1):
        doc = documents.get(doc_id) or {}
        where = doc.get("source_path") or doc_id
        if page:
            where += " (%s %s)" % (_t("page", lang), page)
        text = " ".join(quote.split())
        if len(text) > MAX_QUOTE:
            text = text[:MAX_QUOTE - 1] + "…"
        lines.append("[%d] %s%s" % (n, where, " — „%s”" % text if text else ""))
    lines += ["", "---", _t("note", lang)]
    return "\n".join(lines), title
