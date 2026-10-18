"""Graph facts for the chat (E26): who and what a question names, and how they connect.

A plain question that names people, organisations, places or identifiers the
graph knows gets their relations as extra context next to the RAG excerpts:
the path between the first two named entities, then their direct relations.
Each fact is one context in the RAG shape, carrying a verbatim quote and the
source document and page, so the answer cites it like any excerpt and the UI
opens the source the usual way. No model call is needed to find the names.

Only facts with evidence the caller may see are used; a fact whose documents
the adapter's policy hides is dropped (the graph's ACL copy may lag).
"""
import time

LANG_CODES = {"Hungarian": "hu", "English": "en", "Danish": "da", "German": "de"}
ENTITY_TYPES = ["person", "organization", "place", "identifier", "object"]


class GraphFacts:
    def __init__(self, client, max_facts=12, max_entities=4, profile_ttl=60.0):
        self._client = client  # a GraphClient, or a callable returning the current one
        self.max_facts = max_facts
        self.max_entities = max_entities
        self.profile_ttl = profile_ttl
        self._labels = (0.0, {})

    @property
    def client(self):
        return self._client() if callable(self._client) else self._client

    def _relation_labels(self):
        fetched, labels = self._labels
        if time.time() - fetched > self.profile_ttl:
            profile = self.client.post("/profile/get", {}).get("profile") or {}
            labels = {r["id"]: r.get("label") or {} for r in profile.get("relation_types") or []}
            self._labels = (time.time(), labels)
        return labels

    def contexts(self, question, scope_body, visible, lang=None):
        """(contexts, info) for one question; `visible(path)` is the adapter's check."""
        found = self.client.post("/query/mentioned", dict(
            scope_body, text=question[:4000], limit=self.max_entities, types=ENTITY_TYPES))
        entities = found.get("entities") or []
        if not entities:
            return [], {"entities": [], "facts": 0}
        documents = dict(found.get("documents") or {})
        nodes = {e["id"]: e for e in entities}
        ranked = []  # (priority, edge)

        def collect(res, priority):
            documents.update(res.get("documents") or {})
            for node in res.get("nodes") or []:
                if node:
                    nodes.setdefault(node["id"], node)
            for edge in res.get("edges") or []:
                ranked.append((priority, edge))

        ids = [e["id"] for e in entities]
        if len(ids) >= 2:
            res = self.client.post("/query/path", dict(scope_body, a=ids[0], b=ids[1],
                                                       max_hops=4))
            collect(res.get("path") or {}, 0)
            documents.update(res.get("documents") or {})
        for eid in ids[:2]:
            collect(self.client.post("/query/neighborhood", dict(
                scope_body, id=eid, hops=1, limit=40)), 1)

        labels = self._relation_labels()
        code = LANG_CODES.get(lang or "", "en")
        out, seen = [], set()
        for priority, edge in sorted(ranked, key=lambda x: (x[0], not x[1].get("start"))):
            if edge["id"] in seen or len(out) >= self.max_facts:
                continue
            seen.add(edge["id"])
            evidence = [ev for ev in edge.get("evidence") or []
                        if ev.get("doc_id") in documents
                        and visible(documents[ev["doc_id"]].get("source_path") or "")]
            if not evidence:
                continue
            ev = evidence[0]
            doc = documents[ev["doc_id"]]
            label = labels.get(edge["type"], {})
            name = label.get(code) or label.get("en") or edge["type"]
            if edge.get("raw_label"):
                name = "%s (%s)" % (name, edge["raw_label"])
            when = edge.get("start") or ""
            if edge.get("end") and edge["end"] != when:
                when = "%s - %s" % (when, edge["end"])
            a = (nodes.get(edge["a"]) or {}).get("label") or edge["a"]
            b = (nodes.get(edge["b"]) or {}).get("label") or edge["b"]
            text = "%s -- %s -- %s%s. \"%s\"" % (a, name, b, " (%s)" % when if when else "",
                                                ev.get("quote") or "")
            out.append({"text": text, "source_path": doc.get("source_path"),
                        "corpus_id": doc.get("corpus_id"), "page_number": ev.get("page"),
                        "chunk_id": None, "score": 1.0, "origin": "graph"})
        return out, {"entities": [e["label"] for e in entities], "facts": len(out)}
