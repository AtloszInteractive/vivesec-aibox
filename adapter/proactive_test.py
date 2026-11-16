"""Proactive chat: next-question suggestions and evidence observations (proactive.py)."""
import copy
import unittest

import orchestrator
import proactive
from orchestrator_test import DOC, EDGES, NE, FakeGraph


def run(calls, graph=None, max_facts=40, query=""):
    graph = graph or FakeGraph()
    r = orchestrator.Run(graph, {"corpus_ids": ["c"], "access": None},
                         lambda p: "/hr/" not in p, "Hungarian", max_facts, message=query)
    r.run(calls)
    return r


class Graph(FakeGraph):
    """The fake graph with changed edges, documents or nodes."""

    def __init__(self, weak=False, docs=None, nodes=None):
        super().__init__()
        self.weak, self.docs, self.extra = weak, docs, nodes or {}

    def post(self, path, body):
        out = copy.deepcopy(super().post(path, body))
        for e in out.get("edges") or []:
            e["weak"] = self.weak
        if self.docs is not None and "documents" in out and out["documents"]:
            out["documents"] = self.docs
        for key in ("entities", "nodes"):
            out[key] = [self.extra.get(n["id"], n) if n else n for n in out.get(key) or []]
        return out


class SuggestionTest(unittest.TestCase):
    def test_next_questions_skip_what_was_asked(self):
        r = run([{"tool": "relations", "args": {"entity": "Nagy Éva"}}])
        got = [s["question"] for s in proactive.suggestions(r, "Kik Nagy Éva kapcsolatai?")]
        self.assertIn("Mutasd meg az idővonalát: Nagy Éva", got)
        self.assertIn("Van-e közös telefonszáma, címe vagy más azonosítója valakivel: "
                      "Nagy Éva?", got)
        self.assertNotIn("Kikkel áll közvetlen kapcsolatban: Nagy Éva?", got)
        # A visitor was in no cell: no co-presence question.
        self.assertFalse(any("zárkában" in q for q in got))
        self.assertLessEqual(len(got), proactive.MAX_SUGGESTIONS)

    def test_two_people_get_path_and_common_first_and_a_summary_later(self):
        r = run([{"tool": "timeline", "args": {"entity": "Nagy Éva"}},
                 {"tool": "timeline", "args": {"entity": "Kovács Péter"}}])
        got = [s["question"] for s in proactive.suggestions(r, "idővonalak", turns=3)]
        self.assertEqual(["Milyen úton kapcsolódnak egymáshoz: Nagy Éva és Kovács Péter?",
                          "Kik a közös kapcsolataik: Nagy Éva és Kovács Péter?"], got[:2])
        self.assertNotIn("Mutasd meg az idővonalát: Nagy Éva", got)
        r = run([{"tool": "relations", "args": {"entity": "Nagy Éva"}}])
        got = [s["question"] for s in proactive.suggestions(r, "x", turns=3)]
        self.assertEqual("Foglald össze, mit találtunk eddig ebben a beszélgetésben", got[-1])

    def test_a_cut_result_offers_to_narrow_it_to_a_year(self):
        r = run([{"tool": "relations", "args": {"entity": "Nagy Éva"}}], max_facts=1)
        got = [s["question"] for s in proactive.suggestions(r, "Nagy Éva kapcsolatai")]
        self.assertEqual("Ugyanez, csak erre az évre: 2026", got[0])
        got = [s["question"] for s in proactive.suggestions(r, "Nagy Éva 2025-ben")]
        self.assertFalse(any(q.startswith("Ugyanez") for q in got))


class ObservationTest(unittest.TestCase):
    def test_single_source_and_data_after_the_period_asked(self):
        r = run([{"tool": "relations", "args": {"entity": "Nagy Éva"}}])
        got = proactive.observations(r, "Kik látogatták Nagy Évát 2025-ben?")
        self.assertIn("Minden talált kapcsolat egyetlen forrásból származik: a. Érdemes más "
                      "forrással is megerősíteni.", got)
        self.assertIn("A megkérdezett időszak (2025) után is van adat: Nagy Éva — 1 további "
                      "kapcsolat, legutóbb 2026-01-02.", got)
        self.assertFalse(any("2026" in o for o in proactive.observations(r, "2026-ban?")))

    def test_named_together_only_and_protected_and_partial_documents(self):
        docs = {"d1": dict(DOC["d1"], source_path="/storage/drives/ops/~records/intel_log/"
                                                   "~protected/N-1", partial=True),
                "d2": DOC["d2"]}
        r = run([{"tool": "relations", "args": {"entity": "Nagy Éva"}}],
                graph=Graph(weak=True, docs=docs))
        got = proactive.observations(r, "Nagy Éva")
        self.assertTrue(got[0].startswith("A válasz védett (informátori) adatot"), got)
        self.assertTrue(any(o.startswith("A talált kapcsolatok mind csak együttes") for o in got))
        self.assertTrue(any(o.startswith("1 forrásdokumentum szövegét") for o in got))

    def test_differing_birth_dates_of_a_person_asked_about(self):
        ne = dict(NE, attributes=[
            {"name": "birth_date", "value": "1970-02-02", "doc_id": "d1"},
            {"name": "birth_date", "value": "1970-02-20", "doc_id": "d1"},
            # A hidden document's value does not count.
            {"name": "birth_date", "value": "1999-09-09", "doc_id": "d2"}])
        r = run([{"tool": "relations", "args": {"entity": "Nagy Éva"}}],
                graph=Graph(nodes={"p4": ne}))
        self.assertIn("Eltérő születési idő a forrásokban (Nagy Éva): 1970-02-02 / 1970-02-20. "
                      "Ellenőrizd, valóban egy személyről van-e szó.",
                      proactive.observations(r, "Nagy Éva"))

    def test_nothing_to_note_when_the_evidence_is_plain(self):
        two = dict(EDGES[2], evidence=[{"doc_id": "d3", "page": 1, "quote": "x"}])
        docs = dict(DOC, d3={"corpus_id": "c", "source_path": "/storage/drives/ops/b.txt",
                             "title": "b", "kind": "txt"})

        class Plain(Graph):
            def post(self, path, body):
                out = super().post(path, body)
                out["edges"] = [two if e["id"] == "e3" else e for e in out.get("edges") or []]
                return out

        r = run([{"tool": "relations", "args": {"entity": "Nagy Éva"}}],
                graph=Plain(docs=docs))
        self.assertEqual([], proactive.observations(r, "Nagy Éva kapcsolatai"))


if __name__ == "__main__":
    unittest.main()
