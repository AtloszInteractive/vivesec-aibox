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
                      "a": e["a"], "b": e["b"],
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
        self.assertIn("VISITED = meglátogatta (person -> person) [also: látogatás]", text)
        self.assertIn("INVOLVED_IN = involved in", text)
        self.assertNotIn("INVOLVED_IN = involved in [", text)
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


class StatedRelationTest(unittest.TestCase):
    """A generic relation the planner chose gives way to the kind of link the
    message names; the vocabulary lists the generic ones last, marked."""

    PROFILE = {"relation_types": [
        {"id": "RELATED_TO", "label": {"hu": "Kapcsolatban áll"},
         "from": orchestrator.ENTITY_TYPES, "to": orchestrator.ENTITY_TYPES},
        {"id": "RELATIVE_OF", "label": {"hu": "Rokona"}, "from": ["person"], "to": ["person"],
         "synonyms": ["testvére", "kapcsolattartó"]},
        {"id": "INVOLVED_IN", "label": {"hu": "Érintett"}, "from": ["person"], "to": ["event"]},
        {"id": "PART_OF", "label": {"hu": "Része"}, "from": ["place"], "to": ["place"]}],
        "entity_subtypes": [{"id": "inmate", "type": "person", "label": {"hu": "Fogvatartott"}}]}

    def run_for(self, message):
        graph = FakeGraph()
        return orchestrator.Run(graph, {"corpus_ids": ["c"]}, lambda p: True, "Hungarian",
                                prof=self.PROFILE, message=message), graph

    def test_the_named_kind_replaces_a_generic_relation(self):
        r, _ = self.run_for("Kik Csonka Tibor engedélyezett kapcsolattartói?")
        self.assertEqual(["RELATIVE_OF"], r.relation_ids("RELATED_TO"))
        self.assertEqual(["INVOLVED_IN"], r.relation_ids("INVOLVED_IN"))  # specific: kept
        r, _ = self.run_for("Mi köti össze Csonka Tibort és Kardos Gyulát?")
        self.assertEqual(["RELATED_TO"], r.relation_ids("RELATED_TO"))

    def test_the_ends_of_a_step_pick_between_several_named_kinds(self):
        r, _ = self.run_for("A vele közös eseményben érintett fogvatartottak kapcsolattartói")
        self.assertEqual(["RELATED_TO"], r.relation_ids("RELATED_TO"))  # two fit: kept
        self.assertEqual(["INVOLVED_IN"], r.relation_ids("RELATED_TO", (["event"], ["person"])))
        self.assertEqual(["RELATIVE_OF"], r.relation_ids("RELATED_TO", (["person"], ["person"])))
        # An unknown previous side: both still fit, so nothing is guessed.
        self.assertEqual(["RELATED_TO"], r.relation_ids("RELATED_TO", (None, ["person"])))

    def test_a_pattern_step_is_sent_with_the_named_relation(self):
        r, graph = self.run_for("Csonka Tibor kapcsolattartói mely fogvatartottaknak "
                                "kapcsolattartói még?")
        r.run([{"tool": "find_pattern", "args": {
            "start": {"kind": "inmate"},
            "steps": [{"relation": "RELATED_TO", "kind": "person"},
                      {"relation": "RELATED_TO", "kind": "Fogvatartott"}]}}])
        body = next(b for p, b in graph.calls if p == "/query/match")
        self.assertEqual([["RELATIVE_OF"], ["RELATIVE_OF"]],
                         [s["relations"] for s in body["steps"]])

    def test_a_fact_carries_the_users_word_for_its_link(self):
        r, _ = self.run_for("Kik Nagy Éva kapcsolattartói?")
        r.nodes.update({"p4": NE, "p1": KJ1})
        r.documents.update(DOC)
        r.edge_fact(edge("x", "p1", "p4", typ="RELATIVE_OF", quote="unokatestvére"))
        self.assertTrue(r.contexts[0]["text"].startswith(
            "Kovács János -- Rokona / kapcsolattartó -- Nagy Éva."), r.contexts[0]["text"])
        r, _ = self.run_for("Ki Nagy Éva rokona?")  # the label itself: nothing added
        r.nodes.update({"p4": NE, "p1": KJ1})
        r.documents.update(DOC)
        r.edge_fact(edge("x", "p1", "p4", typ="RELATIVE_OF"))
        self.assertTrue(r.contexts[0]["text"].startswith("Kovács János -- Rokona -- Nagy Éva."))

    def test_a_pattern_touching_no_one_named_is_skipped_quietly(self):
        r, graph = self.run_for("Kik Nagy Éva kapcsolattartói?")
        r.run([{"tool": "relations", "args": {"entity": "Nagy Éva"}},
               {"tool": "find_pattern", "args": {
                   "start": {"kind": "person"},
                   "steps": [{"relation": "RELATIVE_OF", "kind": "inmate"}]}}])
        self.assertFalse(any(p == "/query/match" for p, _ in graph.calls))
        self.assertTrue(r.info()["calls"][1]["skipped"])
        self.assertFalse(any("Gráfkeresés (find_pattern)" in n for n in r.notes))
        # Without a named entity the same pattern runs.
        r, graph = self.run_for("Kik a fogvatartottak kapcsolattartói?")
        r.run([{"tool": "find_pattern", "args": {
            "start": {"kind": "person"}, "steps": [{"relation": "RELATIVE_OF"}]}}])
        self.assertTrue(any(p == "/query/match" for p, _ in graph.calls))

    def test_vocabulary_lists_generic_relations_last_and_marked(self):
        text = orchestrator.vocabulary(self.PROFILE, "Hungarian")
        self.assertLess(text.index("RELATIVE_OF"), text.index("RELATED_TO"))
        self.assertIn("RELATED_TO = Kapcsolatban áll (person/", text)
        self.assertIn("-- generic:", text)
        self.assertIn("[also: testvére, kapcsolattartó]", text)


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

    def test_an_identifier_that_is_not_there_is_not_taken_for_a_name(self):
        # Its first letters fit "Kovács ...": no question back about those people.
        _, contexts, _ = run([{"tool": "relations",
                               "args": {"entity": "kovacs.janos@example.hu"}}])
        texts = [c["text"] for c in contexts]
        self.assertEqual(1, len(texts), texts)
        self.assertIn("kovacs.janos@example.hu", texts[0])
        self.assertNotIn("Kovács", texts[0])

    def test_identity_data_goes_with_the_people_found(self):
        attrs = {"p4": [("mother_name", "Anya Anna", "d1"), ("id_number", "123456AB", "d1"),
                        ("birth_date", "1990-01-01", "d2")],
                 "p1": [("registry_number", "NY-100001", "d1"),
                        ("mother_name", "Titok Teréz", "d1")]}

        class Graph(FakeGraph):
            def post(self, path, body):
                out = json.loads(json.dumps(super().post(path, body)))
                for n in out.get("nodes") or out.get("entities") or []:
                    n["attributes"] = [{"name": k, "value": v, "doc_id": d, "page": 2}
                                       for k, v, d in attrs.get(n["id"], [])]
                return out

        r = orchestrator.Run(Graph(), {"corpus_ids": ["c"], "access": None},
                             lambda p: "/hr/" not in p, "Hungarian")
        contexts = r.run([{"tool": "relations", "args": {"entity": "Nagy Éva"}}])
        identity = [c for c in contexts if c["origin"] == "graph" and c["text"].count(" -- ") == 1]
        # The person asked about: every identifying number and personal data the
        # caller may see; the birth date stands in a hidden document only.
        self.assertEqual("Nagy Éva -- Azonosító szám: 123456AB; Anyja neve: Anya Anna.",
                         identity[0]["text"])
        self.assertEqual(("/storage/drives/ops/a.txt", 2),
                         (identity[0]["source_path"], identity[0]["page_number"]))
        # A person the facts reach: the numbers only, not the mother's name.
        self.assertEqual("Kovács János -- Nyilvántartási szám: NY-100001.", identity[1]["text"])
        self.assertTrue(all("1990" not in c["text"] and "Titok" not in c["text"]
                            for c in contexts))
        self.assertEqual(2, len(identity))
        self.assertNotIn("NY-100001", "\n".join(c["text"] for c in contexts
                                                if c not in identity))
        # A later round looking into a person found: still not asked about by the user.
        contexts = r.run([{"tool": "relations", "args": {"entity": "Kovács János"}}], 2)
        self.assertTrue(all("Titok" not in c["text"] for c in contexts))
        self.assertIn("Kovács János -- Nyilvántartási szám: NY-100001.",
                      [c["text"] for c in contexts])

    def test_an_identity_fact_cites_the_page_of_its_own_values(self):
        attrs = [("id_number", "123456AB", "d1", 1), ("birth_date", "1990-01-01", "d1", 1),
                 ("id_number", "123456AB", "d3", 1), ("mother_name", "Anya Anna", "d3", 3)]
        docs = dict(DOC, d3={"corpus_id": "c", "source_path": "/storage/drives/ops/b.txt",
                             "title": "b", "kind": "txt"})

        class Graph(FakeGraph):
            def post(self, path, body):
                out = json.loads(json.dumps(super().post(path, body)))
                for n in out.get("nodes") or out.get("entities") or []:
                    if n["id"] == "p4":
                        n["attributes"] = [{"name": k, "value": v, "doc_id": d, "page": p}
                                           for k, v, d, p in attrs]
                if "documents" in out:
                    out["documents"] = docs
                return out

        r = orchestrator.Run(Graph(), {"corpus_ids": ["c"], "access": None},
                             lambda p: True, "Hungarian")
        contexts = r.run([{"tool": "relations", "args": {"entity": "Nagy Éva"}}])
        identity = {c["text"]: c for c in contexts if c["text"].startswith("Nagy Éva -- ")
                    and c["text"].count(" -- ") == 1}
        mother = identity["Nagy Éva -- Anyja neve: Anya Anna."]
        self.assertEqual(("/storage/drives/ops/b.txt", 3),
                         (mother["source_path"], mother["page_number"]))

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

    def test_kind_given_as_an_entity_name_is_skipped_quietly(self):
        # "common: Kovács, cell" once put "nothing named \u201ccell\u201d" into the answer.
        r, contexts, graph = run([{"tool": "common", "args": {"a": "Kovács Péter",
                                                              "b": "inmate"}}],
                                 lang="English")
        self.assertEqual([], contexts)
        self.assertEqual(["Kovács Péter"], [b["text"] for p, b in graph.calls
                                            if p == "/query/find"])
        self.assertEqual(0, r.calls[0]["facts"])

    def test_option_lines_name_entity_types_in_the_users_language(self):
        r = orchestrator.Run(FakeGraph(), {"corpus_ids": ["c"]}, lambda p: True, "Hungarian")
        plain = dict(KP, subtype="", label="Kovács M.")
        self.assertEqual("Kovács M. (Személy, 1 dokumentum)", r.describe([plain]))
        self.assertEqual("Kovács Péter (Fogvatartott, 1 dokumentum)", r.describe([KP]))

    def test_timeline_keeps_the_direction_of_a_link(self):
        # Kovács János's timeline: he was visited, he did not visit.
        _, contexts, _ = run([{"tool": "timeline", "args": {"entity": "Kovács János"}}])
        facts = [c["text"] for c in contexts if c["origin"] == "graph"]
        self.assertTrue(facts)
        self.assertTrue(all(f.startswith("Nagy Éva -- meglátogatta -- Kovács János")
                            for f in facts), facts)

        class OldService(FakeGraph):
            def post(self, path, body):
                res = super().post(path, body)
                for item in res.get("timeline") or []:
                    item.pop("a"), item.pop("b")
                return res

        r = orchestrator.Run(OldService(), {"corpus_ids": ["c"]}, lambda p: True, "Hungarian")
        out = r.run([{"tool": "timeline", "args": {"entity": "Nagy Éva"}}])
        self.assertTrue(any(c["text"].startswith("Nagy Éva -- meglátogatta") for c in out))

    def test_repeated_links_are_counted_by_the_code(self):
        visits = [edge("t1", "p4", "p3", start="2025-03-01"),
                  edge("t2", "p4", "p3", start="2025-04-01", quote="masodik"),
                  edge("t3", "p4", "p3", start="2025-05-02"),
                  edge("t4", "p4", "p3", start="2025-05"),  # the same, less precise
                  edge("t5", "p4", "p3", start="2025-06-01", doc="d2"),  # hidden
                  edge("t6", "p4", "p3"),  # undated
                  edge("t8", "p3", "p4", typ="RELATED_TO", start="2025-03-01"),
                  edge("t9", "p3", "p4", typ="RELATED_TO", start="2025-04-01"),
                  edge("t7", "p3", "p1", start="2025-07-01")]  # once only

        class Visits(FakeGraph):
            def post(self, path, body):
                if path == "/query/neighborhood":
                    self.calls.append((path, body))
                    return {"nodes": NODES, "documents": DOC,
                            "edges": [e for e in visits if body["id"] in (e["a"], e["b"])]}
                return super().post(path, body)

        # The facts cap does not cap the count.
        r = orchestrator.Run(Visits(), {"corpus_ids": ["c"]}, lambda p: "/hr/" not in p,
                             "Hungarian", max_facts=1)
        out = r.run([{"tool": "relations", "args": {"entity": "Kovács Péter"}}])
        self.assertEqual(1, sum(c["origin"] == "graph" for c in out))
        notes = [c["text"] for c in out if c["text"].startswith("Gráf-számlálás -- a kérdezett")]
        self.assertEqual(1, len(notes), [c["text"] for c in out])
        self.assertEqual(["- Nagy Éva -- meglátogatta -- Kovács Péter: 3 alkalommal "
                          "(2025-03-01 és 2025-05-02 között)"], notes[0].split("\n")[1:])
        # Links of entities nobody asked about are not tallied.
        r = orchestrator.Run(Visits(), {"corpus_ids": ["c"]}, lambda p: True, "English")
        out = r.run([{"tool": "most_connected", "args": {"kind": "person"}}])
        self.assertFalse(any(c["text"].startswith("Graph count -- repeated") for c in out))
        # Namesakes' links are counted together, as their facts are listed together.
        _, out, _ = run([{"tool": "relations", "args": {"entity": "Kovács János"}}],
                        lang="English")
        notes = [c["text"] for c in out if c["text"].startswith("Graph count -- repeated")]
        self.assertEqual(["- Nagy Éva -- visited -- Kovács János: 2 times "
                          "(2025-03-13 to 2026-01-02)"], notes[0].split("\n")[1:])

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

    def test_same_named_places_are_told_apart_in_a_note(self):
        cells = [("c1", "102. számú zárka (Kőhegyi Intézet)", 5),
                 ("c2", "102. számú zárka (Lista.xlsx)", 6), ("c3", "Kovács János (x)", 1)]

        class Cells(FakeGraph):
            def post(self, path, body):
                if path == "/query/aggregate":
                    self.calls.append((path, body))
                    return {"rows": [{"key": k, "label": label, "count": n, "distinct": n,
                                      "node": None, "nodes": [], "samples": []}
                                     for k, label, n in cells], "documents": DOC}
                return super().post(path, body)

        def notes(rows):
            cells[:] = rows
            r = orchestrator.Run(Cells(), {"corpus_ids": ["c"]}, lambda p: True, "English")
            out = r.run([{"tool": "count", "args": {"relation": "VISITED",
                                                     "group_by": "other"}}])
            return [c["text"] for c in out if c["origin"] == "graph-note"]

        texts = notes(list(cells))
        self.assertTrue(any(t.startswith("Same name, different place: 102. számú zárka --")
                            for t in texts), texts)
        self.assertFalse(any("Kovács János" in t and t.startswith("Same name") for t in texts))
        # One qualified variant alone needs no warning.
        self.assertFalse(any(t.startswith("Same name") for t in notes(
            [("c1", "102. számú zárka (Kőhegyi Intézet)", 5)])))

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


def person(nid, label, subtype="", attrs=()):
    return {"id": nid, "label": label, "type": "person", "subtype": subtype,
            "aliases": [label], "documents": ["d1"], "mentions": 1,
            "attributes": [{"name": n, "value": v, "doc_id": d, "page": 2} for n, v, d in attrs]}


def thing(nid, label, typ, subtype):
    return {"id": nid, "label": label, "type": typ, "subtype": subtype, "aliases": [label],
            "documents": ["d1"], "mentions": 1, "attributes": []}


class InvestigationGraph:
    """Sas Tibor's investigation as the graph answers it. d2 is in a folder the
    adapter's policy hides (the graph's ACL copy lags): Kiss Bori, reached only
    through it, must vanish together with Illés Dénes reached only through her."""
    S = person("S", "Sas Tibor", "inmate", [("registry_number", "NY-100", "d1"),
                                            ("registry_number", "NY-999", "d2")])
    K1 = person("K1", "Kende Anna")
    K2 = person("K2", "Kiss Bori")
    I3 = person("I3", "Izsó Csaba", "inmate", [("registry_number", "NY-300", "d1")])
    I4 = person("I4", "Illés Dénes", "inmate")
    M = person("M", "Major Ede", "inmate")
    E = thing("E", "verekedés (2025-03-01)", "event", "incident")
    P = thing("P", "+36 30 111 1111", "identifier", "phone")
    A = thing("A", "Fő utca 5.", "place", "address")
    MAIL = thing("X", "kende@example.hu", "identifier", "email")

    def __init__(self, truncated=False):
        self.calls = []
        self.truncated = truncated

    def post(self, path, body):
        self.calls.append((path, body))
        if path == "/profile/get":
            return {"profile": {
                "relation_types": [
                    {"id": "RELATIVE_OF", "label": {"en": "Relative of", "hu": "Rokona"},
                     "synonyms": ["kapcsolattartó"]},
                    {"id": "INVOLVED_IN", "label": {"en": "Involved in", "hu": "Érintett"}},
                    {"id": "USES", "label": {"en": "Uses", "hu": "Használja"}},
                    {"id": "RELATED_TO", "label": {"en": "Related to"}}],
                "entity_subtypes": [{"id": "inmate", "type": "person",
                                     "label": {"en": "Inmate", "hu": "Fogvatartott"}}]}}
        if path == "/query/find":
            return {"entities": [self.S] if "sas" in orchestrator.fold(body["text"]) else [],
                    "documents": {}}
        if path == "/review/list":
            return {"review": [], "total": 0}
        if path == "/query/investigate":
            def via(frm, eid, typ="RELATIVE_OF", doc="d1", through=None):
                link = {"from": frm, "edge": edge(eid, frm, "?", typ, doc=doc)}
                if through:
                    link.update(through=through, edge2=edge(eid + "b", "?", through["id"],
                                                            typ, doc=doc))
                return link
            rows = [
                {"level": 0, "node": self.S, "via": [], "held": []},
                {"level": 1, "node": self.K1, "via": [via("S", "r1")], "held": [
                    {"node": self.P, "edge": edge("u1", "K1", "P", "USES")},
                    {"node": self.A, "edge": edge("h1", "K1", "A", "RESIDES_AT")},
                    {"node": self.MAIL, "edge": edge("m1", "K1", "X", "USES", doc="d2")}]},
                {"level": 1, "node": self.K2, "via": [via("S", "r2", doc="d2")], "held": []},
                {"level": 1, "node": self.M, "via": [via("S", "x1", "INVOLVED_IN",
                                                         through=self.E)], "held": []},
                {"level": 2, "node": self.I3, "via": [via("K2", "r5"), via("K1", "r4")],
                 "held": []},
                {"level": 2, "node": self.I4, "via": [via("K2", "r7")], "held": []}]
            return {"rows": rows, "truncated": self.truncated, "levels": 2,
                    "documents": DOC}
        raise AssertionError(path)


class InvestigateTest(unittest.TestCase):
    CONFIG = {"relations": ["RELATIVE_OF"], "via": ["INVOLVED_IN"], "branch": 10,
              "max_rows": 80}

    def run_investigation(self, graph=None, message="Sas Tibor kapcsolattartói", **kw):
        graph = graph or InvestigationGraph()
        r = orchestrator.Run(graph, {"corpus_ids": ["c"], "access": None},
                             lambda p: "/hr/" not in p, "Hungarian",
                             message=message, investigate=kw.pop("config", self.CONFIG), **kw)
        out = r.run([{"tool": "investigate", "args": {"start": "Sas Tibor", "levels": 2}}])
        return r, out, graph

    def test_one_row_per_person_reached_with_role_identifiers_and_path(self):
        r, out, graph = self.run_investigation()
        texts = [c["text"] for c in out]
        rows = [c for c in out if c["text"].startswith("Vizsgálat,")]
        self.assertEqual(["Sas Tibor", "Kende Anna", "Major Ede", "Izsó Csaba"],
                         [c["text"].split(": ", 1)[1].split(" -- ")[0] for c in rows])
        self.assertEqual(
            "Vizsgálat, 1. szint: Kende Anna -- szerep: Személy; telefon: +36 30 111 1111; "
            "lakcím: Fő utca 5. -- kapcsolódás útja: Sas Tibor \u2014[Rokona / "
            "kapcsolattartó]\u2014 Kende Anna.", rows[1]["text"])
        self.assertIn("szerep: Fogvatartott; Nyilvántartási szám: NY-100 -- kapcsolódás "
                      "útja: kiindulópont.", rows[0]["text"])
        self.assertIn("Sas Tibor \u2014[Érintett: verekedés (2025-03-01)]\u2014 Major Ede",
                      rows[2]["text"])
        self.assertIn("Nyilvántartási szám: NY-300", rows[3]["text"])
        # Each row is a citable fact of the document that states its link.
        self.assertEqual(("/storage/drives/ops/a.txt", "graph"),
                         (rows[1]["source_path"], rows[1]["origin"]))
        # The header says what the table covers, ahead of the rows.
        header = next(c for c in out if c["origin"] == "graph-note")
        self.assertLess(out.index(header), out.index(rows[0]))
        self.assertIn("kiindulópont: Sas Tibor; 4 személy, 2 szint", header["text"])
        self.assertIn("Rokona / kapcsolattartó, Érintett (ugyanahhoz", header["text"])
        self.assertIn("a táblázat teljes", header["text"])
        body = graph.calls[-1][1] if graph.calls[-1][0] == "/query/investigate" else \
            next(b for p, b in graph.calls if p == "/query/investigate")
        self.assertEqual((["S"], ["RELATIVE_OF"], ["INVOLVED_IN"], 2, 10, 80),
                         (body["seeds"], body["relations"], body["via_relations"],
                          body["levels"], body["branch"], body["limit"]))
        self.assertNotIn("join_namesakes", body)
        info = r.info()["investigations"][0]
        self.assertEqual(["+36 30 111 1111"], info["rows"][1]["phone"])
        self.assertEqual([0, 1, 1, 2], [row["level"] for row in info["rows"]])
        self.assertTrue(any("NY-100" in t for t in texts))

    def test_what_the_adapter_hides_is_dropped_with_all_reached_through_it(self):
        _, out, _ = self.run_investigation()
        text = "\n".join(c["text"] for c in out)
        for hidden in ("Kiss Bori", "Illés Dénes", "kende@example.hu", "NY-999"):
            self.assertNotIn(hidden, text)
        # Izsó Csaba stays: Kende Anna links him too.
        self.assertIn("Sas Tibor \u2014[Rokona / kapcsolattartó]\u2014 Kende Anna \u2014[Rokona "
                      "/ kapcsolattartó]\u2014 Izsó Csaba", text)
        self.assertTrue(all(c["source_path"] is None or "/hr/" not in c["source_path"]
                            for c in out))

    def test_a_cut_table_says_so_and_namesakes_are_joined_on_request(self):
        r, out, graph = self.run_investigation(InvestigationGraph(truncated=True),
                                               name_join=True)
        header = next(c for c in out if c["origin"] == "graph-note")
        self.assertIn("csonkolva", header["text"])
        self.assertIn("legfeljebb 10 új személy, összesen 80 sor", header["text"])
        body = next(b for p, b in graph.calls if p == "/query/investigate")
        self.assertTrue(body["join_namesakes"])

    def test_an_investigation_ends_the_planning_rounds(self):
        r = orchestrator.Run(InvestigationGraph(), {"corpus_ids": ["c"]}, lambda p: True,
                             "Hungarian", investigate=self.CONFIG)
        first = {"action": "tools", "more": True, "search": "", "calls": [
            {"tool": "investigate", "args": {"start": "Sas Tibor", "levels": 3}}]}
        replanned = []
        _, info = orchestrator.orchestrate(r, first, lambda results: replanned.append(1))
        self.assertEqual(([], "investigated"), (replanned, info["stop"]))
        self.assertEqual(1, len(info["investigations"]))
        self.assertEqual(6, info["facts"])  # the table rows are the facts

    def test_without_a_configuration_the_call_is_skipped_quietly(self):
        r, out, graph = self.run_investigation(config=None)
        self.assertEqual([], out)
        self.assertNotIn("/query/investigate", [p for p, _ in graph.calls])
        self.assertTrue(r.info()["calls"][0]["skipped"])

    def test_configuration_keeps_known_specific_relations_only(self):
        prof = InvestigationGraph().post("/profile/get", {})["profile"]
        cfg = orchestrator.investigate_config(prof, ["RELATIVE_OF", "CONTACT_OF", "RELATED_TO"],
                                              ["INVOLVED_IN"], 99, 9999)
        self.assertEqual({"relations": ["RELATIVE_OF"], "via": ["INVOLVED_IN"], "branch": 50,
                          "max_rows": 500}, cfg)
        self.assertIsNone(orchestrator.investigate_config(prof, ["CONTACT_OF"], ["RELATED_TO"]))
        line = orchestrator.investigate_tool(prof, cfg, "Hungarian")
        self.assertIn("RELATIVE_OF (Rokona) and people sharing a INVOLVED_IN (Érintett)", line)
        self.assertEqual("", orchestrator.investigate_tool(prof, None))

    def test_the_planner_gets_the_tool_only_when_offered_and_args_are_cleaned(self):
        p = orchestrator.parse_plan(json.dumps({"action": "tools", "calls": [
            {"tool": "investigate", "args": {"start": "NY-100", "levels": "3"}},
            {"tool": "investigate", "args": {"start": {"name": "Sas Tibor"}, "levels": 9}},
            {"tool": "investigate", "args": {"entity": "+36 30 111 1111"}},
            {"tool": "investigate", "args": {"levels": 2}}]}))
        self.assertEqual([{"start": "NY-100", "levels": 3}, {"start": "Sas Tibor", "levels": 4},
                          {"start": "+36 30 111 1111", "levels": 2}],
                         [c["args"] for c in p["calls"]])
        seen = []

        def fake_chat(system, user, **kw):
            seen.append(system)
            return '{"action": "chat"}', None, None
        with patch.object(llm, "GENERATE", "on"), patch.object(llm, "_chat", fake_chat):
            orchestrator.plan("Ki?", [], "Hungarian", "V", investigate="- investigate {start}\n")
            orchestrator.plan("Ki?", [], "Hungarian", "V")
        self.assertIn("- investigate {start}", seen[0])
        self.assertNotIn("investigate", seen[1])
        self.assertNotIn("{investigate}", seen[1])


if __name__ == "__main__":
    unittest.main()
