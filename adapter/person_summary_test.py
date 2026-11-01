"""Per-person summaries of an investigation (person_summary.py), with a fake graph,
RAG and model."""
import json
import tempfile
import threading
import unittest

import analysis_checkpoint
import deep
import llm
import orchestrator
import person_summary
import orchestrator_test as ot

VISIBLE = "/storage/drives/ops/a.txt"
HIDDEN = "/storage/drives/ops/hr/secret.txt"
TEXT = {"Sas Tibor": "Sas Tibor zárkájában mobiltelefont találtak, rajta a +36 30 999 9999 "
                     "szám. A verekedésben Sas Tibor is érintett volt.",
        "Kende Anna": "Kende Anna kapcsolattartó, látogatást kért.",
        "Major Ede": "Major Ede neve csak a névsorban szerepel.",
        "Izsó Csaba": "Izsó Csaba fogvatartott."}


class Rag:
    def __init__(self):
        self.bodies = []

    def __call__(self, path, body):
        assert path == "/rag/match_chunks", path
        self.bodies.append(body)
        chunks = []
        for name, text in TEXT.items():
            if any(name in t for t in body["terms"]):
                for i, where in enumerate((VISIBLE, VISIBLE + ".bak", HIDDEN)):
                    chunks.append({"chunk_id": "%s-%d" % (name, i), "source_path": where,
                                   "corpus_id": "c", "page_number": 1, "text": text})
        return {"chunks": chunks, "total": len(chunks), "documents": 1, "next_offset": None}


class Model:
    """The map step quotes the person's passage; the writer cites note 1 and a
    note that does not exist."""

    def __init__(self, summary="%s: a jelentés szerint mobiltelefont találtak nála [1]. [9]",
                 quote=True):
        self.calls = []
        self.summary = summary
        self.quote = quote

    def __call__(self, system, user, **kw):
        self.calls.append(system)
        if system == deep.MAP_SYSTEM:
            if "Major Ede" in user:
                return '{"findings": []}', None, None
            text = user.split("EXCERPTS:\n", 1)[1].split("\n", 2)[1]
            quote = text.split(".")[0] if self.quote else "kitalált mondat"
            return json.dumps({"findings": [{"excerpt": 1, "quote": quote,
                                             "note": "lefoglalt telefon"}]}), None, None
        name = user.split("PERSON: ", 1)[1].split(" (", 1)[0]
        return (self.summary % name if "%s" in self.summary else self.summary), None, None


def investigation():
    graph = ot.InvestigationGraph()
    run = orchestrator.Run(graph, {"corpus_ids": ["c"], "access": None},
                           lambda p: "/hr/" not in p, "Hungarian",
                           investigate=ot.InvestigateTest.CONFIG)
    run.run([{"tool": "investigate", "args": {"start": "Sas Tibor", "levels": 2}}])
    return run, run.investigations[0]


class PersonSummaryTest(unittest.TestCase):
    def summarize(self, model=None, rag=None, **kw):
        run, inv = investigation()
        self.rag = rag or Rag()
        self.model = model or Model()
        return run, person_summary.summarize(run, inv, self.rag, {"corpus_ids": ["c"]},
                                             lang="Hungarian", chat=self.model, **kw)

    def test_each_person_from_their_visible_documents_with_valid_citations(self):
        _, out = self.summarize()
        persons = {p["name"]: p for p in out["persons"]}
        self.assertEqual(["Sas Tibor", "Kende Anna", "Major Ede", "Izsó Csaba"],
                         [p["name"] for p in out["persons"]])
        sas = persons["Sas Tibor"]
        self.assertEqual("Sas Tibor: a jelentés szerint mobiltelefont találtak nála [#1].",
                         sas["summary"])
        self.assertEqual((1, 1, 1, 1), (sas["passages"], sas["documents_read"], sas["findings"],
                                        sas["cited"]))
        # Only the visible document is asked for and read; the longer path and
        # the hidden document the RAG might return are dropped.
        self.assertTrue(all(b["path_prefixes"] == [VISIBLE] for b in self.rag.bodies))
        self.assertTrue(all(c["source_path"] == VISIBLE for c in out["contexts"]))
        self.assertEqual([VISIBLE], out["sources"])
        first = self.rag.bodies[0]["terms"]
        self.assertEqual(["Sas Tibor", "NY-100"], first)
        # The quote is verbatim from the passage.
        self.assertIn('"Sas Tibor zárkájában mobiltelefont találtak, rajta a +36 30 999 9999 '
                      'szám"', out["contexts"][0]["text"])
        self.assertTrue(persons["Kende Anna"]["summary"].endswith("[#2]."))
        self.assertNotIn("[9]", out["text"])
        # Nothing found: said so, nothing cited.
        self.assertIn("nem tartalmaznak további információt", persons["Major Ede"]["summary"])
        self.assertEqual(0, persons["Major Ede"]["cited"])
        self.assertIn("### Sas Tibor \u2014 Fogvatartott, NY-100 (kiindulópont)", out["text"])
        self.assertIn("### Kende Anna \u2014 Személy (1. szint)", out["text"])
        self.assertIn("senkit nem értékel", out["text"])
        self.assertNotIn("secret", json.dumps(out))
        self.assertFalse(out["info"]["partial"])

    def test_a_summary_citing_nothing_valid_gives_way_to_the_notes(self):
        _, out = self.summarize(Model(summary="Veszélyes személy [5]."))
        sas = out["persons"][0]
        self.assertNotIn("Veszélyes", out["text"])
        self.assertIn("Amit az iratok erről a személyről rögzítenek:\n- lefoglalt telefon [#1]",
                      sas["summary"])

    def test_a_figure_the_notes_do_not_hold_is_not_written(self):
        _, out = self.summarize(Model(summary="%s 2019-ben 14 esetben verekedett [1]."))
        self.assertNotIn("14 esetben", out["text"])
        self.assertIn("lefoglalt telefon [#1]", out["persons"][0]["summary"])

    def test_an_invented_quote_is_no_finding(self):
        _, out = self.summarize(Model(quote=False))
        self.assertEqual([], out["contexts"])
        self.assertTrue(all(p["cited"] == 0 for p in out["persons"]))

    def test_a_person_without_visible_documents_and_the_cuts(self):
        run, inv = investigation()
        run.nodes["K1"] = dict(run.nodes["K1"], documents=["d2"])
        out = person_summary.summarize(run, inv, Rag(), {"corpus_ids": ["c"]},
                                       lang="Hungarian", chat=Model(),
                                       config={"max_persons": 2, "max_docs": 8,
                                               "max_passages": 60})
        self.assertEqual(["Sas Tibor", "Kende Anna"], [p["name"] for p in out["persons"]])
        self.assertIn("egyik sem kapcsolódik", out["persons"][1]["summary"])
        self.assertIn("4 személyéből az első 2-ről", out["text"])
        self.assertTrue(out["info"]["partial"])

    def test_the_client_may_only_narrow_the_people(self):
        _, out = self.summarize(ids=["I3", "nobody", "E"])
        self.assertEqual(["Izsó Csaba"], [p["name"] for p in out["persons"]])
        with self.assertRaises(ValueError):
            person_summary.parse_ids("I3")
        with self.assertRaises(ValueError):
            person_summary.parse_ids([1])
        self.assertIsNone(person_summary.parse_ids(None))
        self.assertEqual(["a", "b"], person_summary.parse_ids(["a", "b", "a"]))

    def test_a_stopped_run_resumes_without_reading_again(self):
        store = analysis_checkpoint.CheckpointStore(tempfile.mkdtemp(prefix="vivesec-ps-"))
        _, first = self.summarize(checkpoints=store, user="u1")
        calls = len(self.model.calls)
        self.assertGreater(calls, 0)
        _, again = self.summarize(checkpoints=store, user="u1")
        # Major Ede has nothing to write, so his map call is the only one again.
        self.assertEqual(0, sum(1 for s in self.model.calls if s != deep.MAP_SYSTEM))
        self.assertEqual(first["persons"][0]["summary"], again["persons"][0]["summary"])
        self.assertIn("korábbi, félbeszakadt futás", again["text"])
        # Another user reads anew.
        _, other = self.summarize(checkpoints=store, user="u2")
        self.assertEqual(calls, len(self.model.calls))
        self.assertNotIn("félbeszakadt", other["text"])

    def test_a_cancel_stops_between_calls(self):
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(llm.GenerationCancelled):
            self.summarize(cancel_event=cancel)
        self.assertEqual([], self.model.calls)

    def test_settings_are_bounded(self):
        self.assertEqual({"max_persons": 12, "max_docs": 12, "max_passages": 60},
                         person_summary.settings({}))
        self.assertEqual({"max_persons": 50, "max_docs": 20, "max_passages": 1},
                         person_summary.settings({
                             "ADAPTER_PERSON_SUMMARY_MAX_PERSONS": "999",
                             "ADAPTER_PERSON_SUMMARY_MAX_DOCS": "99",
                             "ADAPTER_PERSON_SUMMARY_MAX_PASSAGES": "0"}))
        self.assertEqual(12, person_summary.settings(
            {"ADAPTER_PERSON_SUMMARY_MAX_PERSONS": "many"})["max_persons"])


if __name__ == "__main__":
    unittest.main()
