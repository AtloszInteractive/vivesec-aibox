"""The BV demo exports fit the bv profile and the documents' world (bv_demo.py)."""
import base64
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
GRAPH = os.path.join(os.path.dirname(HERE), "graph_service")
sys.path.insert(0, HERE)
sys.path.append(os.path.join(os.path.dirname(HERE), "demo-corpus"))
sys.path.insert(1, GRAPH)

import bv_demo  # noqa: E402
import structured_samples as samples  # noqa: E402
# At import time: another test of this folder puts adapter/ (its own `service`) first.
import domain_profile  # noqa: E402
import service  # noqa: E402
from graph_store import MemoryGraphStore  # noqa: E402
from ingest_jobs import IngestWorker  # noqa: E402
from ingest_jobs_test import FakeLLM  # noqa: E402

TINY = {"persons": 80, "background": 300, "intel": 0.4, "odin": 0.23}

with open(os.path.join(GRAPH, "profiles", "bv.json"), encoding="utf-8") as f:
    PROFILE = json.load(f)
SOURCES = {s["id"]: s for s in PROFILE["structured_sources"]}


def columns_of(source):
    cols = {source["record_id"], source["deleted"]["column"], source["acl"]["column"]}
    for item in source.get("entities", []):
        cols.add(item["name"])
        cols.update(item.get("attributes", {}).values())
    for rel in source.get("relations", []):
        for key in ("start", "end", "label"):
            value = rel.get(key)
            cols.update(value if isinstance(value, list) else [value] if value else [])
    cols.update(source.get("text_columns", []))
    return cols


class BvDemoTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.w, cls.reg = bv_demo.build(TINY, seed=3)
        cls.headers = {s: h for s, (h, _) in samples.exports().items()}

    def table(self, source):
        header = self.headers[source]
        return [dict(zip(header, row)) for row in self.reg.rows[source]]

    def test_every_mapped_column_and_code_is_in_the_profile(self):
        self.assertEqual(set(SOURCES), set(self.reg.rows))
        for sid, source in SOURCES.items():
            header = self.headers[sid]
            self.assertLessEqual(columns_of(source), set(header), sid)
            for row in self.table(sid):
                self.assertEqual(len(header), len(row))
                for item in source.get("entities", []):
                    if item.get("codelist"):
                        self.assertIn(row[item["name"]], source["codelists"][item["codelist"]])
                for rel in source.get("relations", []):
                    labels = rel.get("label")
                    for col in labels if isinstance(labels, list) else [labels] if labels else []:
                        if rel.get("label_codelist") and row[col]:
                            self.assertIn(row[col], source["codelists"][rel["label_codelist"]],
                                          (sid, col))
        self.assertEqual(bv_demo.INSTITUTIONS, SOURCES["placement"]["codelists"]["intezet"])

    def test_records_name_the_register_and_the_documents_world(self):
        base = {r["Nyilvántartási szám"]: r["Név"] for r in self.table("fonix_base")}
        for sid, reg_col, name_col in (
                ("placement", "Nyilvántartási szám", "Fogvatartott neve"),
                ("contacts", "Fogvatartott nyilvántartási száma", "Fogvatartott neve"),
                ("intel_log", "Érintett nyilvántartási száma", "Érintett neve"),
                ("odin", "Érintett nyilvántartási száma", "Érintett neve")):
            for row in self.table(sid):
                self.assertEqual(base.get(row[reg_col]), row[name_col], sid)
        for p in self.w.persons.values():
            if p.registry_number:
                self.assertEqual(base[p.registry_number], p.name)
        self.assertEqual(len(base), len(self.table("fonix_base")))
        self.assertTrue(any(r["Forrás típusa"] == "FOGV" for r in self.table("intel_log")))

    def test_the_graph_imports_every_export_cleanly(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        salt = os.path.join(tmp, "salt")
        with open(salt, "w") as f:
            f.write("s" * 32)
        old = os.environ.get("GRAPH_PERSON_KEY_SALT_FILE")
        os.environ["GRAPH_PERSON_KEY_SALT_FILE"] = salt
        self.addCleanup(lambda: os.environ.pop("GRAPH_PERSON_KEY_SALT_FILE", None) if old is None
                        else os.environ.__setitem__("GRAPH_PERSON_KEY_SALT_FILE", old))
        state = service.State(MemoryGraphStore(), domain_profile.load("bv"))
        state.worker = IngestWorker(state, FakeLLM())
        for sid in ("fonix_base", "placement", "contacts", "intel_log", "odin"):
            content = samples.to_csv(self.headers[sid], self.reg.rows[sid])
            out = service.handle_post(state, "/import/records", {
                "source": sid, "as_of": bv_demo.AS_OF, "folder": "/storage/drives/r/x",
                "corpus_id": "c1", "filename": sid + ".csv",
                "content_b64": base64.b64encode(content).decode("ascii")})["import"]
            stats = out["stats"]
            self.assertEqual("done", out["status"], sid)
            self.assertEqual(len(self.reg.rows[sid]), stats["records"], sid)
            self.assertEqual((0, {}, []), (stats["problems"], stats["unknown_codes"],
                                           stats["missing_columns"]), sid)
        access = {"principals": ["user:x", "group:bv-orszagos"], "clearance": 0,
                  "membership_known": True, "default_classification": 0}
        mothers = [r for r in self.table("contacts") if r["Kapcsolat jellege"] == "ANYA"]
        shared = [m for m in mothers if sum(1 for o in mothers if o["Kapcsolattartó neve"] ==
                                            m["Kapcsolattartó neve"] and o["Születési idő"] ==
                                            m["Születési idő"]) > 1]
        self.assertTrue(shared, "the background has brothers with one mother")
        found = service.handle_post(state, "/query/find", {
            "text": shared[0]["Kapcsolattartó neve"], "types": ["person"], "corpus_ids": ["c1"],
            "access": access})["entities"]
        self.assertEqual(1, len([e for e in found if e["label"] == shared[0]["Kapcsolattartó neve"]]))


if __name__ == "__main__":
    unittest.main()
