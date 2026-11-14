"""Structured import of periodic exports and contact linking (E28).

Synthetic exports (graph-corpus/structured_samples.py): the brothers' mother in
two contact records, namesakes with different birth dates, a phone shared by
two records of different data. Then idempotency, deletion, ordering, the
record-level access rule and the differential leakage check.
"""
import base64
import copy
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.append(os.path.join(os.path.dirname(HERE), "graph-corpus"))
import domain_profile  # noqa: E402
import service  # noqa: E402
import structured_import  # noqa: E402
from graph_store import MemoryGraphStore  # noqa: E402
from ingest_jobs import IngestWorker  # noqa: E402
from ingest_jobs_test import FakeLLM  # noqa: E402
from leakage_test import answer, first_difference  # noqa: E402

try:  # the synthetic exports live in graph-corpus/, not in the graph image
    import structured_samples as samples  # noqa: E402
except ImportError:
    samples = None
needs_samples = unittest.skipIf(samples is None, "needs graph-corpus/structured_samples.py")

FOLDER = "/storage/drives/bv/export"


def access(*groups):
    return {"principals": ["user:x"] + ["group:%s" % g for g in groups], "clearance": 0,
            "membership_known": True, "default_classification": 0}


NATIONAL = access("bv-orszagos")
INST1 = access("bv-intezet-01")
NOBODY = access()
CONTACT = ["CONTACT_OF"]


class Box:
    """A graph service with the bv profile and the extraction worker."""

    def __init__(self):
        self.state = service.State(MemoryGraphStore(), domain_profile.load("bv"))
        self.state.worker = IngestWorker(self.state, FakeLLM())

    def load(self, source, header, rows, as_of=None, folder=FOLDER, acl=None,
             fmt="csv", **extra):
        content = samples.to_xlsx(header, rows) if fmt == "xlsx" else samples.to_csv(header, rows)
        body = dict({"source": source, "as_of": as_of or samples.AS_OF, "folder": folder,
                     "corpus_id": "c1",
                     "filename": "%s.%s" % (source, fmt),
                     "content_b64": base64.b64encode(content).decode("ascii")}, **extra)
        if acl is not None:
            body["acl"] = acl
        return service.handle_post(self.state, "/import/records", body)["import"]

    def load_samples(self, only=None):
        for source, (header, rows) in samples.exports().items():
            if only:
                rows = [r for r in rows if only(source, header, r)]
            self.load(source, header, rows)
        return self

    def q(self, path, body, who=NATIONAL):
        return service.handle_post(self.state, path, dict(body, corpus_ids=["c1"], access=who))

    def find(self, text, who=NATIONAL):
        return [e for e in self.q("/query/find", {"text": text, "types": ["person"]},
                                  who)["entities"]]

    def one(self, text, who=NATIONAL):
        found = self.find(text, who)
        assert len(found) == 1, (text, found)
        return found[0]["id"]

    def investigate(self, seed, who=NATIONAL, **kw):
        body = dict({"seeds": [seed], "relations": CONTACT, "levels": 4}, **kw)
        out = self.q("/query/investigate", body, who)
        return {r["node"]["label"]: (r["level"], [bool(v.get("shared_contact")) for v in r["via"]])
                for r in out["rows"]}


class SaltedTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.salt = os.path.join(self.tmp, "salt")
        with open(self.salt, "w") as f:
            f.write("s" * 32)
        self._env = os.environ.get("GRAPH_PERSON_KEY_SALT_FILE")
        os.environ["GRAPH_PERSON_KEY_SALT_FILE"] = self.salt

    def tearDown(self):
        if self._env is None:
            os.environ.pop("GRAPH_PERSON_KEY_SALT_FILE", None)
        else:
            os.environ["GRAPH_PERSON_KEY_SALT_FILE"] = self._env
        shutil.rmtree(self.tmp, ignore_errors=True)


@needs_samples
class ContactLinkingTest(SaltedTest):
    def setUp(self):
        super().setUp()
        self.box = Box().load_samples()

    def test_the_brothers_mother_is_one_person_and_links_them(self):
        [mother] = self.box.find("Kerekes Ilona")
        self.assertEqual(2, len(mother["documents"]))
        self.assertEqual("contact", mother["subtype"])
        rows = self.box.investigate(self.box.one("NY-1001"), levels=2)
        self.assertEqual({"Sas Tibor": (0, []), "Kerekes Ilona": (1, [False]),
                          "Sas Gábor": (2, [False])}, rows)
        edges = self.box.q("/query/entity", {"id": mother["id"]})["edges"]
        self.assertEqual(["anyja", "anyja"], [e["raw_label"] for e in edges
                                              if e["type"] == "CONTACT_OF"])

    def test_the_person_key_is_never_shown(self):
        mother = self.box.find("Kerekes Ilona")[0]
        self.assertEqual({"birth_date", "birth_place", "mother_name"},
                         {a["name"] for a in mother["attributes"]})
        self.assertNotIn("link_rule", self.box.find("Sas Tibor")[0])
        self.assertEqual("key_only", mother["link_rule"])
        keys = {a.value for e in self.box.state.store.entities.values() for a in e.attributes
                if a.name == "person_key"}
        self.assertEqual(10, len(keys))  # 6 inmates; contacts: the mother once, 2 + 1 others
        tib = self.box.one("NY-1001")
        dumped = json.dumps([self.box.q("/query/entity", {"id": mother["id"]}),
                             self.box.q("/query/neighborhood", {"id": tib, "hops": 3}),
                             self.box.q("/query/find", {"text": "a"}),
                             self.box.q("/review/list", {})], ensure_ascii=False)
        self.assertFalse([k for k in keys if k in dumped])
        self.assertNotIn("person_key", dumped)
        # Nor can a payload smuggle one in.
        with self.assertRaises(service.HttpError):
            service.handle_post(self.box.state, "/graph/document/put", {
                "document": {"doc_id": "x", "corpus_id": "c1", "source_path": "/x"},
                "entities": [{"id": "person:x", "type": "person", "attributes": [
                    {"name": "person_key", "value": sorted(keys)[0]}]}]})

    def test_namesakes_with_different_birth_dates_stay_two_people(self):
        self.assertEqual(2, len(self.box.find("Nagy Erzsébet")))
        rows = self.box.investigate(self.box.one("NY-1003"))
        self.assertNotIn("Illés Dénes", rows)
        self.assertEqual([], [i for i in self.box.q("/review/list", {})["review"]
                              if "Nagy" in i["a_label"]])

    def test_a_shared_phone_is_a_review_pair_and_a_named_step_never_a_merge(self):
        self.assertEqual(2, len(self.box.find("Fekete Judit")))
        [pair] = self.box.q("/review/list", {})["review"]
        self.assertEqual(("Fekete Judit", "same name + shared contact data"),
                         (pair["a_label"], pair["reason"]))
        bodo = self.box.one("NY-1005")
        self.assertNotIn("Varga Péter", self.box.investigate(bodo))
        rows = self.box.investigate(bodo, shared_contact=True)
        self.assertEqual((3, [False]), rows["Varga Péter"])
        judit = [r for r in self.box.q("/query/investigate", {
            "seeds": [bodo], "relations": CONTACT, "levels": 3, "shared_contact": True})["rows"]
            if r["level"] == 2][0]
        self.assertEqual("+36207772222", judit["via"][0]["through"]["id"].rsplit(":", 1)[1])
        self.assertTrue(judit["via"][0]["shared_contact"])

    def test_a_shared_phone_with_conflicting_birth_dates_is_not_even_a_pair(self):
        box = Box()
        header = samples.CONTACTS
        box.load("contacts", header, [
            ["X1", "NY-1", "A Béla", "Kiss Ágnes", "Vác", "1970-01-01", "Pap Éva", "ANYA",
             "+36 30 999 0000", "", "", "", "", "01", ""],
            ["X2", "NY-2", "B Béla", "Kiss Ágnes", "Vác", "1971-01-01", "Pap Éva", "ANYA",
             "+36 30 999 0000", "", "", "", "", "01", ""]])
        self.assertEqual(2, len(box.find("Kiss Ágnes")))
        self.assertEqual([], box.q("/review/list", {})["review"])
        self.assertEqual((3, [False]), box.investigate(box.one("NY-1"),
                                                       shared_contact=True)["B Béla"])

    def test_the_name_alone_links_nothing(self):
        box = Box()
        header = samples.CONTACTS
        box.load("contacts", header, [
            ["Y1", "NY-1", "A Béla", "Kiss Ágnes", "Vác", "1970-01-01", "Pap Éva", "ANYA",
             "+36 30 111 0000", "", "", "", "", "01", ""],
            ["Y2", "NY-2", "B Béla", "Kiss Ágnes", "", "", "", "ANYA", "+36 30 222 0000", "",
             "", "", "", "01", ""],
            # Name and birth date, no mother's name: partial, a review pair only.
            ["Y3", "NY-3", "C Béla", "Kiss Ágnes", "", "1970-01-01", "", "ANYA", "", "", "", "",
             "", "01", ""]])
        self.assertEqual(3, len(box.find("Kiss Ágnes")))
        reasons = sorted(i["reason"] for i in box.q("/review/list", {})["review"])
        self.assertEqual(["same name + birth date"], reasons)
        # An extracted person with the same name and birth date is not merged either.
        service.handle_post(box.state, "/graph/document/put", {
            "document": {"doc_id": "note", "corpus_id": "c1", "source_path": "/c1/note.txt"},
            "entities": [{"id": "person:note", "type": "person", "label": "Kiss Ágnes",
                          "attributes": [{"name": "birth_date", "value": "1970-01-01"}]}],
            "mentions": [{"id": "note:m0", "entity_id": "person:note", "text": "Kiss Ágnes"}]})
        self.assertEqual(4, len(box.find("Kiss Ágnes")))

    def test_without_a_salt_no_contact_is_linked(self):
        os.environ["GRAPH_PERSON_KEY_SALT_FILE"] = os.path.join(self.tmp, "missing")
        box = Box()
        out = box.load("contacts", *samples.exports()["contacts"])
        self.assertEqual(("off", 0), (out["stats"]["person_key"], out["stats"]["person_keys"]))
        self.assertEqual(2, len(box.find("Kerekes Ilona")))
        self.assertNotIn("Sas Gábor", box.investigate(box.one("NY-1001")))
        self.assertIn("same name + birth date",
                      [i["reason"] for i in box.q("/review/list", {})["review"]])

    def test_name_joining_leaves_contact_records_alone(self):
        rows = self.box.q("/query/investigate", {"seeds": [self.box.one("NY-1005")],
                                                 "relations": CONTACT, "levels": 4,
                                                 "join_namesakes": True})["rows"]
        self.assertNotIn("Varga Péter", [r["node"]["label"] for r in rows])

    def test_placements_give_cellmates_by_date(self):
        tib = self.box.one("NY-1001")
        [mate] = self.box.q("/query/copresence", {"id": tib})["copresence"]
        self.assertEqual(("Major Ede", "2026-03-15", "2026-04-30"),
                         (mate["other"]["label"], mate["from"], mate["to"]))
        self.assertEqual("102 (Kőhegyi Országos Büntetés-végrehajtási Intézet / A objektum / "
                         "B részleg)", mate["place"]["label"])
        edge = [e for e in self.box.q("/query/entity", {"id": tib})["edges"]
                if e["type"] == "PLACED_IN" and e["start"] == "2026-02-01"][0]
        self.assertEqual("befogadás / áthelyezés intézeten belül", edge["raw_label"])
        self.assertIn("Behelyezés indoka: BEF (befogadás)", edge["evidence"][0]["quote"])
        self.assertNotIn("E-0001", edge["evidence"][0]["quote"])  # the record id is not text


@needs_samples
class ImportBehaviourTest(SaltedTest):
    def setUp(self):
        super().setUp()
        self.box = Box()
        self.header, self.rows = samples.exports()["contacts"]

    def test_the_same_export_twice_changes_nothing(self):
        first = self.box.load("contacts", self.header, self.rows)["stats"]
        again = self.box.load("contacts", self.header, self.rows)["stats"]
        self.assertEqual((6, 0), (first["created"], first["unchanged"]))
        self.assertEqual((0, 0, 6), (again["created"], again["updated"], again["unchanged"]))
        self.assertEqual(1, len(self.box.find("Kerekes Ilona")))

    def test_a_newer_row_replaces_an_older_export_never(self):
        self.box.load("contacts", self.header, self.rows)
        changed = copy.deepcopy(self.rows)
        changed[2][8] = "+36 70 000 0000"
        stats = self.box.load("contacts", self.header, changed, as_of="2026-12-01")["stats"]
        self.assertEqual((1, 5), (stats["updated"], stats["unchanged"]))
        stale = self.box.load("contacts", self.header, self.rows, as_of="2026-10-01")["stats"]
        # Only the changed row carries the newer date; an older export touches none.
        self.assertEqual((0, 6), (stale["updated"], stale["older"]))
        phones = {e["b"] for e in self.box.q("/query/entity", {
            "id": self.box.find("Nagy Erzsébet")[0]["id"]})["edges"] if e["type"] == "USES"} | {
            e["b"] for e in self.box.q("/query/entity", {
                "id": self.box.find("Nagy Erzsébet")[1]["id"]})["edges"] if e["type"] == "USES"}
        self.assertIn("id:phone:+36700000000", phones)
        self.assertNotIn("id:phone:+36701234567", phones)

    def test_delta_removes_marked_rows_only_and_full_removes_the_unlisted(self):
        self.box.load("contacts", self.header, self.rows)
        marked = [r[:-1] + ["igen"] for r in self.rows[:1]]
        stats = self.box.load("contacts", self.header, marked, as_of="2026-11-02")["stats"]
        self.assertEqual(1, stats["removed"])
        self.assertEqual([], self.box.find("Sas Tibor"))  # named only in the removed record
        self.assertEqual(1, len(self.box.find("Kerekes Ilona")[0]["documents"]))
        stats = self.box.load("contacts", self.header, self.rows[1:5], as_of="2026-11-03",
                              mode="full")["stats"]
        self.assertEqual((1, 4), (stats["removed"], stats["unchanged"]))
        self.assertEqual(1, len(self.box.find("Fekete Judit")))

    def test_a_full_export_that_would_wipe_the_register_is_refused(self):
        self.box.load("contacts", self.header, self.rows)
        with self.assertRaises(service.HttpError) as e:
            self.box.load("contacts", self.header, self.rows[:2], mode="full")
        self.assertEqual(409, e.exception.status)
        self.assertEqual(2, len(self.box.find("Fekete Judit")))
        stats = self.box.load("contacts", self.header, self.rows[:2], mode="full",
                              force=True)["stats"]
        self.assertEqual(4, stats["removed"])

    def test_xlsx_codes_and_columns(self):
        rows = copy.deepcopy(self.rows)
        rows[0][7] = "XYZ"
        out = self.box.load("contacts", self.header, rows, fmt="xlsx")["stats"]
        self.assertEqual({"kapcsolat": ["XYZ"]}, out["unknown_codes"])
        header = [h for h in self.header if h != "E-mail"]
        rows = [[c for h, c in zip(self.header, r) if h != "E-mail"] for r in self.rows]
        self.assertEqual(["E-mail"], self.box.load("contacts", header, rows)["stats"][
            "missing_columns"])
        with self.assertRaises(service.HttpError) as e:
            self.box.load("contacts", self.header[1:], [r[1:] for r in self.rows])
        self.assertEqual(422, e.exception.status)
        self.assertIn("Rekordazonosító", str(e.exception))

    def test_records_are_not_listed_for_reprocessing(self):
        self.box.load("contacts", self.header, self.rows)
        out = self.box.q("/documents/profile", {"with_paths": True})
        self.assertEqual(({}, []), (out["counts"], out["paths"]))

    def test_a_late_older_export_neither_overwrites_nor_revives(self):
        self.box.load("contacts", self.header, self.rows)
        again = self.box.load("contacts", self.header, self.rows, as_of="2026-12-01")["stats"]
        self.assertEqual(6, again["unchanged"])  # same rows, newer date: the date moves on
        changed = copy.deepcopy(self.rows)
        changed[2][8] = "+36 70 000 0000"
        late = self.box.load("contacts", self.header, changed, as_of="2026-11-15")["stats"]
        self.assertEqual((0, 6), (late["updated"], late["older"]))
        marked = [r[:-1] + ["igen"] for r in self.rows[4:5]]
        self.assertEqual(1, self.box.load("contacts", self.header, marked,
                                          as_of="2026-12-02")["stats"]["removed"])
        revive = self.box.load("contacts", self.header, self.rows[4:5], as_of="2026-12-01")
        self.assertEqual((0, 1), (revive["stats"]["created"], revive["stats"]["older"]))
        self.assertEqual(1, len(self.box.find("Fekete Judit")))
        back = self.box.load("contacts", self.header, self.rows[4:5], as_of="2026-12-03")
        self.assertEqual(1, back["stats"]["created"])
        self.assertEqual(2, len(self.box.find("Fekete Judit")))

    def test_another_folder_never_touches_these_records(self):
        self.box.load("contacts", self.header, self.rows)
        marked = [r[:-1] + ["igen"] for r in self.rows]
        other = self.box.load("contacts", self.header, marked, folder=FOLDER + "-b",
                              as_of="2026-12-01")["stats"]
        self.assertEqual(0, other["removed"])
        self.assertEqual(1, len(self.box.find("Kerekes Ilona")))
        self.assertEqual(2, len(self.box.find("Kerekes Ilona")[0]["documents"]))

    def test_a_reviewers_merge_survives_a_new_export(self):
        os.environ["GRAPH_PERSON_KEY_SALT_FILE"] = os.path.join(self.tmp, "missing")
        self.box.load("contacts", self.header, self.rows)
        [pair] = [i for i in self.box.q("/review/list", {})["review"]
                  if i["a_label"] == "Kerekes Ilona"]
        self.box.q("/review/decide", {"a": pair["a"], "b": pair["b"], "decision": "merge"})
        self.assertEqual(1, len(self.box.find("Kerekes Ilona")))
        changed = copy.deepcopy(self.rows)
        changed[1][9] = "uj.cim@example.hu"
        self.assertEqual(1, self.box.load("contacts", self.header, changed,
                                          as_of="2026-12-01")["stats"]["updated"])
        [mother] = self.box.find("Kerekes Ilona")
        self.assertEqual(2, len(mother["documents"]))
        self.assertIn("Sas Tibor", self.box.investigate(self.box.find("Sas Gábor")[0]["id"]))

    def test_a_registry_number_is_not_readable_from_an_id(self):
        import hashlib
        header, rows = samples.exports()["placement"]
        self.box.load("placement", header, rows[:1])
        pid = self.box.find("Sas Tibor")[0]["id"]
        self.assertNotEqual("person:" + hashlib.sha1(b"reg\0ny-1001").hexdigest()[:16], pid)


@needs_samples
class RecordAccessTest(SaltedTest):
    def setUp(self):
        super().setUp()
        self.box = Box()

    def test_a_record_is_seen_by_its_institution_and_the_national_group(self):
        self.box.load_samples()
        self.assertEqual(1, len(self.box.find("NY-1001", INST1)))
        self.assertEqual([], self.box.find("NY-1002", INST1))
        self.assertEqual(1, len(self.box.find("NY-1002", NATIONAL)))
        self.assertEqual([], self.box.find("NY-1001", NOBODY))
        mother = self.box.find("Kerekes Ilona", INST1)[0]
        self.assertEqual(1, len(mother["documents"]))
        self.assertNotIn("Sas Gábor", self.box.investigate(self.box.one("NY-1001", INST1),
                                                           INST1))

    def test_an_unknown_institution_code_admits_only_the_national_group(self):
        header, rows = samples.exports()["fonix_base"]
        rows = [r[:6] + ["99/x"] + r[7:] for r in rows[:1]]
        self.assertEqual(0, self.box.load("fonix_base", header, rows)["stats"]["acl_nobody"])
        self.assertEqual([], self.box.find("NY-1001", access("bv-intezet-99/x")))
        self.assertEqual(1, len(self.box.find("NY-1001", NATIONAL)))

    def test_the_folder_rule_applies_too_and_follows_its_changes(self):
        analysts = {"restricted": True, "allow": ["group:elemzok"], "deny": [], "classification": 0}
        header, rows = samples.exports()["fonix_base"]
        self.box.load("fonix_base", header, rows, acl=analysts)
        self.assertEqual([], self.box.find("NY-1001", INST1))
        self.assertEqual(1, len(self.box.find("NY-1001", access("bv-intezet-01", "elemzok"))))
        self.assertEqual([], self.box.find("NY-1001", access("elemzok")))
        service.handle_post(self.box.state, "/graph/acl/update", {
            "corpus_id": "c1", "entries": [{"path": FOLDER, "acl": None}]})
        self.assertEqual(1, len(self.box.find("NY-1001", INST1)))
        # Deleting the folder removes its records.
        service.handle_post(self.box.state, "/graph/document/drop_tree",
                            {"corpus_id": "c1", "path": FOLDER})
        self.assertEqual([], self.box.find("NY-1001", NATIONAL))


@needs_samples
class StructuredLeakageTest(SaltedTest):
    """Differential, like leakage_test: an institution-01 analyst gets byte for byte
    what a graph imported from the institution-01 records alone answers."""

    HIDDEN_FIRST = [  # a hidden record holding both a visible person's key and another's phone
        ["H1", "NY-1006", "Varga Péter", "Kovács Éva", "Tata", "1966-06-06", "Bán Irén", "ANYA",
         "+36 30 444 4444", "", "", "", "", "02", ""],
        ["V1", "NY-1001", "Sas Tibor", "Kovács Éva", "Tata", "1966-06-06", "Bán Irén", "ISM",
         "", "", "", "", "", "01", ""],
        ["V2", "NY-1003", "Major Ede", "Kovács Éva", "", "", "", "ISM", "+36 30 444 4444", "",
         "", "", "", "01", ""],
    ]

    def build(self, visible_only):
        box = Box()

        def only(source, header, row):
            return not visible_only or row[header.index("Intézet kód")] == "01"
        box.load_samples(only)
        header = samples.CONTACTS
        box.load("contacts", header, [r for r in self.HIDDEN_FIRST
                                      if only("contacts", header, r)], as_of="2026-11-02")
        return box

    def requests(self, box):
        ids = sorted(e.id for e in box.state.store.entities.values())
        out = [("/query/find", {"text": t}) for t in (
            "Kerekes", "Nagy Erzsébet", "Fekete Judit", "Kovács Éva", "NY-1001", "NY-1002",
            "Sas", "102")]
        for eid in ids:
            out += [("/query/entity", {"id": eid}), ("/query/copresence", {"id": eid}),
                    ("/query/timeline", {"id": eid}), ("/query/neighborhood", {"id": eid,
                                                                             "hops": 3})]
            for shared in (False, True):
                out.append(("/query/investigate", {"seeds": [eid], "levels": 4,
                                                   "relations": CONTACT,
                                                   "shared_contact": shared}))
        out += [("/query/shared", {}), ("/query/ranking", {"types": ["person", "place"]}),
                ("/query/communities", {}), ("/review/list", {}), ("/review/history", {})]
        return out

    def test_an_institution_analyst_sees_exactly_the_graph_of_their_records(self):
        full, oracle = self.build(False), self.build(True)
        leaks = []
        for path, body in self.requests(full):
            got = answer(full.state, path, body, INST1, ["c1"])
            want = answer(oracle.state, path, body, INST1, ["c1"])
            if got != want:
                leaks.append(path)
                if len(leaks) < 3:
                    print("\nLEAK %s %s\n  got:  %s" % (path, body, first_difference(got, want)))
        self.assertEqual([], leaks)
        # The hidden record linked by key the visible Kovács Éva (V1) and, by the
        # phone, suggested V2; neither the merge's data nor the pair shows.
        self.assertEqual(2, len(full.find("Kovács Éva", INST1)))
        self.assertEqual(1, len([i for i in full.q("/review/list", {}, NATIONAL)["review"]
                                 if i["a_label"] == "Kovács Éva"]))
        # The full graph does differ for the national group (the check has teeth).
        self.assertNotEqual(answer(full.state, "/query/find", {"text": "Kerekes"}, NATIONAL,
                                   ["c1"]),
                            answer(oracle.state, "/query/find", {"text": "Kerekes"}, NATIONAL,
                                   ["c1"]))


FEFO = access("bv-orszagos", "bv-fefo")
INFORMANTS = ("Rigó Lajos", "Fehér Gábor", "Hollós Péter")


def entry_id(box, number, who=NATIONAL):
    [entry] = box.q("/query/find", {"text": number, "types": ["event"]}, who)["entities"]
    return entry["id"]


@needs_samples
class InformantProtectionTest(SaltedTest):
    """E29: who gave a piece of intelligence is seen by the FEFO only."""

    def setUp(self):
        super().setUp()
        self.box = Box().load_samples()

    def test_only_the_fefo_sees_who_gave_the_information(self):
        for name in INFORMANTS:
            self.assertEqual([], self.box.find(name, NATIONAL), name)
            self.assertEqual(1, len(self.box.find(name, FEFO)), name)
        eid = entry_id(self.box, "2026/14/1")
        open_ = self.box.q("/query/entity", {"id": eid}, NATIONAL)
        self.assertEqual(["INVOLVED_IN"], [e["type"] for e in open_["edges"]])
        quote = open_["edges"][0]["evidence"][0]["quote"]
        self.assertIn("kábítószert", quote)
        for hidden in ("Forrás", "FOGV", "fogvatartott", "NY-1005"):
            self.assertNotIn(hidden, json.dumps(open_, ensure_ascii=False))
        fefo = self.box.q("/query/entity", {"id": eid}, FEFO)
        [provided] = [e for e in fefo["edges"] if e["type"] == "PROVIDED_INFO"]
        self.assertEqual("fogvatartott", provided["raw_label"])
        self.assertIn("Forrás neve: Bodó Ákos", provided["evidence"][0]["quote"])
        # Two rows of one entry number are one entry with both subjects.
        both = self.box.q("/query/entity", {"id": entry_id(self.box, "2026/14/3")}, NATIONAL)
        self.assertEqual(2, len([e for e in both["edges"] if e["type"] == "INVOLVED_IN"]))

    def test_membership_must_be_known_and_is_decided_by_the_profile_now(self):
        self.assertEqual([], self.box.find("Rigó Lajos", dict(FEFO, membership_known=False)))
        role = dict(NATIONAL, principals=["user:x", "role:fefo"])
        self.assertEqual(1, len(self.box.find("Rigó Lajos", role)))
        acl_off = service.handle_post(self.box.state, "/query/find", {
            "text": "Rigó Lajos", "types": ["person"], "corpus_ids": ["c1"]})
        self.assertEqual([], acl_off["entities"])
        self.assertEqual(1, len(self.box.find("Sas Tibor", NATIONAL)))
        prof = copy.deepcopy(self.box.state.profile)
        prof["protected_principals"] = ["group:fefo-uj"]
        self.box.state.profile = prof
        self.assertEqual([], self.box.find("Rigó Lajos", FEFO))
        self.assertEqual(1, len(self.box.find("Rigó Lajos", access("bv-orszagos", "fefo-uj"))))

    def test_a_protected_registry_number_links_nobody_and_is_a_review_for_the_fefo(self):
        [inmate] = self.box.find("Bodó Ákos", NATIONAL)
        before = [answer(self.box.state, p, b, NATIONAL, ["c1"]) for p, b in (
            ("/query/neighborhood", {"id": inmate["id"], "hops": 3}),
            ("/query/find", {"text": "Bodó"}), ("/review/list", {}), ("/review/history", {}))]
        self.assertEqual(2, len(self.box.find("Bodó Ákos", FEFO)))
        pairs = [i for i in self.box.q("/review/list", {}, FEFO)["review"]
                 if "Bodó Ákos" in (i["a_label"], i["b_label"])]
        self.assertEqual(["same registry_number"], [i["reason"] for i in pairs])
        self.assertEqual(inmate["id"], pairs[0]["b"])
        self.box.q("/review/decide", {"a": pairs[0]["a"], "b": pairs[0]["b"],
                                      "decision": "merge"}, FEFO)
        after = [answer(self.box.state, p, b, NATIONAL, ["c1"]) for p, b in (
            ("/query/neighborhood", {"id": inmate["id"], "hops": 3}),
            ("/query/find", {"text": "Bodó"}), ("/review/list", {}), ("/review/history", {}))]
        self.assertEqual(before, after)
        [merged] = self.box.find("Bodó Ákos", FEFO)
        edges = self.box.q("/query/entity", {"id": merged["id"]}, FEFO)["edges"]
        self.assertEqual(2, len([e for e in edges if e["type"] == "PROVIDED_INFO"]))
        # The next export keeps the FEFO's merge.
        header, rows = samples.exports()["intel_log"]
        self.box.load("intel_log", header, rows, as_of="2026-11-02")
        self.assertEqual(1, len(self.box.find("Bodó Ákos", FEFO)))

    def test_a_protected_relation_from_an_open_document_is_hidden_too(self):
        from model import Document, Entity, Evidence, Mention, Relation
        self.box.state.store.put_document(
            Document("d1", "c1", FOLDER + "/jelentes.txt", "jelentés"),
            [Entity("P1", "person", "", "Kiss Béla"),
             Entity("E1", "event", "intel_report", "2026/1/1")],
            [Mention("m1", "d1", "P1", "Kiss Béla"), Mention("m2", "d1", "E1", "2026/1/1")],
            [Relation("r1", "PROVIDED_INFO", "P1", "E1",
                      evidence=[Evidence("d1", "Kiss Béla információt adott")])])
        self.assertEqual([], self.box.q("/query/entity", {"id": "P1"}, NATIONAL)["edges"])
        self.assertEqual(1, len(self.box.q("/query/entity", {"id": "P1"}, FEFO)["edges"]))

    def test_the_protected_part_follows_its_record(self):
        header, rows = samples.exports()["intel_log"]
        stats = self.box.load("intel_log", header, rows)["stats"]
        self.assertEqual((len(rows), 0), (stats["unchanged"], stats["protected"]))
        dele = header.index("Törölve")
        rows = [r[:dele] + (["igen"] if r[0] == "N-0006" else [""]) for r in rows]
        stats = self.box.load("intel_log", header, rows, as_of="2026-11-02")["stats"]
        self.assertEqual(1, stats["removed"])
        self.assertEqual([], self.box.find("Hollós Péter", FEFO))
        # The informant left out of a later export: the entry stays, the informant goes.
        first = header.index("Forrás neve")
        rows = [r[:first] + ["", "", ""] + r[first + 3:] if r[0] == "N-0005" else r
                for r in rows]
        stats = self.box.load("intel_log", header, rows, as_of="2026-11-03")["stats"]
        self.assertEqual(1, stats["updated"])
        self.assertEqual([], self.box.find("Fehér Gábor", FEFO))
        self.assertTrue(entry_id(self.box, "2026/14/4", FEFO))
        # An older export brings back neither.
        self.box.load("intel_log", *samples.exports()["intel_log"], as_of="2026-11-01")
        self.assertEqual([], self.box.find("Hollós Péter", FEFO))
        self.assertEqual([], self.box.find("Fehér Gábor", FEFO))
        # The folder's rule reaches the protected part as well.
        service.handle_post(self.box.state, "/graph/acl/update", {"corpus_id": "c1", "entries": [
            {"path": FOLDER, "acl": {"restricted": True, "allow": ["group:mas"], "deny": [],
                                     "classification": 0}}]})
        self.assertEqual([], self.box.find("Rigó Lajos", FEFO))
        self.assertEqual(1, len(self.box.find("Rigó Lajos", access("bv-fefo", "mas"))))


@needs_samples
class InformantLeakageTest(SaltedTest):
    """Differential (1.3): a non-FEFO analyst gets byte for byte what a graph of
    the same exports without any informant answers; the FEFO does not."""

    # An informant's registry number is an open subject's, under a longer name: the
    # protected record must not teach anyone that name (a later open record of it
    # would then be paired with the subject).
    LATER = [
        ["N-0101", "2026/15/1", "2026-07-01", "01", "Lukács Ottó", "NY-2001", "EGY", "Hír.",
         "Lukács Ottó Béla", "NY-2001", "FOGV", ""],
        ["N-0102", "2026/15/2", "2026-07-02", "01", "Lukács Ottó Béla", "", "EGY", "Hír.",
         "", "", "", ""],
    ]

    def build(self, informants):
        box = Box()
        exports = samples.exports()
        for source, (header, rows) in exports.items():
            if source == "intel_log" and not informants:
                drop = {header.index(c) for c in samples.INFORMANT}
                rows = [["" if k in drop else v for k, v in enumerate(r)] for r in rows]
            box.load(source, header, rows)
        header = exports["intel_log"][0]
        for n, row in enumerate(self.LATER):
            if not informants:
                drop = {header.index(c) for c in samples.INFORMANT}
                row = ["" if k in drop else v for k, v in enumerate(row)]
            box.load("intel_log", header, [row], as_of="2026-11-0%d" % (n + 2))
            if n == 0:
                # The FEFO merges the informant into the subject; then a restart
                # rebuilds the resolver from the store.
                for item in box.q("/review/list", {}, FEFO)["review"]:
                    if item["b_label"] == "Lukács Ottó":
                        box.q("/review/decide", {"a": item["a"], "b": item["b"],
                                                 "decision": "merge"}, FEFO)
                worker = box.state.worker
                worker.resolver.rebuild(worker._mirror())
        return box

    def requests(self, box):
        ids = sorted(e.id for e in box.state.store.entities.values())
        people = sorted(e.id for e in box.state.store.entities.values() if e.type == "person")
        rels = ["CONTACT_OF", "INVOLVED_IN", "PROVIDED_INFO"]
        out = [("/query/find", {"text": t}) for t in (
            "Bodó", "Rigó", "Fehér", "Hollós", "2026/14", "Sas", "NY-1005", "Lukács", "NY-2001")]
        for eid in ids:
            out += [("/query/entity", {"id": eid}), ("/query/timeline", {"id": eid}),
                    ("/query/neighborhood", {"id": eid, "hops": 3}),
                    ("/query/copresence", {"id": eid}),
                    ("/query/investigate", {"seeds": [eid], "levels": 3, "relations": rels})]
        for a in people:
            for b in people:
                if a < b:
                    out.append(("/query/path", {"a": a, "b": b}))
        for group in ("focus", "other", "month", "relation", "other_kind"):
            out += [("/query/aggregate", {"relations": [r], "group_by": group}) for r in rels]
        out += [("/query/shared", {}), ("/query/ranking", {"types": ["person", "event"]}),
                ("/query/communities", {}), ("/query/mentioned", {"text": "Bodó Ákos Rigó Lajos"}),
                ("/review/list", {}), ("/review/history", {})]
        return out

    def test_a_non_member_sees_exactly_the_graph_without_informants(self):
        full, oracle = self.build(True), self.build(False)
        requests = self.requests(full)
        for who in (NATIONAL, INST1, dict(FEFO, membership_known=False)):
            leaks = []
            for path, body in requests:
                got = answer(full.state, path, body, who, ["c1"])
                want = answer(oracle.state, path, body, who, ["c1"])
                if got != want:
                    leaks.append(path)
                    if len(leaks) < 3:
                        print("\nLEAK %s %s\n  got:  %s" % (path, body,
                                                            first_difference(got, want)))
            self.assertEqual([], leaks, who)
        # Negative control: the FEFO sees the informants there.
        for path, body in (("/query/find", {"text": "Rigó"}),
                           ("/query/aggregate", {"relations": ["PROVIDED_INFO"]})):
            self.assertNotEqual(answer(full.state, path, body, FEFO, ["c1"]),
                                answer(oracle.state, path, body, FEFO, ["c1"]))


class PersonKeyTest(unittest.TestCase):
    def test_the_key_folds_spelling_and_needs_all_four_fields(self):
        import person_key as pk
        salt = b"s" * 32
        key = pk.person_key(salt, "Kerekes Ilona", "Szeged", "1962-04-11", "Bodnár Rozália")
        self.assertEqual(64, len(key))
        for variant in (("ILONA KEREKES", "szeged", "1962. 04. 11.", "Bodnar  Rozalia"),
                        ("Dr. Kerekes Ilona", "Szeged.", "1962/4/11", "Bodnár Rozália")):
            self.assertEqual(key, pk.person_key(salt, *variant))
        self.assertNotEqual(key, pk.person_key(salt, "ifj. Kerekes Ilona", "Szeged",
                                               "1962-04-11", "Bodnár Rozália"))
        self.assertNotEqual(key, pk.person_key(b"t" * 32, "Kerekes Ilona", "Szeged",
                                               "1962-04-11", "Bodnár Rozália"))
        self.assertEqual("", pk.person_key(salt, "Kerekes Ilona", "", "1962-04-11", "B R"))
        self.assertEqual("", pk.person_key(salt, "Kerekes Ilona", "Szeged", "1962-13-11", "B R"))
        self.assertEqual("", pk.person_key(None, "Kerekes Ilona", "Szeged", "1962-04-11", "B R"))
        with tempfile.TemporaryDirectory() as tmp:
            short = os.path.join(tmp, "s")
            with open(short, "w") as f:
                f.write("too short")
            self.assertIsNone(pk.load_salt(short))
            self.assertIsNone(pk.load_salt(os.path.join(tmp, "missing")))


class ProfileValidationTest(unittest.TestCase):
    def source(self, **change):
        prof = copy.deepcopy(domain_profile.load("bv"))
        src = prof["structured_sources"][2]
        src.update(change)
        return prof

    def test_bad_sources_reject_the_profile(self):
        bad = [
            {"record_id": []},
            {"mode": "snapshot"},
            {"entities": [{"ref": "x", "type": "person", "name": "N", "person_key": True}]},
            {"relations": [{"type": "CONTACT_OF", "from": "contact", "to": "nobody"}]},
            {"relations": [{"type": "CO_MENTIONED", "from": "contact", "to": "inmate"}]},
            {"acl": {"pattern": "group:x-{value}"}},
            {"acl": {"column": "Intézet kód", "always": ["everyone"]}},
            {"codelists": {"Bad Name": {}}},
            {"entities": [{"ref": "cell", "type": "place", "name": "Z", "within": "cell"}]},
        ]
        for change in bad:
            with self.assertRaises(domain_profile.ProfileError, msg=change):
                domain_profile.validate(self.source(**change))

    def test_a_protected_part_must_really_hide_its_columns(self):
        def intel(protected=None, **top):
            prof = copy.deepcopy(domain_profile.load("bv"))
            src = next(s for s in prof["structured_sources"] if s["id"] == "intel_log")
            if protected is not None:
                src["protected"] = protected
            prof.update(top)
            return prof
        domain_profile.validate(intel())
        bad = [
            intel(protected_principals=None),
            intel(protected_principals=["user:fefo"]),
            # the open INVOLVED_IN reads Dátum
            intel({"entities": ["informant"], "columns": ["Dátum"]}),
            # PROVIDED_INFO would be open
            intel({"columns": ["Megjegyzés"]}),
            intel({"entities": ["entry", "institution", "subject", "informant"]}),
            intel({"entities": ["nobody"]}),
        ]
        for prof in bad:
            if prof.get("protected_principals") is None:
                prof.pop("protected_principals", None)
            with self.assertRaises(domain_profile.ProfileError):
                domain_profile.validate(prof)
        seen = domain_profile.for_extraction(domain_profile.load("bv"))
        self.assertNotIn("PROVIDED_INFO", [r["id"] for r in seen["relation_types"]])

    def test_structured_only_items_stay_out_of_extraction(self):
        prof = domain_profile.load("bv")
        seen = domain_profile.for_extraction(prof)
        self.assertNotIn("CONTACT_OF", [r["id"] for r in seen["relation_types"]])
        self.assertNotIn("contact", [s["id"] for s in seen["entity_subtypes"]])
        self.assertIn("CONTACT_OF", [r["id"] for r in prof["relation_types"]])
        generic = domain_profile.load("generic")
        self.assertIs(generic, domain_profile.for_extraction(generic))


if __name__ == "__main__":
    unittest.main()
