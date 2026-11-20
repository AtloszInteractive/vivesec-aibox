"""Learning structured sources from a sample file (E28b).

A contact list whose columns the profile does not know: the name rules and the
model's answer make a draft, the accepted draft becomes a validated source that
imports, a renamed column is matched once recorded, and no cell value leaves
through an answer or the log.
"""
import base64
import contextlib
import copy
import io
import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import domain_profile  # noqa: E402
import service  # noqa: E402
import source_learning as sl  # noqa: E402
import structured_import  # noqa: E402
from graph_store import MemoryGraphStore  # noqa: E402
from llm_client import LLMError  # noqa: E402

HEADER = ["Sorszám", "Fogvatartott neve", "Fogvatartott nyilvántartási száma",
          "Kapcsolattartó neve", "Születési hely", "Születési idő", "Anyja neve",
          "Kapcsolat jellege", "Mobil", "Email cím", "Lakcím", "Engedély kezdete",
          "Engedély vége", "Megjegyzés", "Törölve"]
ROWS = [
    ["K-1", "Sas Tibor", "NY-1001", "Kerekes Ilona", "Eger", "1955.02.03", "Fekete Mária",
     "anyja", "+36 20 777 1111", "ilona@example.hu", "3300 Eger, Fő u. 1.", "2026-01-01",
     "2026-12-31", "titkos megjegyzés", ""],
    ["K-2", "Sas Péter", "NY-1002", "Kerekes Ilona", "Eger", "1955.02.03", "Fekete Mária",
     "anyja", "+36 20 777 1111", "ilona@example.hu", "3300 Eger, Fő u. 1.", "2026-02-01",
     "", "", ""],
]
SECRET_CELLS = ("Sas Tibor", "Kerekes Ilona", "NY-1001", "Fekete Mária", "+36 20 777 1111",
                "ilona@example.hu", "titkos megjegyzés")


def csv_bytes(header, rows):
    lines = [";".join(header)] + [";".join(r) for r in rows]
    return ("\n".join(lines) + "\n").encode("utf-8")


class ScriptedLLM:
    def __init__(self, answer=None, error=None):
        self.answer, self.error, self.seen = answer, error, []

    def json_call(self, system, user, schema, max_tokens=4096, retries=1):
        self.seen.append(user)
        if self.error:
            raise self.error
        return self.answer


def roles(draft):
    return {c["column"]: (c["role"], c["subtype"], c["of"], c.get("relation"))
            for c in draft["columns"]}


class LearnTest(unittest.TestCase):
    def setUp(self):
        self.profile = domain_profile.load("bv")
        self.raw = csv_bytes(HEADER, ROWS)

    def test_name_rules_read_an_unknown_contact_list_without_the_model(self):
        draft = sl.learn(self.profile, self.raw, "kapcsolattartok_2026.csv")
        got = roles(draft)
        self.assertEqual("off", draft["model"])
        self.assertEqual(("record_id", "", "", None), got["Sorszám"])
        self.assertEqual(("person", "inmate", "", None), got["Fogvatartott neve"])
        self.assertEqual(("person", "contact", "", None), got["Kapcsolattartó neve"])
        # A property goes to the person it names, else to the nearest one on its left.
        self.assertEqual("Fogvatartott neve", got["Fogvatartott nyilvántartási száma"][2])
        for column in ("Születési hely", "Születési idő", "Anyja neve"):
            self.assertEqual("Kapcsolattartó neve", got[column][2], column)
        self.assertEqual(("phone", "", "Kapcsolattartó neve", "USES"), got["Mobil"])
        self.assertEqual(("email", "", "Kapcsolattartó neve", "USES"), got["Email cím"])
        self.assertEqual(("address", "", "Kapcsolattartó neve", "RESIDES_AT"), got["Lakcím"])
        self.assertEqual(("deleted", "text"), (got["Törölve"][0], got["Megjegyzés"][0]))
        self.assertEqual([{"type": "CONTACT_OF", "from": "Kapcsolattartó neve",
                           "to": "Fogvatartott neve", "start": "Engedély kezdete",
                           "end": "Engedély vége", "label": "Kapcsolat jellege"}],
                         draft["relations"])
        self.assertEqual(("kapcsolattartok 2026", 2), (draft["name"], draft["rows"]))
        kinds = {c["column"]: c["kind"] for c in draft["columns"]}
        self.assertEqual(("phone", "date", "empty"),
                         (kinds["Mobil"], kinds["Engedély kezdete"], kinds["Törölve"]))

    def test_the_model_sees_the_samples_but_the_draft_carries_no_cell(self):
        answer = {"columns": [
            {"column": "Sorszám", "role": "record_id"},
            {"column": "Fogvatartott neve", "role": "person", "subtype": "inmate"},
            {"column": "Kapcsolattartó neve", "role": "person", "subtype": "visitor"},
            {"column": "Mobil", "role": "phone", "of": "Fogvatartott neve"},
            {"column": "Megjegyzés", "role": "ignore"},
            {"column": "Ismeretlen", "role": "person"},
            {"column": "Lakcím", "role": "nonsense"}],
            "relations": [{"type": "VISITED", "from": "Kapcsolattartó neve",
                           "to": "Fogvatartott neve"},
                          {"type": "CALLED", "from": "Mobil", "to": "Fogvatartott neve"},
                          {"type": "RELATED_TO", "from": "Mobil", "to": "Lakcím"}]}
        llm = ScriptedLLM(answer)
        draft = sl.learn(self.profile, self.raw, "k.csv", llm=llm)
        self.assertIn("Kerekes Ilona", llm.seen[0])
        dumped = json.dumps(draft, ensure_ascii=False)
        for cell in SECRET_CELLS:
            self.assertNotIn(cell, dumped)
        got = roles(draft)
        self.assertEqual("used", draft["model"])
        self.assertEqual(("person", "visitor"), got["Kapcsolattartó neve"][:2])
        self.assertEqual(("phone", "", "Fogvatartott neve", "CALLED"), got["Mobil"])
        self.assertEqual("ignore", got["Megjegyzés"][0])
        # What the model left out or got wrong stays with the rules.
        self.assertEqual(("address", "RESIDES_AT"), (got["Lakcím"][0], got["Lakcím"][3]))
        self.assertEqual("model", next(c["source"] for c in draft["columns"]
                                       if c["column"] == "Mobil"))
        self.assertEqual("rule", next(c["source"] for c in draft["columns"]
                                      if c["column"] == "Lakcím"))
        self.assertEqual(["VISITED"], [r["type"] for r in draft["relations"]])

    def test_a_record_id_the_model_missed_comes_from_the_rules(self):
        llm = ScriptedLLM({"columns": [{"column": "Sorszám", "role": "ignore"}],
                           "relations": []})
        draft = sl.learn(self.profile, self.raw, "k.csv", llm=llm)
        self.assertEqual("record_id", roles(draft)["Sorszám"][0])

    def test_a_failed_model_falls_back_and_logs_no_sample(self):
        err = io.StringIO()
        llm = ScriptedLLM(error=LLMError("model call failed: Kerekes Ilona"))
        with contextlib.redirect_stderr(err):
            draft = sl.learn(self.profile, self.raw, "k.csv", llm=llm)
        self.assertEqual("failed", draft["model"])
        self.assertEqual("person", roles(draft)["Kapcsolattartó neve"][0])
        self.assertIn("LLMError", err.getvalue())
        for cell in SECRET_CELLS:
            self.assertNotIn(cell, err.getvalue())


class ApplyTest(unittest.TestCase):
    def setUp(self):
        self.profile = domain_profile.load("bv")
        self.raw = csv_bytes(HEADER, ROWS)
        self.draft = sl.learn(self.profile, self.raw, "k.csv")

    def test_the_accepted_draft_is_a_validated_source_that_imports(self):
        profile, source = sl.apply_draft(self.profile, self.draft, "Kapcsolattartók 2026")
        self.assertEqual(self.profile["version"] + 1, profile["version"])
        self.assertEqual(profile, domain_profile.validate(copy.deepcopy(profile)))
        self.assertEqual(("kapcsolattartok_2026", ["Sorszám"]), (source["id"], source["record_id"]))
        contact = next(e for e in source["entities"] if e["subtype"] == "contact")
        self.assertTrue(contact["person_key"])
        self.assertEqual(["Megjegyzés"], source["text_columns"])
        self.assertEqual("Törölve", source["deleted"]["column"])
        header, rows = structured_import.read_table(self.raw, "k.csv")
        plan = structured_import.plan_import(profile, source, header, rows, b"s" * 32)
        self.assertEqual((2, 0, 2), (len(plan.records), plan.problem_count, plan.person_keys))
        rec = plan.records["K-1"]
        self.assertEqual({"CONTACT_OF", "USES", "RESIDES_AT"},
                         {r["type"] for r in rec.relations})
        contact_of = next(r for r in rec.relations if r["type"] == "CONTACT_OF")
        self.assertEqual(("contact", "inmate", "2026-01-01", "anyja"),
                         (contact_of["from"], contact_of["to"], contact_of["start"],
                          contact_of["label"]))
        # Another source from the same name gets its own id.
        again, second = sl.apply_draft(profile, self.draft, "Kapcsolattartók 2026")
        self.assertEqual("kapcsolattartok_2026_2", second["id"])

    def test_dates_no_relation_reads_stay_in_the_text(self):
        draft = dict(self.draft, relations=[])
        _, source = sl.apply_draft(self.profile, draft, "K")
        self.assertEqual(["Kapcsolat jellege", "Engedély kezdete", "Engedély vége",
                          "Megjegyzés"], source["text_columns"])

    def test_empty_header_cells_are_no_columns(self):
        # A trailing separator (and a blank header cell) is common in exports.
        header = HEADER[:3] + [""] + HEADER[3:] + [""]
        rows = [r[:3] + [""] + r[3:] + [""] for r in ROWS]
        draft = sl.learn(self.profile, csv_bytes(header, rows), "k.csv")
        self.assertEqual(HEADER, [c["column"] for c in draft["columns"]])
        _, source = sl.apply_draft(self.profile, draft, "K")
        self.assertEqual(["Sorszám"], source["record_id"])

    def test_a_draft_that_makes_no_valid_source_is_refused(self):
        def change(column, **kw):
            d = copy.deepcopy(self.draft)
            next(c for c in d["columns"] if c["column"] == column).update(kw)
            return d
        bad = [
            change("Sorszám", role="ignore"),  # no record id
            change("Születési hely", of=""),  # two persons: whose?
            change("Születési hely", of="Mobil"),  # not a person
            change("Fogvatartott neve", subtype="cell"),
            change("Lakcím", relation="USES"),  # a person does not use an address
            change("Kapcsolattartó neve", role="nonsense"),
            dict(self.draft, relations=[{"type": "CONTACT_OF", "from": "Mobil",
                                         "to": "Fogvatartott neve"}]),
            dict(self.draft, relations=[{"type": "PROVIDED_INFO", "from": "Kapcsolattartó neve",
                                         "to": "Fogvatartott neve"}]),  # protected
            dict(self.draft, relations=[dict(self.draft["relations"][0], start="Mobil")]),
        ]
        for i, draft in enumerate(bad):
            with self.assertRaises(domain_profile.ProfileError, msg=i):
                sl.apply_draft(self.profile, draft, "K")
        with self.assertRaises(domain_profile.ProfileError):
            sl.apply_draft(self.profile, self.draft, "  ")


class AliasTest(unittest.TestCase):
    def setUp(self):
        self.profile = domain_profile.load("bv")
        self.source = next(s for s in self.profile["structured_sources"] if s["id"] == "contacts")

    def export(self, renamed):
        columns = sorted(structured_import.source_columns(self.source))
        header = [renamed.get(c, c) for c in columns]
        rows = [["R-1" if c == "Rekordazonosító" else "Kiss Anna" if c.endswith("neve")
                 else "01" if c == "Intézet kód" else "" for c in columns]]
        return header, csv_bytes(header, rows)

    def test_a_renamed_column_is_matched_once_recorded(self):
        header, raw = self.export({"Rekordazonosító": "Azonosító", "Telefonszám": "Mobil"})
        fit = {f["id"]: f for f in sl.inspect(self.profile, raw, "k.csv")["sources"]}
        self.assertEqual(("none", ["Rekordazonosító"], ["Azonosító", "Mobil"]),
                         (fit["contacts"]["fit"], fit["contacts"]["missing_required"],
                          sorted(fit["contacts"]["unused"])))
        self.assertIsNone(sl.inspect(self.profile, raw, "k.csv")["best"])
        profile, changes = sl.apply_aliases(self.profile, "contacts",
                                            {"Rekordazonosító": "Azonosító",
                                             "Telefonszám": "Mobil"})
        self.assertEqual(2, len(changes))
        out = sl.inspect(profile, raw, "k.csv")
        fit = {f["id"]: f for f in out["sources"]}
        self.assertEqual(("contacts", "full", ["Rekordazonosító", "Telefonszám"]),
                         (out["best"], fit["contacts"]["fit"], fit["contacts"]["aliased"]))
        source = next(s for s in profile["structured_sources"] if s["id"] == "contacts")
        h, rows = structured_import.read_table(raw, "k.csv", None,
                                               structured_import.expected_columns(source))
        renamed = structured_import.plan_import(profile, source, h, rows, None)
        orig_header, orig_raw = self.export({})
        h, rows = structured_import.read_table(orig_raw, "k.csv")
        original = structured_import.plan_import(profile, source, h, rows, None)
        # The record reads the same: the text names the columns as the profile does.
        self.assertEqual(original.records["R-1"].text, renamed.records["R-1"].text)
        with self.assertRaises(domain_profile.ProfileError):
            sl.apply_aliases(profile, "contacts", {"Rekordazonosító": "azonosító"})  # known

    def test_a_header_spelt_otherwise_keeps_the_files_spelling(self):
        # Matched without an alias (case, accents): the record text stays as before.
        header, raw = self.export({"Kapcsolattartó neve": "KAPCSOLATTARTO NEVE"})
        h, rows = structured_import.read_table(raw, "k.csv")
        plan = structured_import.plan_import(self.profile, self.source, h, rows, None)
        self.assertIn("KAPCSOLATTARTO NEVE: Kiss Anna", plan.records["R-1"].text)

    def test_aliases_are_validated_strictly(self):
        raw = json.loads(json.dumps(self.profile))
        src = next(s for s in raw["structured_sources"] if s["id"] == "contacts")
        for aliases in ({"Nincs ilyen": ["X"]},  # not a column of the source
                        {"Telefonszám": ["E-mail"]},  # another column of the source
                        {"Telefonszám": ["Mobil"], "E-mail": ["mobil"]},  # two columns
                        {"Telefonszám": []}, {"Telefonszám": "Mobil"}):
            src["column_aliases"] = aliases
            with self.assertRaises(domain_profile.ProfileError, msg=aliases):
                domain_profile.validate(raw)
        src["column_aliases"] = {"telefonszám": ["Mobil"]}
        valid = domain_profile.validate(raw)
        source = next(s for s in valid["structured_sources"] if s["id"] == "contacts")
        self.assertEqual({"Telefonszám": ["Mobil"]}, source["column_aliases"])
        self.assertEqual(valid, domain_profile.validate(copy.deepcopy(valid)))


class ServiceTest(unittest.TestCase):
    """learn -> apply -> profile/set -> import over the service's handlers."""

    def test_a_learnt_source_is_saved_as_a_new_version_and_imports(self):
        from ingest_jobs import IngestWorker
        from ingest_jobs_test import FakeLLM

        state = service.State(MemoryGraphStore(), domain_profile.load("bv"))
        state.worker = IngestWorker(state, FakeLLM())
        file = {"filename": "k.csv",
                "content_b64": base64.b64encode(csv_bytes(HEADER, ROWS)).decode("ascii")}
        out = service.handle_post(state, "/import/inspect", file)
        self.assertEqual((None, HEADER), (out["best"], out["header"]))
        draft = service.handle_post(state, "/import/learn", dict(file, model=False))
        self.assertEqual("off", draft["model"])
        applied = service.handle_post(state, "/import/learn/apply",
                                      {"draft": draft, "name": "Kapcsolattartók"})
        self.assertEqual(state.profile["version"] + 1, applied["profile"]["version"])
        with self.assertRaises(service.HttpError) as bad:
            service.handle_post(state, "/import/learn/apply", {"draft": {}, "name": "x"})
        self.assertEqual(400, bad.exception.status)
        tag = domain_profile.profile_tag(state.profile)
        saved = service.handle_post(state, "/profile/set", {"profile": applied["profile"]})
        # A new source does not change how documents are extracted.
        self.assertEqual((False, tag), (saved["reprocess_required"],
                                        domain_profile.profile_tag(state.profile)))
        self.assertIn(applied["source"]["id"], [s["id"] for s in service.import_sources(
            state)["sources"]])
        res = service.handle_post(state, "/import/records", dict(
            file, source=applied["source"]["id"], as_of="2026-11-20", folder="/d/export",
            corpus_id="c1", wait=30))["import"]
        self.assertEqual(("done", 2), (res["status"], res["stats"]["created"]))
        out = service.handle_post(state, "/import/inspect", file)
        self.assertEqual(applied["source"]["id"], out["best"])
        with self.assertRaises(service.HttpError):
            service.handle_post(state, "/import/aliases/apply",
                                {"source": "contacts", "aliases": {"Nincs": "X"}})


if __name__ == "__main__":
    unittest.main()
