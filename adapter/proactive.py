"""Proactive chat (BV): what the analyst may ask next, and what the box noticed
while answering -- built from what the planner's run found, without a model call.

  suggestions(run, query, turns)  -> [{"label", "question"}]  at most MAX_SUGGESTIONS
  observations(run, query)        -> [str]                    at most MAX_OBSERVATIONS

Suggestions are whole questions in the user's language for the people, places,
numbers and events the answer was about, skipping what was just asked; a cut
result offers to narrow it, a longer conversation a summary. Observations are
facts about the evidence, never about a person's risk: relations only named
together, a single source, differing personal data, informant (protected) data,
documents not fully processed yet, data after the period asked about. The names
stand after a colon, never inflected, so no language's grammar breaks.
"""
import re

MAX_SUGGESTIONS = 4
MAX_OBSERVATIONS = 4
PROTECTED_SEGMENT = "/~protected/"
_YEAR = re.compile(r"\b((?:19|20)\d{2})\b")
_DATE_YEAR = re.compile(r"\b((?:19|20)\d{2})-\d{2}")

TEXT = {
    "timeline": {
        "hu": "Mutasd meg az idővonalát: {a}",
        "en": "Show the timeline of {a}",
        "da": "Vis tidslinjen for {a}",
        "de": "Zeige die Zeitleiste von {a}"},
    "relations": {
        "hu": "Kikkel áll közvetlen kapcsolatban: {a}?",
        "en": "Who is {a} directly related to?",
        "da": "Hvem har {a} direkte relationer til?",
        "de": "Mit wem steht {a} in direkter Beziehung?"},
    "shared_identifiers": {
        "hu": "Van-e közös telefonszáma, címe vagy más azonosítója valakivel: {a}?",
        "en": "Does {a} share a phone number, address or other identifier with anyone?",
        "da": "Deler {a} telefonnummer, adresse eller en anden identifikator med nogen?",
        "de": "Teilt {a} eine Telefonnummer, Adresse oder andere Kennung mit jemandem?"},
    "investigate": {
        "hu": "Vizsgáld meg két szinten a kapcsolatait: {a}",
        "en": "Investigate the relations of {a} on two levels",
        "da": "Undersøg relationerne for {a} på to niveauer",
        "de": "Untersuche die Beziehungen von {a} über zwei Ebenen"},
    "copresence": {
        "hu": "Kikkel volt egy helyen (pl. zárkában) ugyanabban az időben: {a}?",
        "en": "Who was in the same place (e.g. cell) at the same time as {a}?",
        "da": "Hvem var samme sted (f.eks. celle) på samme tid som {a}?",
        "de": "Wer war zur gleichen Zeit am selben Ort (z. B. Haftraum) wie {a}?"},
    "placed_here": {
        "hu": "Kik voltak itt elhelyezve, és mikor: {a}?",
        "en": "Who was placed in {a}, and when?",
        "da": "Hvem var anbragt i {a}, og hvornår?",
        "de": "Wer war wann in {a} untergebracht?"},
    "users_of": {
        "hu": "Kik használják, vagy kik kapcsolódnak ehhez: {a}?",
        "en": "Who uses {a}, or is linked to it?",
        "da": "Hvem bruger {a} eller er knyttet til det?",
        "de": "Wer nutzt {a} oder ist damit verbunden?"},
    "involved": {
        "hu": "Kik érintettek ebben: {a}?",
        "en": "Who is involved in {a}?",
        "da": "Hvem er involveret i {a}?",
        "de": "Wer ist an {a} beteiligt?"},
    "connection": {
        "hu": "Milyen úton kapcsolódnak egymáshoz: {a} és {b}?",
        "en": "How are {a} and {b} connected?",
        "da": "Hvordan er {a} og {b} forbundet?",
        "de": "Wie sind {a} und {b} verbunden?"},
    "common": {
        "hu": "Kik a közös kapcsolataik: {a} és {b}?",
        "en": "Who do {a} and {b} have in common?",
        "da": "Hvem har {a} og {b} til fælles?",
        "de": "Wen haben {a} und {b} gemeinsam?"},
    "narrow_year": {
        "hu": "Ugyanez, csak erre az évre: {year}",
        "en": "The same, for {year} only",
        "da": "Det samme, kun for {year}",
        "de": "Dasselbe, nur für {year}"},
    "narrow_direct": {
        "hu": "Ugyanez, csak a közvetlen kapcsolatokkal",
        "en": "The same, with direct relations only",
        "da": "Det samme, kun med direkte relationer",
        "de": "Dasselbe, nur mit direkten Beziehungen"},
    "summary": {
        "hu": "Foglald össze, mit találtunk eddig ebben a beszélgetésben",
        "en": "Summarise what we have found so far in this conversation",
        "da": "Opsummer, hvad vi har fundet indtil nu i denne samtale",
        "de": "Fasse zusammen, was wir in diesem Gespräch bisher gefunden haben"},
    # observations
    "weak_all": {
        "hu": "A talált kapcsolatok mind csak együttes említésen alapulnak (ugyanabban a "
              "szövegrészben szerepelnek): közvetlen kapcsolatot a források nem állítanak.",
        "en": "All relations found rest only on being named together (in the same passage): "
              "the sources do not state a direct link.",
        "da": "Alle fundne relationer bygger kun på, at de nævnes sammen (i samme passage): "
              "kilderne angiver ikke en direkte forbindelse.",
        "de": "Alle gefundenen Beziehungen beruhen nur auf gemeinsamer Nennung (in derselben "
              "Passage): Die Quellen behaupten keine direkte Verbindung."},
    "weak_some": {
        "hu": "A talált kapcsolatok közül {n} csak együttes említésen alapul.",
        "en": "{n} of the relations found rest only on being named together.",
        "da": "{n} af de fundne relationer bygger kun på, at de nævnes sammen.",
        "de": "{n} der gefundenen Beziehungen beruhen nur auf gemeinsamer Nennung."},
    "single_source": {
        "hu": "Minden talált kapcsolat egyetlen forrásból származik: {title}. Érdemes más "
              "forrással is megerősíteni.",
        "en": "Every relation found comes from a single source: {title}. Worth confirming "
              "from another source.",
        "da": "Alle fundne relationer stammer fra én kilde: {title}. Værd at bekræfte fra en "
              "anden kilde.",
        "de": "Alle gefundenen Beziehungen stammen aus einer einzigen Quelle: {title}. Eine "
              "Bestätigung aus einer weiteren Quelle ist ratsam."},
    "differs": {
        "hu": "Eltérő {what} a forrásokban ({name}): {values}. Ellenőrizd, valóban egy "
              "személyről van-e szó.",
        "en": "The sources differ on {what} ({name}): {values}. Check that this is one person.",
        "da": "Kilderne er uenige om {what} ({name}): {values}. Kontrollér, at det er én person.",
        "de": "Die Quellen weichen bei {what} ab ({name}): {values}. Prüfen Sie, ob es sich um "
              "eine Person handelt."},
    "protected": {
        "hu": "A válasz védett (informátori) adatot is tartalmaz: csak a jogosultak láthatják, "
              "továbbadni nem szabad.",
        "en": "The answer includes protected (informant) data: only those entitled may see it; "
              "do not pass it on.",
        "da": "Svaret indeholder beskyttede (informant)data: kun de berettigede må se dem; "
              "de må ikke videregives.",
        "de": "Die Antwort enthält geschützte (Informanten-)Daten: nur Berechtigte dürfen sie "
              "sehen; nicht weitergeben."},
    "partial": {
        "hu": "{n} forrásdokumentum szövegét a rendszer még nem dolgozta fel teljesen; a "
              "feldolgozás után több kapcsolat is előkerülhet.",
        "en": "{n} source documents have not been fully processed yet; more relations may "
              "appear once they are.",
        "da": "{n} kildedokumenter er endnu ikke fuldt behandlet; flere relationer kan dukke op "
              "bagefter.",
        "de": "{n} Quelldokumente sind noch nicht vollständig verarbeitet; danach können weitere "
              "Beziehungen hinzukommen."},
    "later": {
        "hu": "A megkérdezett időszak ({year}) után is van adat: {name} — {n} további "
              "kapcsolat, legutóbb {last}.",
        "en": "There is data after the period asked about ({year}): {name} — {n} more "
              "relations, the latest {last}.",
        "da": "Der er data efter den spurgte periode ({year}): {name} — {n} flere relationer, "
              "senest {last}.",
        "de": "Es gibt Daten nach dem erfragten Zeitraum ({year}): {name} — {n} weitere "
              "Beziehungen, zuletzt {last}."},
}
WHAT = {
    "birth_date": {"hu": "születési idő", "en": "date of birth", "da": "fødselsdato",
                   "de": "Geburtsdatum"},
    "birth_place": {"hu": "születési hely", "en": "place of birth", "da": "fødested",
                    "de": "Geburtsort"},
    "mother_name": {"hu": "anyja neve", "en": "mother's name", "da": "mors navn",
                    "de": "Name der Mutter"},
}
# Which tool asks what about an entity of each type, in the order offered.
BY_TYPE = {
    "person": ["timeline", "relations", "shared_identifiers", "investigate"],
    "organization": ["relations", "shared_identifiers"],
    "place": ["placed_here"],
    "identifier": ["users_of"],
    "event": ["involved"],
}
# The planner tools that already answered the same question.
SAME_TOOL = {"timeline": {"timeline"}, "relations": {"relations", "investigate"},
             "shared_identifiers": {"shared_identifiers"}, "investigate": {"investigate"},
             "copresence": {"copresence"}, "placed_here": {"relations", "investigate"},
             "users_of": {"relations", "shared_identifiers"}, "involved": {"relations"},
             "connection": {"connection"}, "common": {"common"}}


def _t(key, code, **values):
    entry = TEXT[key]
    return (entry.get(code) or entry["en"]).format(**values)


def _fold(text):
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFD", str(text or "").casefold())
                   if unicodedata.category(c) != "Mn").strip()


def _asked_nodes(run):
    """The entities the message itself named, in order (then those the facts name
    first, when it named none)."""
    order = [i for ids in run._names.values() for i in ids if i in run._asked]
    if not order:
        order = list(run._people[:1])
    out, seen = [], set()
    for eid in order:
        node = run.nodes.get(eid)
        if node and node.get("label") and _fold(node["label"]) not in seen:
            seen.add(_fold(node["label"]))
            out.append(node)
    return out


def _done(run, label):
    """The tools already run about this name."""
    key = _fold(label)
    out = set()
    for call in run.calls:
        args = call.get("args") or {}
        names = [args.get(k) for k in ("entity", "start", "a", "b", "name")]
        if any(isinstance(n, str) and _fold(n) == key for n in names):
            out.add(call["tool"])
    return out


def _years_asked(query):
    return sorted({int(y) for y in _YEAR.findall(query or "")})


def suggestions(run, query, turns=0, code=None):
    """Up to MAX_SUGGESTIONS next questions about what the answer found; `code`:
    the language ("hu") when the run's own (from the question) is only a guess."""
    code = code or run.code
    out = []

    def add(key, **values):
        question = _t(key, code, **values)
        if _fold(question) != _fold(query) and all(s["question"] != question for s in out):
            out.append({"label": question, "question": question})

    nodes = _asked_nodes(run)
    # A cut result: offer to narrow it before anything else.
    cut = bool(run._table_notes) or len(run.contexts) >= run.max_facts
    if cut:
        if not _years_asked(query):
            # The latest year of everything found, the facts left out by the cut too.
            years = [int(y) for c in run.contexts for y in _DATE_YEAR.findall(c.get("text") or "")]
            years += [int(d[:4]) for ds in run._tally.values() for d in ds if d[:4].isdigit()]
            if years:
                add("narrow_year", year=max(years))
        if any(c["tool"] == "investigate" and (c.get("args") or {}).get("levels", 2) > 1
               for c in run.calls):
            add("narrow_direct")
    people = [n for n in nodes if n.get("type") == "person"]
    if len(people) >= 2:
        a, b = people[0]["label"], people[1]["label"]
        done = _done(run, a) | _done(run, b)
        for key in ("connection", "common"):
            if not SAME_TOOL[key] & done:
                add(key, a=a, b=b)
    for node in nodes[:2]:
        kinds = list(BY_TYPE.get(node.get("type"), []))
        if node.get("subtype") == "inmate":
            kinds.insert(2, "copresence")
        done = _done(run, node["label"])
        for key in kinds:
            if not SAME_TOOL.get(key, {key}) & done:
                add(key, a=node["label"])
    if turns >= 2:
        add("summary")
    return out[:MAX_SUGGESTIONS]


def _graph_facts(run):
    return [c for c in run.contexts if c.get("origin") == "graph" and c.get("source_path")]


def observations(run, query, code=None):
    """Up to MAX_OBSERVATIONS facts about the evidence of the answer."""
    code = code or run.code
    out = []
    facts = _graph_facts(run)
    if any(PROTECTED_SEGMENT in (c.get("source_path") or "") for c in facts):
        out.append(_t("protected", code))
    if run.edge_facts and run.weak_facts == run.edge_facts:
        out.append(_t("weak_all", code))
    elif run.weak_facts:
        out.append(_t("weak_some", code, n=run.weak_facts))
    sources = {c["source_path"] for c in facts}
    if run.edge_facts >= 2 and len(sources) == 1:
        path = next(iter(sources))
        title = next((d.get("title") for d in run.documents.values()
                      if d.get("source_path") == path and d.get("title")), None)
        out.append(_t("single_source", code, title=title or path.rsplit("/", 1)[-1]))
    for node in _asked_nodes(run):
        if node.get("type") != "person":
            continue
        values = {}
        for attr in node.get("attributes") or []:
            doc = run.documents.get(attr.get("doc_id"))
            if attr.get("name") in WHAT and doc is not None \
                    and run.visible(doc.get("source_path") or ""):
                values.setdefault(attr["name"], {}).setdefault(
                    _fold(attr.get("value")), str(attr.get("value")))
        for name, seen in values.items():
            if len(seen) > 1:
                what = WHAT[name].get(code) or WHAT[name]["en"]
                out.append(_t("differs", code, what=what, name=node["label"],
                              values=" / ".join(sorted(seen.values())[:3])))
    partial = {d.get("source_path") for d in run.documents.values() if d.get("partial")}
    partial &= sources | {c.get("source_path") for c in run.contexts}
    if partial:
        out.append(_t("partial", code, n=len(partial)))
    asked = _years_asked(query)
    if asked:
        last_year = max(asked)
        named = {i for ids in run._names.values() for i in ids}
        for node in _asked_nodes(run):
            dates = sorted(d for (_, a, b), ds in run._tally.items()
                           if node["id"] in (a, b) and node["id"] in named
                           for d in ds if d[:4].isdigit() and int(d[:4]) > last_year)
            if dates:
                out.append(_t("later", code, year=last_year, name=node["label"],
                              n=len(dates), last=dates[-1]))
                break
    return out[:MAX_OBSERVATIONS]
