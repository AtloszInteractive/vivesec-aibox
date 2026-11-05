"""The file or case numbers a document states, as document metadata of query results."""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import domain_profile  # noqa: E402
import service  # noqa: E402
from graph_store import MemoryGraphStore  # noqa: E402
from model import Document, Entity, Evidence, Mention, Relation  # noqa: E402
from visibility import Scope  # noqa: E402

SECRET = {"restricted": True, "allow": ["user:anna"], "deny": [], "classification": 0}
ANNA = {"principals": ["user:anna"], "clearance": 0, "membership_known": True,
        "default_classification": 0}
BELA = {"principals": ["user:bela"], "clearance": 0, "membership_known": True,
        "default_classification": 0}


def put(store, doc_id, mentions, rels, acl=None):
    """mentions: [(entity, text, page, start)]"""
    ents = list({e.id: e for e, _, _, _ in mentions}.values())
    store.put_document(
        Document(doc_id, "c1", "/a/%s.txt" % doc_id, acl=acl), ents,
        [Mention("%s:m%d" % (doc_id, i), doc_id, e.id, text, page, start, start + len(text),
                 "rule")
         for i, (e, text, page, start) in enumerate(mentions)], rels)


class DocumentRefsTest(unittest.TestCase):
    def setUp(self):
        self.store = MemoryGraphStore()
        person = Entity("P", "person", "inmate", "Deák Norbert")
        incident = Entity("E", "event", "incident", "verekedés")
        own = Entity("J1", "identifier", "case_number", "J-2025/0001")
        cited = Entity("J9", "identifier", "case_number", "J-2024/0099")
        phone = Entity("T", "identifier", "phone", "+36 30 111 1111")
        # The report's own number heads it; it also cites an earlier case.
        put(self.store, "d1", [(cited, "J-2024/0099", 1, 400), (person, "Deák Norbert", 1, 80),
                               (own, "J-2025/0001", 1, 10), (incident, "verekedés", 1, 120),
                               (phone, "+36 30 111 1111", 1, 200)],
            [Relation("x1", "INVOLVED_IN", "P", "E", "2025-03-01", None, "",
                      [Evidence("d1", "Deák Norbert verekedés")])])
        hidden = Entity("J2", "identifier", "case_number", "J-2025/0002")
        put(self.store, "d2", [(hidden, "J-2025/0002", 1, 10), (person, "Deák Norbert", 1, 50),
                               (incident, "verekedés", 1, 70)],
            [Relation("x2", "INVOLVED_IN", "P", "E", "2025-03-01", None, "",
                      [Evidence("d2", "Deák Norbert verekedés")])], acl=SECRET)

    def test_refs_in_reading_order_for_visible_documents_only(self):
        view = self.store.view(Scope(["c1"], ANNA))
        self.assertEqual({"d1": {"case_number": ["J-2025/0001", "J-2024/0099"]},
                          "d2": {"case_number": ["J-2025/0002"]}},
                         view.document_refs(["d1", "d2"], ["case_number"]))
        self.assertEqual({"d1": {"case_number": ["J-2025/0001"]}},
                         self.store.view(Scope(["c1"], BELA)).document_refs(
                             ["d1", "d2"], ["case_number"], limit=1))
        self.assertEqual({}, view.document_refs(["d1"], []))
        self.assertEqual({}, self.store.view(Scope([], ANNA)).document_refs(
            ["d1", "d2"], ["case_number"]))

    def test_a_query_returns_them_with_its_documents_when_asked(self):
        state = service.State(self.store, domain_profile.load("bv"))
        body = {"corpus_ids": ["c1"], "access": BELA, "id": "P"}
        out = service.handle_post(state, "/query/neighborhood", dict(body))
        self.assertNotIn("refs", out["documents"]["d1"])
        out = service.handle_post(state, "/query/neighborhood",
                                  dict(body, doc_refs=["case_number", 7]))
        json.dumps(out)
        self.assertEqual(["d1"], sorted(out["documents"]))
        self.assertEqual({"case_number": ["J-2025/0001", "J-2024/0099"]},
                         out["documents"]["d1"]["refs"])
        self.assertNotIn("J-2025/0002", json.dumps(out))


if __name__ == "__main__":
    unittest.main()
