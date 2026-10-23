"""Chat orchestrator (E26 analyst mode): the model plans, the adapter runs the graph.

One planning call per message decides what the answer needs: graph queries
(relations, connection, timeline, ...), a document search, or a question back
to the user when a detail is missing. The adapter then runs the queries itself
within the caller's scope. The model never sees the graph API and cannot widen
the scope: it only names entities, and the names are resolved here. A name that
fits several different entities is asked back instead of guessed.

Every fact a query returns carries its verbatim quote and source, and becomes
one context in the RAG shape, so the answer cites it like any excerpt and the
UI opens the source the usual way. Evidence from documents the adapter's
policy hides is dropped (the graph's ACL copy may lag).
"""
import json
import re
import unicodedata

import llm

TOOLS = ("relations", "connection", "common", "timeline", "copresence",
         "shared_identifiers", "most_connected")
ENTITY_TYPES = ["person", "organization", "place", "identifier", "event", "object"]
MAX_CALLS = 4
MAX_CANDIDATES = 5
PLAN_TIMEOUT = 120
_DATE_RE = re.compile(r"^\d{4}(-\d{2}(-\d{2})?)?$")

PLANNER = (
    "You plan how to answer a message about people, organisations, places, "
    "identifiers (phone numbers, vehicles, ID or case numbers) and events found in the "
    "user's documents. You do not answer it; you choose tools.\n"
    "Tools (entity arguments are names as the user wrote them or as they appear in the "
    "conversation; dates are YYYY-MM-DD):\n"
    "- relations {entity}: everything directly linked to one entity\n"
    "- connection {a, b}: the chain of documented links between two entities\n"
    "- common {a, b}: entities linked to both\n"
    "- timeline {entity, from?, to?}: the dated events of one entity in order\n"
    "- copresence {entity, from?, to?}: who was at the same place or event at the same time\n"
    "- shared_identifiers {kind?}: identifiers or addresses used by more than one person "
    "or organisation (kind: a subtype such as phone, vehicle, address)\n"
    "- most_connected {kind}: the entities with the most links "
    "(kind: person, organization, place or identifier)\n"
    "Reply with one JSON object only, one of:\n"
    '{"action": "tools", "calls": [{"tool": "...", "args": {...}}], '
    '"search": "<the request as a standalone question for a document search, in the '
    'user\'s language>"}\n'
    '{"action": "clarify", "question": "<one short question to the user>"}\n'
    '{"action": "chat"} -- only when the message is about the conversation itself '
    "(summarise that, thanks, rephrase).\n"
    "Rules: at most 4 calls. Resolve pronouns and follow-ups from the conversation. "
    "Ask back ONLY when the request cannot be answered without the missing detail -- "
    "who or what is meant is unclear even with the conversation, or a second entity or "
    "a period the request depends on is missing. If the user has just answered your "
    "question, plan with that answer and never ask the same thing again. "
    "When no tool fits, plan calls: [] with a search."
)

_TEXT = {
    "ambiguous": {
        "en": "Several entries match \u201c%s\u201d. Which one do you mean?",
        "hu": "T\u00f6bb tal\u00e1lat is van erre: \u201e%s\u201d. Melyikre gondol?",
        "da": "Flere poster passer til \u201d%s\u201d. Hvilken mener du?",
        "de": "Mehrere Eintr\u00e4ge passen zu \u201e%s\u201c. Welchen meinen Sie?",
    },
    "documents": {"en": "%d documents", "hu": "%d dokumentum", "da": "%d dokumenter",
                  "de": "%d Dokumente"},
    "not_found": {
        "en": "Graph search: nothing named \u201c%s\u201d in the documents you may see.",
        "hu": "Gr\u00e1fkeres\u00e9s: \u201e%s\u201d nev\u0171 szerepl\u0151 nincs az \u00d6n "
              "sz\u00e1m\u00e1ra l\u00e1that\u00f3 dokumentumokban.",
        "da": "Grafs\u00f8gning: intet med navnet \u201d%s\u201d i de dokumenter, du har "
              "adgang til.",
        "de": "Graphsuche: nichts mit dem Namen \u201e%s\u201c in den Dokumenten, die Sie "
              "sehen d\u00fcrfen.",
    },
    "namesakes": {
        "en": "Graph search: %d separate records are named \u201c%s\u201d; the documents do "
              "not confirm they are the same.",
        "hu": "Gr\u00e1fkeres\u00e9s: %d k\u00fcl\u00f6n bejegyz\u00e9s viseli a \u201e%s\u201d "
              "nevet; a dokumentumok nem igazolj\u00e1k, hogy ugyanarr\u00f3l van sz\u00f3.",
        "da": "Grafs\u00f8gning: %d separate poster hedder \u201d%s\u201d; dokumenterne "
              "bekr\u00e6fter ikke, at det er den samme.",
        "de": "Graphsuche: %d getrennte Eintr\u00e4ge hei\u00dfen \u201e%s\u201c; die "
              "Dokumente best\u00e4tigen nicht, dass es dieselbe Person ist.",
    },
    "no_path": {
        "en": "Graph search: no documented chain of links between %s and %s.",
        "hu": "Gr\u00e1fkeres\u00e9s: %s \u00e9s %s k\u00f6z\u00f6tt nincs dokument\u00e1lt "
              "kapcsolatl\u00e1nc.",
        "da": "Grafs\u00f8gning: ingen dokumenteret k\u00e6de af forbindelser mellem %s og %s.",
        "de": "Graphsuche: keine dokumentierte Verbindungskette zwischen %s und %s.",
    },
    "nothing": {
        "en": "Graph search (%s): no result in the documents you may see.",
        "hu": "Gr\u00e1fkeres\u00e9s (%s): nincs tal\u00e1lat az \u00d6n sz\u00e1m\u00e1ra "
              "l\u00e1that\u00f3 dokumentumokban.",
        "da": "Grafs\u00f8gning (%s): intet resultat i de dokumenter, du har adgang til.",
        "de": "Graphsuche (%s): kein Ergebnis in den Dokumenten, die Sie sehen d\u00fcrfen.",
    },
    "ranking": {
        "en": "Graph statistics -- number of distinct linked entities, counted over the "
              "documents you may see:",
        "hu": "Gr\u00e1f-statisztika -- a kapcsol\u00f3d\u00f3 k\u00fcl\u00f6nb\u00f6z\u0151 "
              "szerepl\u0151k sz\u00e1ma, az \u00d6n sz\u00e1m\u00e1ra l\u00e1that\u00f3 "
              "dokumentumok alapj\u00e1n:",
        "da": "Grafstatistik -- antal forskellige forbundne enheder, talt i de dokumenter, "
              "du har adgang til:",
        "de": "Graphstatistik -- Anzahl verschiedener verbundener Eintr\u00e4ge, gez\u00e4hlt "
              "in den Dokumenten, die Sie sehen d\u00fcrfen:",
    },
    "together": {"en": "both at", "hu": "egyszerre itt:", "da": "begge ved",
                 "de": "beide bei"},
}
LANG_CODES = {"Hungarian": "hu", "English": "en", "Danish": "da", "German": "de"}


def _t(key, code):
    return _TEXT[key].get(code) or _TEXT[key]["en"]


def fold(text):
    return "".join(c for c in unicodedata.normalize("NFD", (text or "").casefold())
                   if unicodedata.category(c) != "Mn").strip()


# -- planning ------------------------------------------------------------------
def _transcript(history, turns=6, chars=600):
    lines = []
    for turn in (history or [])[-turns:]:
        content = (turn.get("content") or "").strip()
        if turn.get("role") in ("user", "assistant") and content:
            lines.append("%s: %s" % ("USER" if turn["role"] == "user" else "ASSISTANT",
                                     content[:chars]))
    return "\n".join(lines)


def _clean_args(tool, args):
    args = args if isinstance(args, dict) else {}
    out = {}
    for key in ("entity", "a", "b", "kind"):
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            out[key] = value.strip()[:120]
    for key in ("from", "to"):
        value = args.get(key)
        if isinstance(value, str) and _DATE_RE.match(value.strip()):
            out[key] = value.strip()
    need = {"relations": ("entity",), "timeline": ("entity",), "copresence": ("entity",),
            "connection": ("a", "b"), "common": ("a", "b")}.get(tool, ())
    return out if all(k in out for k in need) else None


def parse_plan(text):
    """The planner's reply as {"action", "calls", "search", "question"}, or None."""
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.strip("`").split("\n", 1)[-1]
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(text[start:end + 1])
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    action = data.get("action")
    if action == "clarify":
        question = data.get("question")
        if isinstance(question, str) and question.strip():
            return {"action": "clarify", "question": question.strip()[:500], "calls": [],
                    "search": ""}
        return None
    if action == "chat":
        return {"action": "chat", "calls": [], "search": ""}
    if action != "tools":
        return None
    calls = []
    for call in data.get("calls") or []:
        if not isinstance(call, dict) or call.get("tool") not in TOOLS:
            continue
        args = _clean_args(call["tool"], call.get("args"))
        if args is not None:
            calls.append({"tool": call["tool"], "args": args})
    search = data.get("search")
    return {"action": "tools", "calls": calls[:MAX_CALLS],
            "search": search.strip()[:500] if isinstance(search, str) else ""}


def plan(question, history, lang=None):
    """One planning call; None when the model is off, fails or answers nonsense
    (the caller then keeps the plain document + graph-facts path)."""
    if llm.GENERATE == "off":
        return None
    system = PLANNER + (" Write any question to the user in %s." % lang if lang else "")
    convo = _transcript(history)
    user = ("CONVERSATION:\n%s\n\nMESSAGE: %s" % (convo, question)) if convo \
        else "MESSAGE: %s" % question
    try:
        content, _, _ = llm._chat(system, user, timeout=PLAN_TIMEOUT, stream=False,
                                  num_predict=400, response_format="json")
    except Exception:  # noqa: BLE001
        return None
    return parse_plan(content)


# -- running the plan ----------------------------------------------------------
class Clarify(Exception):
    """A name the user gave fits several different entities."""

    def __init__(self, question):
        super().__init__(question)
        self.question = question


class Run:
    """Executes one plan against the graph within one caller's scope."""

    def __init__(self, client, scope_body, visible, lang=None, max_facts=40):
        self.client = client
        self.scope = scope_body
        self.visible = visible
        self.code = LANG_CODES.get(lang or "", "en")
        self.max_facts = max_facts
        self.contexts = []
        self.notes = []
        self.documents = {}
        self.nodes = {}
        self.calls = []
        self._seen = set()
        self._names = {}
        self._profile = None

    def query(self, name, **params):
        res = self.client.post("/query/" + name, dict(self.scope, **params))
        self.documents.update(res.get("documents") or {})
        return res

    # labels from the domain profile, in the user's language
    def _label_maps(self):
        if self._profile is None:
            profile = self.client.post("/profile/get", {}).get("profile") or {}
            self._profile = (
                {r["id"]: r.get("label") or {} for r in profile.get("relation_types") or []},
                {s["id"]: s.get("label") or {} for s in profile.get("entity_subtypes") or []})
        return self._profile

    def _label(self, labels, key):
        lab = labels.get(key) or {}
        return lab.get(self.code) or lab.get("en") or key

    def describe(self, node):
        _, subtypes = self._label_maps()
        kind = self._label(subtypes, node.get("subtype")) if node.get("subtype") else node["type"]
        return "%s (%s, %s)" % (node["label"], kind,
                                _t("documents", self.code) % len(node.get("documents") or []))

    def resolve(self, name):
        """Ids for one name. Namesakes (same name, separate records) are kept
        together; different entities that fit a partial name are asked back."""
        key = fold(name)
        if key in self._names:
            return self._names[key]
        found = self.query("find", text=name, limit=8, types=ENTITY_TYPES).get("entities") or []
        exact = [n for n in found if key in {fold(x) for x in [n["label"], *n.get("aliases", [])]}]
        if exact:
            picked = exact
        elif len(found) == 1 or len({fold(n["label"]) for n in found}) == 1:
            picked = found
        elif found:
            options = "\n".join("- " + self.describe(n) for n in found[:MAX_CANDIDATES])
            raise Clarify("%s\n%s" % (_t("ambiguous", self.code) % name, options))
        else:
            # Inflected forms ("Kovács Jánossal") are matched by the mention finder.
            picked = (self.query("mentioned", text=name, limit=1, types=ENTITY_TYPES)
                      .get("entities") or [])
        for node in picked:
            self.nodes[node["id"]] = node
        if not picked:
            self.notes.append(_t("not_found", self.code) % name)
        elif len(picked) > 1:
            self.notes.append(_t("namesakes", self.code) % (len(picked), picked[0]["label"]))
        self._names[key] = [n["id"] for n in picked]
        return self._names[key]

    def _evidence(self, edge):
        return [ev for ev in edge.get("evidence") or []
                if ev.get("doc_id") in self.documents
                and self.visible(self.documents[ev["doc_id"]].get("source_path") or "")]

    def _add(self, text, ev):
        if len(self.contexts) >= self.max_facts:
            return False
        doc = self.documents[ev["doc_id"]]
        key = (text, doc.get("source_path"), ev.get("page"))
        if key in self._seen:
            return True
        self._seen.add(key)
        quote = " ".join((ev.get("quote") or "").split())
        self.contexts.append({
            "text": "%s%s" % (text, ' "%s"' % quote if quote else ""),
            "source_path": doc.get("source_path"), "corpus_id": doc.get("corpus_id"),
            "page_number": ev.get("page"), "chunk_id": None, "score": 1.0,
            "origin": "graph"})
        return True

    def _name(self, node_id):
        node = self.nodes.get(node_id) or {}
        return node.get("label") or node_id

    def edge_fact(self, edge):
        evidence = self._evidence(edge)
        if not evidence:
            return 0
        relations, _ = self._label_maps()
        name = self._label(relations, edge["type"])
        raw = edge.get("raw_label") or ""
        if raw and raw != edge["type"] and not re.fullmatch(r"[A-Z0-9_]+", raw):
            name = "%s (%s)" % (name, raw)
        when = edge.get("start") or ""
        if edge.get("end") and edge["end"] != when:
            when = "%s - %s" % (when, edge["end"])
        text = "%s -- %s -- %s%s." % (self._name(edge["a"]), name, self._name(edge["b"]),
                                      " (%s)" % when if when else "")
        return int(self._add(text, evidence[0]))

    def _remember(self, res):
        for node in res.get("nodes") or []:
            if node:
                self.nodes.setdefault(node["id"], node)

    def _edges(self, edges):
        ordered = sorted(edges, key=lambda e: (not e.get("start"), e.get("start") or "", e["id"]))
        return sum(self.edge_fact(e) for e in ordered)

    # -- the tools -------------------------------------------------------------
    def relations(self, args):
        facts = 0
        for eid in self.resolve(args["entity"]):
            res = self.query("neighborhood", id=eid, hops=1, limit=80)
            self._remember(res)
            facts += self._edges([e for e in res.get("edges") or [] if eid in (e["a"], e["b"])])
        return facts

    def connection(self, args):
        ids_a, ids_b = self.resolve(args["a"]), self.resolve(args["b"])
        facts, found = 0, False
        for a in ids_a[:3]:
            for b in ids_b[:3]:
                path = self.query("path", a=a, b=b, max_hops=6).get("path")
                if path:
                    found = True
                    self._remember(path)
                    facts += sum(self.edge_fact(e) for e in path.get("edges") or [])
        if ids_a and ids_b and not found:
            self.notes.append(_t("no_path", self.code) % (args["a"], args["b"]))
        return facts

    def common(self, args):
        facts = 0
        for a in self.resolve(args["a"])[:3]:
            for b in self.resolve(args["b"])[:3]:
                for item in self.query("common", a=a, b=b).get("common") or []:
                    if item.get("node"):
                        self.nodes.setdefault(item["node"]["id"], item["node"])
                    facts += self.edge_fact(item["edge_a"]) + self.edge_fact(item["edge_b"])
        return facts

    def timeline(self, args):
        facts = 0
        for eid in self.resolve(args["entity"]):
            items = self.query("timeline", id=eid, date_from=args.get("from"),
                               date_to=args.get("to")).get("timeline") or []
            for item in items:
                other = item.get("other") or {}
                if other:
                    self.nodes.setdefault(other["id"], other)
                edge = {"id": "", "a": eid, "b": other.get("id", ""), "type": item["type"],
                        "start": item.get("date"), "end": item.get("end"),
                        "evidence": item.get("evidence") or []}
                facts += self.edge_fact(edge)
        return facts

    def copresence(self, args):
        facts = 0
        word = _t("together", self.code)
        for eid in self.resolve(args["entity"]):
            items = self.query("copresence", id=eid, date_from=args.get("from"),
                               date_to=args.get("to")).get("copresence") or []
            for item in items:
                evidence = self._evidence({"evidence": item.get("evidence") or []})
                if not evidence:
                    continue
                span = item["from"] + (" - %s" % item["to"] if item.get("to")
                                       and item["to"] != item["from"] else "")
                text = "%s, %s %s %s (%s)." % (self._name(eid), item["other"]["label"], word,
                                                item["place"]["label"], span)
                for ev in evidence[:2]:
                    facts += int(self._add(text, ev))
        return facts

    def shared_identifiers(self, args):
        facts = 0
        params = {"limit": 20}
        if args.get("kind"):
            _, subtypes = self._label_maps()
            wanted = fold(args["kind"])
            # The planner names the kind in words; the graph wants the profile's ids.
            ids = [sid for sid, labels in subtypes.items()
                   if wanted in {fold(sid), *(fold(v) for v in labels.values())}]
            if ids:
                params["subtypes"] = ids
        for item in self.query("shared", **params).get("shared") or []:
            self.nodes.setdefault(item["identifier"]["id"], item["identifier"])
            for holder in item.get("holders") or []:
                self.nodes.setdefault(holder["node"]["id"], holder["node"])
                facts += sum(self.edge_fact(e) for e in holder.get("edges") or [])
        return facts

    def most_connected(self, args):
        kind = (args.get("kind") or "person").casefold()
        kind = kind if kind in ENTITY_TYPES else "person"
        ranking = self.query("ranking", types=[kind], limit=10).get("ranking") or []
        if ranking:
            lines = ["%d. %s -- %d" % (i, r["node"]["label"], r["degree"])
                     for i, r in enumerate(ranking, 1)]
            self.notes.append(_t("ranking", self.code) + "\n" + "\n".join(lines))
        return len(ranking)

    def run(self, calls):
        """Contexts for the plan's calls; raises Clarify on an ambiguous name."""
        for call in calls:
            noted = len(self.notes)
            facts = getattr(self, call["tool"])(call["args"])
            self.calls.append({"tool": call["tool"], "args": call["args"], "facts": facts})
            if not facts and len(self.notes) == noted:
                self.notes.append(_t("nothing", self.code) % call["tool"])
        notes = [{"text": n, "source_path": None, "title": "graph search", "corpus_id": None,
                  "page_number": None, "chunk_id": None, "score": 1.0, "origin": "graph-note"}
                 for n in self.notes]
        return notes + self.contexts

    def info(self):
        return {"calls": self.calls, "facts": len(self.contexts),
                "entities": sorted({n["label"] for n in self.nodes.values()
                                    if any(n["id"] in ids for ids in self._names.values())})}
