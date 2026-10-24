"""Orchestrator planning and plan execution against a fake graph."""
import json
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
    def __init__(self, pending=0):
        self.calls = []
        self.pending = pending

    def post(self, path, body):
        self.calls.append((path, body))
        if path == "/review/list":
            return {"review": [], "total": self.pending}
        if path == "/profile/get":
            return {"profile": {
                "relation_types": [{"id": "VISITED", "label": {"en": "visited",
                                                               "hu": "meglátogatta"},
                                    "from": ["person"], "to": ["person"],
                                    "synonyms": ["látogatás"]},
                                   {"id": "INVOLVED_IN", "label": {"en": "involved in"}}],
                "entity_subtypes": [{"id": "inmate", "type": "person",
                                     "label": {"en": "Inmate", "hu": "Fogvatartott"}},
                                    {"id": "phone", "type": "identifier",
                                     "label": {"en": "Phone number", "hu": "Telefonszám"}}]}}
        if path == "/query/match":
            paths = [{"nodes": [NE, KJ1], "edges": [EDGES[0]]},
                     {"nodes": [TT, KJ1], "edges": [EDGES[1]]}]
            return {"paths": paths, "documents": DOC}
        if path == "/query/aggregate":
            return {"rows": [{"key": "p4", "label": "Nagy Éva", "count": 2, "distinct": 2,
                              "nodes": [NE, KJ1, KJ2], "samples": [EDGES[0], EDGES[2]]}],
                    "documents": DOC}
        if path == "/query/sequence":
            return {"pairs": [{"focus": NE, "first": EDGES[0], "then": EDGES[2], "days": 295,
                               "nodes": [KJ1, KJ2]},
                              {"focus": KJ1, "first": EDGES[0], "then": EDGES[1], "days": 19,
                               "nodes": [NE, TT]}], "documents": DOC}
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

    def test_parse_pattern_count_sequence_and_rounds(self):
        p = orchestrator.parse_plan(
            '{"action": "tools", "more": true, "calls": ['
            '{"tool": "find_pattern", "args": {"start": {"kind": "visitor", "x": 1}, '
            '"steps": [{"relation": "VISITED", "kind": "inmate"}, "bad", {"name": "A12"}]}},'
            '{"tool": "count", "args": {"relation": "VISITED", "group_by": "weekday"}},'
            '{"tool": "sequence", "args": {"first": "VISITED", "then": "INVOLVED_IN", '
            '"within_days": "30"}},'
            '{"tool": "sequence", "args": {"first": "VISITED"}}]}')
        self.assertTrue(p["more"])
        self.assertEqual([
            {"tool": "find_pattern", "args": {
                "start": {"kind": "visitor"},
                "steps": [{"kind": "inmate", "relation": "VISITED"}, {"name": "A12"}]}},
            {"tool": "count", "args": {"relation": "VISITED", "group_by": "entity"}},
            {"tool": "sequence", "args": {"first": "VISITED", "then": "INVOLVED_IN",
                                          "within_days": 30}}], p["calls"])
        self.assertEqual("done", orchestrator.parse_plan('{"action": "done"}')["action"])
        self.assertFalse(orchestrator.parse_plan('{"action": "tools", "calls": []}')["more"])

    def test_vocabulary_lists_profile_kinds_and_relations(self):
        text = orchestrator.vocabulary(FakeGraph().post("/profile/get", {})["profile"],
                                       "Hungarian")
        self.assertIn("person (inmate = Fogvatartott)", text)
        self.assertIn("identifier (phone = Telefonszám)", text)
        self.assertIn("VISITED = meglátogatta (person -> person)", text)
        self.assertIn("INVOLVED_IN = involved in", text)
        with patch.object(llm, "GENERATE", "on"), \
                patch.object(llm, "_chat", return_value=('{"action": "done"}', 0, 0)) as chat:
            orchestrator.plan("Ki?", [], "Hungarian", text, "- relations: 2 results")
            system, user = chat.call_args[0][:2]
            self.assertIn("VISITED = meglátogatta", system)
            self.assertNotIn("{vocabulary}", system)
            self.assertIn("RESULTS SO FAR:\n- relations: 2 results", user)
            self.assertNotIn("{deep}", system)
            self.assertNotIn("read_all", system)  # not offered unless asked for
            orchestrator.plan("Ki?", [], "Hungarian", text, deep=True)
            self.assertIn("- read_all {terms, mode?}", chat.call_args[0][0])

    def test_read_all_terms_are_cleaned_and_the_graph_skips_them(self):
        plan = orchestrator.parse_plan(json.dumps({"action": "tools", "calls": [
            {"tool": "read_all", "args": {"terms": ["Kovács János", " ", "Kovács János",
                                                    "a" * 300] + ["t%d" % i for i in range(9)],
                                          "mode": "all"}},
            {"tool": "read_all", "args": {"terms": []}},
            {"tool": "read_all", "args": {"terms": "látogat"}},
            {"tool": "relations", "args": {"entity": "Kovács János"}}]}))
        reads = [c["args"] for c in plan["calls"] if c["tool"] == "read_all"]
        self.assertEqual(2, len(reads))
        self.assertEqual("all", reads[0]["mode"])
        self.assertEqual(orchestrator.MAX_READ_TERMS, len(reads[0]["terms"]))
        self.assertEqual(["Kovács János", "a" * 80], reads[0]["terms"][:2])
        self.assertEqual({"terms": ["látogat"], "mode": "any"}, reads[1])
        r = orchestrator.Run(FakeGraph(), {"corpus_ids": ["c"]}, lambda p: True)
        r.run(plan["calls"])
        self.assertEqual(["relations"], [c["tool"] for c in r.calls])

    def test_analyze_document_needs_a_file_and_the_graph_skips_it(self):
        plan = orchestrator.parse_plan(json.dumps({"action": "tools", "calls": [
            {"tool": "analyze_document", "args": {"file": "  Hazai-ICS-korkep.pdf "}},
            {"tool": "analyze_document", "args": {"file": ""}},
            {"tool": "analyze_document", "args": {}},
            {"tool": "relations", "args": {"entity": "Kovács János"}}]}))
        self.assertEqual([("analyze_document", {"file": "Hazai-ICS-korkep.pdf"}),
                          ("relations", {"entity": "Kovács János"})],
                         [(c["tool"], c["args"]) for c in plan["calls"]])
        r = orchestrator.Run(FakeGraph(), {"corpus_ids": ["c"]}, lambda p: True)
        r.run(plan["calls"])
        self.assertEqual(["relations"], [c["tool"] for c in r.calls])

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

    def test_inflected_partial_name_is_asked_back_with_matches(self):
        with self.assertRaises(orchestrator.Clarify) as cm:
            run([{"tool": "timeline", "args": {"entity": "Kovácsot"}}])
        self.assertIn("Kovács Péter", cm.exception.question)
        r = orchestrator.Run(FakeGraph(), {"corpus_ids": ["c"]}, lambda p: True, "Hungarian")
        self.assertIn("Kovács Péter", r.name_options("Mikor szállították át Kovácsot?"))
        self.assertIsNone(r.name_options("Mi a helyzet vele?"))
        self.assertIsNone(r.name_options("Mikor látogatták meg Nagy Évát?"))

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

    def test_find_pattern_maps_kinds_and_relation_synonyms(self):
        _, contexts, graph = run([{"tool": "find_pattern", "args": {
            "start": {"kind": "person"},
            "steps": [{"relation": "látogatás", "kind": "Fogvatartott"}]}}])
        body = next(b for p, b in graph.calls if p == "/query/match")
        self.assertEqual({"types": ["person"]}, body["start"])
        self.assertEqual([{"relations": ["VISITED"], "target": {"subtypes": ["inmate"]}}],
                         body["steps"])
        self.assertEqual(["c"], body["corpus_ids"])
        facts = [c for c in contexts if c["origin"] == "graph"]
        self.assertEqual(1, len(facts))  # the hidden document's link is dropped
        self.assertIn("Nagy Éva -- meglátogatta -- Kovács János", facts[0]["text"])

    def test_unknown_relation_or_kind_skips_the_query(self):
        _, contexts, graph = run([
            {"tool": "find_pattern", "args": {"steps": [{"relation": "BRIBED"}]}},
            {"tool": "count", "args": {"other_kind": "dinosaur", "group_by": "entity"}}])
        self.assertFalse([p for p, _ in graph.calls if p in ("/query/match",
                                                            "/query/aggregate")])
        texts = [c["text"] for c in contexts]
        self.assertTrue(any("\u201eBRIBED\u201d nem ismert" in t for t in texts), texts)
        self.assertTrue(any("\u201edinosaur\u201d nem ismert" in t for t in texts), texts)

    def test_count_note_and_sample_facts(self):
        _, contexts, graph = run([{"tool": "count", "args": {
            "relation": "VISITED", "other_kind": "inmate", "group_by": "entity"}}],
            lang="English")
        body = next(b for p, b in graph.calls if p == "/query/aggregate")
        self.assertEqual((["VISITED"], None, {"subtypes": ["inmate"]}, "focus"),
                         (body["relations"], body["focus"], body["other"], body["group_by"]))
        self.assertIn("visited by entity", contexts[0]["text"])
        self.assertIn("1. Nagy Éva -- Inmate: 2 different (2 links)", contexts[0]["text"])
        self.assertEqual(1, sum(c["origin"] == "graph" for c in contexts))

    def test_kind_given_as_a_name_filters_by_kind(self):
        _, _, graph = run([{"tool": "count", "args": {
            "name": "Fogvatartott", "kind": "person", "relation": "VISITED",
            "group_by": "entity"}}])
        body = next(b for p, b in graph.calls if p == "/query/aggregate")
        self.assertEqual({"subtypes": ["inmate"]}, body["focus"])
        self.assertFalse([b for p, b in graph.calls if p == "/query/find"])

    def test_pending_namesake_merges_are_noted_once(self):
        graph = FakeGraph(pending=3)
        r = orchestrator.Run(graph, {"corpus_ids": ["c"]}, lambda p: True, "English")
        out = r.run([{"tool": "count", "args": {"relation": "VISITED", "group_by": "entity"}},
                     {"tool": "count", "args": {"relation": "VISITED", "group_by": "other"}}])
        notes = [c["text"] for c in out if "await an administrator" in c["text"]]
        self.assertEqual(1, len(notes))
        self.assertIn("3 pairs", notes[0])
        self.assertEqual([{"corpus_ids": ["c"], "limit": 1}],
                         [b for p, b in graph.calls if p == "/review/list"])
        # Over time the split records do not matter; nothing pending, nothing said.
        _, contexts, graph = run([{"tool": "count", "args": {"relation": "VISITED",
                                                             "group_by": "month"}}])
        self.assertFalse([p for p, _ in graph.calls if p == "/review/list"])
        _, contexts, graph = run([{"tool": "count", "args": {"relation": "VISITED",
                                                             "group_by": "entity"}}])
        self.assertTrue([p for p, _ in graph.calls if p == "/review/list"])
        self.assertFalse(any("adminisztr" in c["text"] for c in contexts))

    def test_empty_pattern_names_the_pending_merges_as_its_reason(self):
        class NoMatch(FakeGraph):
            def post(self, path, body):
                if path in ("/query/match", "/query/sequence"):
                    self.calls.append((path, body))
                    return {"paths": [], "pairs": [], "documents": {}}
                return super().post(path, body)

        graph = NoMatch(pending=5)
        r = orchestrator.Run(graph, {"corpus_ids": ["c"]}, lambda p: True, "English")
        out = r.run([{"tool": "find_pattern", "args": {"steps": [{"relation": "VISITED"}]}},
                     {"tool": "sequence", "args": {"first": "VISITED", "then": "INVOLVED_IN"}}])
        notes = [c["text"] for c in out if c["origin"] == "graph-note"]
        self.assertEqual(2, len(notes))
        self.assertIn("(find_pattern): no result", notes[0])
        self.assertIn("5 pairs of same-named records", notes[0])
        self.assertNotIn("same-named", notes[1])  # said once
        self.assertEqual(1, sum(p == "/review/list" for p, _ in graph.calls))
        self.assertIn("5 pairs of same-named records", r.info()["caveat"])
        self.assertNotIn("caveat", orchestrator.Run(
            FakeGraph(pending=5), {"corpus_ids": ["c"]}, lambda p: True).info())

    def test_name_join_lists_who_was_joined_and_why_a_result_is_empty(self):
        class Joined(FakeGraph):
            empty = False

            def post(self, path, body):
                if path == "/query/match":
                    self.calls.append((path, body))
                    if self.empty:
                        return {"paths": [], "namesakes": {"ambiguous": 3}, "documents": {}}
                    return {"paths": [{"nodes": [NE, dict(KJ1, joined=["a", "b", "c"])],
                                       "edges": [EDGES[0]]}],
                            "namesakes": {"ambiguous": 3}, "documents": DOC}
                return super().post(path, body)

        graph = Joined(pending=7)
        r = orchestrator.Run(graph, {"corpus_ids": ["c"]}, lambda p: True, "English",
                             name_join=True)
        call = [{"tool": "find_pattern", "args": {"steps": [{"relation": "VISITED"}]}}]
        out = r.run(call)
        body = next(b for p, b in graph.calls if p == "/query/match")
        self.assertIs(True, body["join_namesakes"])
        joined = [c["text"] for c in out if c["text"].startswith("Linked by name")]
        self.assertEqual(["Linked by name: the records of these people from different documents"
                          " were joined only by the same name; no birth date or ID number "
                          "confirms it: Kovács János (3)."], joined)
        self.assertIn("Kovács János (3)", r.info()["caveat"])
        # The merge queue says nothing about joined records.
        self.assertFalse([p for p, _ in graph.calls if p == "/review/list"])
        graph = Joined()
        graph.empty = True
        r = orchestrator.Run(graph, {"corpus_ids": ["c"]}, lambda p: True, "English",
                             name_join=True)
        r.run(call)
        self.assertIn("3 names in the documents you may see", r.info()["caveat"])
        # Off: the graph is asked plainly.
        graph = Joined()
        orchestrator.Run(graph, {"corpus_ids": ["c"]}, lambda p: True).run(call)
        self.assertNotIn("join_namesakes", next(b for p, b in graph.calls
                                                if p == "/query/match"))

    def test_name_join_is_reported_only_where_it_changes_the_answer(self):
        joined_kj = dict(KJ1, joined=["a", "b"])

        class Rows(FakeGraph):
            row_node = None

            def post(self, path, body):
                if path == "/query/aggregate":
                    self.calls.append((path, body))
                    return {"rows": [{"key": "k", "label": "Kovács János", "count": 2,
                                      "distinct": 1, "node": self.row_node,
                                      "nodes": [NE, joined_kj], "samples": [EDGES[0]]}],
                            "documents": DOC}
                return super().post(path, body)

        def caveat(group, row_node=None):
            graph = Rows()
            graph.row_node = row_node
            r = orchestrator.Run(graph, {"corpus_ids": ["c"]}, lambda p: True, "English",
                                 name_join=True)
            r.run([{"tool": "count", "args": {"relation": "VISITED", "group_by": group}}])
            return r.info().get("caveat") or ""

        self.assertEqual("", caveat("month"))  # a link counts once, joined or not
        self.assertIn("Kovács János (2)", caveat("entity", joined_kj))
        self.assertIn("the counts treat records of the same name", caveat("entity", NE))

    def test_sequence_lists_only_visible_pairs(self):
        _, contexts, _ = run([{"tool": "sequence", "args": {
            "first": "VISITED", "then": "VISITED", "within_days": 300}}])
        note = contexts[0]["text"]
        self.assertIn("meglátogatta után meglátogatta, 300 napon belül", note)
        self.assertIn("- Nagy Éva: meglátogatta Kovács János (2025-03-13) -> "
                      "meglátogatta Kovács János (2026-01-02), 295 nappal később", note)
        self.assertTrue(all("Titkos" not in c["text"] for c in contexts))

    def test_orchestrate_replans_with_results(self):
        graph = FakeGraph()
        r = orchestrator.Run(graph, {"corpus_ids": ["c"]}, lambda p: True, "English")
        first = {"action": "tools", "more": True, "search": "",
                 "calls": [{"tool": "most_connected", "args": {"kind": "person"}}]}
        seen = []

        def replan(results):
            seen.append(results)
            return {"action": "tools", "more": True, "search": "",
                    "calls": [{"tool": "timeline", "args": {"entity": "Nagy Éva"}}]}

        contexts, info = orchestrator.orchestrate(r, first, replan, max_rounds=2)
        self.assertEqual(1, len(seen))  # the last round does not replan
        self.assertIn("most_connected", seen[0])
        self.assertIn("Kovács János", seen[0])
        self.assertEqual([1, 2], [c["round"] for c in info["calls"]])
        self.assertTrue(any("2025-03-13" in c["text"] for c in contexts))
        self.assertEqual("max_rounds", info["stop"])
        r2 = orchestrator.Run(FakeGraph(), {"corpus_ids": ["c"]}, lambda p: True, "English")
        _, info = orchestrator.orchestrate(r2, first, lambda results: {"action": "done"})
        self.assertEqual(1, len(info["calls"]))
        self.assertEqual("done", info["stop"])

    def test_repeated_calls_end_the_rounds_and_rankings_reach_the_planner(self):
        r = orchestrator.Run(FakeGraph(), {"corpus_ids": ["c"]}, lambda p: True, "English")
        first = {"action": "tools", "more": True, "search": "",
                 "calls": [{"tool": "count", "args": {"relation": "VISITED",
                                                      "group_by": "entity"}}]}
        seen = []

        def replan(results):
            seen.append(results)
            return first

        _, info = orchestrator.orchestrate(r, first, replan, max_rounds=3)
        self.assertEqual(1, len(seen))
        self.assertEqual(1, len(info["calls"]))
        self.assertEqual("repeat", info["stop"])
        self.assertIn("1. Nagy Éva -- 2 links", seen[0])


if __name__ == "__main__":
    unittest.main()
