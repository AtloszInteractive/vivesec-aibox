"""The review list shows what tells two namesakes apart, and leaves out the
pairs the namesake rule already calls two people (E26)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import domain_profile  # noqa: E402
import service  # noqa: E402
from graph_store import MemoryGraphStore  # noqa: E402
from ingest_jobs import IngestWorker  # noqa: E402
from ingest_jobs_test import FakeLLM  # noqa: E402
from model import AttributeValue, Document, Entity, Evidence, Mention, Relation  # noqa: E402

SCOPE = {"corpus_ids": ["c1"]}


def person(doc_id, eid, name, subtype, **attrs):
    return Entity(eid, "person", subtype, name,
                  [AttributeValue(k, v, doc_id) for k, v in attrs.items()])


class ReviewCardsTest(unittest.TestCase):
    def setUp(self):
        store = MemoryGraphStore()
        phone = Entity("id:phone:+3630", "identifier", "phone", "+36 30")
        cell = Entity("place:zarka", "place", "cell", "102. zárka")
        store.put_document(
            Document("d1", "c1", "/c1/elhelyezes.txt", "Elhelyezési lista"),
            [person("d1", "person:a", "Kiss Anna", "inmate", birth_date="1990-01-01"),
             phone, cell],
            [Mention("m1", "d1", "person:a", "Kiss Anna"),
             Mention("mp", "d1", "id:phone:+3630", "+36 30"),
             Mention("mz", "d1", "place:zarka", "102. zárka")],
            [Relation("r1", "USES", "person:a", "id:phone:+3630",
                      evidence=[Evidence("d1", "Hívószám: +36 30")]),
             Relation("r2", "PLACED_IN", "person:a", "place:zarka",
                      evidence=[Evidence("d1", "Kiss Anna a 102. zárkában")])])
        store.put_document(
            Document("d2", "c1", "/c1/latogatas.txt"),
            [person("d2", "person:b", "Kiss Anna", "inmate", birth_date="1985-05-05",
                    mother_name="Nagy Éva"), Entity("id:phone:+3630", "identifier", "phone")],
            [Mention("m2", "d2", "person:b", "Kiss Anna")],
            [Relation("r3", "USES", "person:b", "id:phone:+3630",
                      evidence=[Evidence("d2", "Telefon: +36 30")])])
        store.put_document(
            Document("d3", "c1", "/c1/latogatok.txt"),
            [person("d3", "person:c", "Kiss Anna", "visitor")],
            [Mention("m3", "d3", "person:c", "Kiss Anna")], [])
        store.put_document(
            Document("d4", "c2", "/c2/titkos.txt"),
            [person("d4", "person:a", "Kiss Anna", "inmate", mother_name="Titkos Mária")],
            [Mention("m4", "d4", "person:a", "Kiss Anna")], [])
        self.state = service.State(store, domain_profile.load("bv"))
        self.state.worker = IngestWorker(self.state, FakeLLM())
        self.state.worker.resolver.review.extend([
            {"a": "person:b", "b": "person:a", "score": 0.75, "reason": "same name",
             "doc_id": "d2", "names": ["Kiss Anna"]},
            {"a": "person:c", "b": "person:a", "score": 0.75, "reason": "same name",
             "doc_id": "d3", "names": ["Kiss Anna"]},
            {"a": "person:c", "b": "person:b", "score": 0.75,
             "reason": "same name + shared contact data", "doc_id": "d3", "names": ["Kiss Anna"]},
        ])

    def post(self, body):
        return service.handle_post(self.state, "/review/list", dict(SCOPE, **body))

    def test_role_mismatch_on_the_name_alone_is_counted_not_listed(self):
        out = self.post({})
        pairs = [(i["a"], i["b"]) for i in out["review"]]
        # The visitor-inmate pair with only the name is out; with a shared
        # contact it stays, and comes first (more than the name links it).
        self.assertEqual([("person:c", "person:b"), ("person:b", "person:a")], pairs)
        self.assertEqual((2, 2, 1), (out["total"], out["open_total"], out["role_mismatch"]))
        self.assertEqual({"same name": 1, "same name + shared contact data": 1}, out["reasons"])
        everything = self.post({"include_role_mismatch": True})
        self.assertEqual(3, everything["total"])
        # Still decidable when shown.
        service.handle_post(self.state, "/review/decide",
                            dict(SCOPE, a="person:c", b="person:a", decision="separate"))

    def test_each_side_is_described_and_compared(self):
        [item] = [i for i in self.post({})["review"] if i["a"] == "person:b"]
        a, b = item["a_info"], item["b_info"]
        self.assertEqual(("inmate", "inmate"), (a["subtype"], b["subtype"]))
        self.assertEqual(["Elhelyezési lista"], [d["name"] for d in b["documents"]])
        self.assertEqual(["latogatas.txt"], [d["name"] for d in a["documents"]])
        self.assertEqual(["+36 30"], [x["label"] for x in b["identifiers"]])
        [link] = b["links"]
        self.assertEqual(("PLACED_IN", "102. zárka", "Kiss Anna a 102. zárkában"),
                         (link["type"], link["other"], link["quote"]))
        fields = {f["name"]: (f["state"], f["a"], f["b"]) for f in item["fields"]}
        self.assertEqual({"birth_date": ("conflict", ["1985-05-05"], ["1990-01-01"]),
                          "mother_name": ("a_only", ["Nagy Éva"], []),
                          "identifiers": ("same", ["+36 30"], ["+36 30"])}, fields)

    def test_a_hidden_document_says_nothing(self):
        dumped = repr(self.post({"include_role_mismatch": True}))
        self.assertNotIn("Titkos", dumped)
        self.assertNotIn("titkos.txt", dumped)


if __name__ == "__main__":
    unittest.main()
