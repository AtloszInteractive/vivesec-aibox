"""Orchestrator planning and plan execution against a fake graph."""
import unittest
from unittest.mock import patch

import llm
import orchestrator

DOC = {"d1": {"corpus_id": "c", "source_path": "/storage/drives/ops/a.txt", "title": "a",
              "kind": "txt"},
       "d2": {"corpus_id": "c", "source_path": "/storage/drives/ops/hr/secret.txt",
              "title": "s", "kind": "txt"}}


def node(nid, label, subtype="inmate", docs=1, aliases=()):
    return {"id": nid, "label": label, "type": "person", "subtype": subtype,
            "aliases": [label, *aliases], "documents": ["x"] * docs, "mentions": docs,
            "attributes": []}


def edge(eid, a, b, typ="VISITED", start=None, doc="d1", quote="q"):
    return {"id": eid, "a": a, "b": b, "type": typ, "start": start, "end": None,
            "raw_label": typ, "weak": False,
            "evidence": [{"doc_id": doc, "page": 1, "quote": quote}]}


KJ1 = node("p1", "Kovács János", docs=3)
KJ2 = node("p2", "Kovács János", docs=1)
KP = node("p3", "Kovács Péter")
NE = node("p4", "Nagy Éva", subtype="visitor")
TT = node("p5", "Titkos Tamás")
NODES = [KJ1, KJ2, KP, NE, TT]
EDGES = [edge("e1", "p4", "p1", start="2025-03-13", quote="Nagy Éva meglátogatta"),
         edge("e2", "p5", "p1", start="2025-04-01", doc="d2", quote="titkos"),
         edge("e3", "p4", "p2", start="2026-01-02", quote="második")]


class FakeGraph:
    def __init__(self):
        self.calls = []

    def post(self, path, body):
        self.calls.append((path, body))
        if path == "/profile/get":
            return {"profile": {
                "relation_types": [{"id": "VISITED", "label": {"en": "visited",
                                                               "hu": "meglátogatta"}}],
                "entity_subtypes": [{"id": "inmate", "label": {"en": "Inmate",
                                                               "hu": "Fogvatartott"}},
                                    {"id": "phone", "label": {"en": "Phone number",
                                                              "hu": "Telefonszám"}}]}}
        if path == "/query/find":
            text = orchestrator.fold(body["text"])
            hits = [n for n in NODES if all(any(w.startswith(t) for w in
                                                orchestrator.fold(n["label"]).split())
                                            for t in text.split())]
            return {"entities": hits, "documents": {}}
        if path == "/query/mentioned":
            return {"entities": [KJ1] if "kovacs janossal" in orchestrator.fold(body["text"])
                    else [], "documents": {}}
        if path == "/query/neighborhood":
            edges = [e for e in EDGES if body["id"] in (e["a"], e["b"])]
            return {"nodes": NODES, "edges": edges, "documents": DOC}
        if path == "/query/path":
            edges = [e for e in EDGES if {e["a"], e["b"]} == {body["a"], body["b"]}]
            return {"path": {"nodes": NODES, "edges": edges} if edges else None,
                    "documents": DOC}
        if path == "/query/timeline":
            items = [{"date": e["start"], "end": None, "type": e["type"],
                      "other": next(n for n in NODES if n["id"] == (e["a"] if e["b"] == body["id"]
                                                                  else e["b"])),
                      "evidence": e["evidence"]}
                     for e in EDGES if body["id"] in (e["a"], e["b"])]
            return {"timeline": items, "documents": DOC}
        if path == "/query/ranking":
            return {"ranking": [{"node": KJ1, "degree": 2}], "documents": {}}
        if path == "/query/shared":
            phone = {"id": "id:phone:1", "label": "+36 1", "type": "identifier",
                     "subtype": "phone", "aliases": [], "documents": [], "mentions": 1}
            uses = [edge("u%d" % i, n["id"], phone["id"], typ="USES") for i, n in
                    enumerate((KJ1, KJ2, NE))]
            return {"shared": [
                {"identifier": phone, "holders": [{"node": KJ1, "edges": [uses[0]]},
                                                  {"node": KJ2, "edges": [uses[1]]}]},
                {"identifier": dict(phone, id="id:phone:2", label="+36 2"),
                 "holders": [{"node": KJ1, "edges": [uses[0]]},
                             {"node": NE, "edges": [uses[2]]}]}], "documents": DOC}
        raise AssertionError(path)


def run(calls, lang="Hungarian"):
    graph = FakeGraph()
    r = orchestrator.Run(graph, {"corpus_ids": ["c"], "access": None},
                         lambda p: "/hr/" not in p, lang)
    return r, r.run(calls), graph


class PlanTest(unittest.TestCase):
    def test_parse_tools_clarify_chat_and_garbage(self):
        p = orchestrator.parse_plan('```json\n{"action": "tools", "calls": ['
                                    '{"tool": "connection", "args": {"a": "X", "b": "Y"}},'
                                    '{"tool": "connection", "args": {"a": "X"}},'
                                    '{"tool": "drop_graph", "args": {}},'
                                    '{"tool": "timeline", "args": {"entity": "X", '
                                    '"from": "2025-01-01", "to": "tomorrow"}}],'
                                    '"search": "X és Y"}\n```')
        self.assertEqual(["connection", "timeline"], [c["tool"] for c in p["calls"]])
        self.assertEqual({"entity": "X", "from": "2025-01-01"}, p["calls"][1]["args"])
        self.assertEqual("X és Y", p["search"])
        self.assertEqual("Melyik?", orchestrator.parse_plan(
            '{"action": "clarify", "question": "Melyik?"}')["question"])
        self.assertEqual("chat", orchestrator.parse_plan('{"action": "chat"}')["action"])
        self.assertIsNone(orchestrator.parse_plan("I think you should look at..."))
        self.assertIsNone(orchestrator.parse_plan('{"action": "clarify", "question": ""}'))

    def test_plan_fails_open(self):
        with patch.object(llm, "GENERATE", "on"), \
                patch.object(llm, "_chat", side_effect=OSError("down")):
            self.assertIsNone(orchestrator.plan("Ki?", []))
        with patch.object(llm, "GENERATE", "on"), \
                patch.object(llm, "_chat", return_value=('{"action": "chat"}', 0, 0)) as chat:
            orchestrator.plan("Foglald össze", [{"role": "user", "content": "előző"}],
                              "Hungarian")
            system, user = chat.call_args[0][:2]
            self.assertIn("Hungarian", system)
            self.assertIn("USER: előző", user)
            self.assertEqual("json", chat.call_args[1]["response_format"])


class RunTest(unittest.TestCase):
    def test_namesakes_are_kept_together_and_hidden_evidence_dropped(self):
        r, contexts, _ = run([{"tool": "relations", "args": {"entity": "Kovács János"}}])
        texts = [c["text"] for c in contexts]
        self.assertTrue(any("2 külön bejegyzés" in t for t in texts), texts)
        facts = [c for c in contexts if c["origin"] == "graph"]
        self.assertEqual(2, len(facts))
        self.assertTrue(all("Titkos" not in c["text"] for c in contexts))
        self.assertIn("meglátogatta", facts[0]["text"])
        self.assertEqual("/storage/drives/ops/a.txt", facts[0]["source_path"])
        self.assertEqual(["Kovács János"], r.info()["entities"])

    def test_partial_name_with_different_people_is_asked_back(self):
        with self.assertRaises(orchestrator.Clarify) as cm:
            run([{"tool": "relations", "args": {"entity": "Kovács"}}])
        self.assertIn("Melyikre gondol", cm.exception.question)
        self.assertIn("Kovács Péter (Fogvatartott, 1 dokumentum)", cm.exception.question)
        # The two Kovács János records are one option.
        self.assertEqual(1, cm.exception.question.count("Kovács János"))
        self.assertIn("Kovács János (Fogvatartott, 4 dokumentum)", cm.exception.question)

    def test_inflected_name_and_unknown_name(self):
        _, contexts, _ = run([{"tool": "timeline", "args": {"entity": "Kovács Jánossal"}},
                              {"tool": "relations", "args": {"entity": "Senki Sem"}}],
                             lang="English")
        texts = [c["text"] for c in contexts]
        self.assertTrue(any("2025-03-13" in t for t in texts), texts)
        self.assertTrue(any("nothing named" in t for t in texts), texts)

    def test_connection_and_missing_path(self):
        _, contexts, _ = run([{"tool": "connection", "args": {"a": "Nagy Éva",
                                                              "b": "Kovács Péter"}}])
        self.assertEqual(1, len(contexts))
        self.assertIn("nincs dokumentált kapcsolatlánc", contexts[0]["text"])
        _, contexts, _ = run([{"tool": "connection", "args": {"a": "Nagy Éva",
                                                              "b": "Kovács János"}}])
        self.assertEqual(2, sum(c["origin"] == "graph" for c in contexts))

    def test_ranking_note_and_fact_budget(self):
        _, contexts, _ = run([{"tool": "most_connected", "args": {"kind": "person"}}])
        self.assertIn("1. Kovács János -- 2", contexts[0]["text"])
        graph = FakeGraph()
        r = orchestrator.Run(graph, {"corpus_ids": ["c"]}, lambda p: True, "English",
                             max_facts=1)
        out = r.run([{"tool": "relations", "args": {"entity": "Nagy Éva"}}])
        self.assertEqual(1, sum(c["origin"] == "graph" for c in out))

    def test_shared_kind_is_mapped_to_profile_subtypes(self):
        r, contexts, graph = run([{"tool": "shared_identifiers",
                                   "args": {"kind": "telefonszám"}}])
        body = next(b for p, b in graph.calls if p == "/query/shared")
        self.assertEqual(["phone"], body["subtypes"])
        self.assertEqual(["c"], body["corpus_ids"])
        # Two records of one name are not two people sharing a number.
        self.assertEqual(2, len(contexts))
        self.assertTrue(all("+36 1" not in c["text"] for c in contexts))


if __name__ == "__main__":
    unittest.main()
