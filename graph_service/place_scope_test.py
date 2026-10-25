"""A place that only exists inside a larger one (a cell inside an institution):
cell 102 of two prisons are two places, and a cell with no institution named
stays its document's own (E26 item 9)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import domain_profile  # noqa: E402
import pipeline  # noqa: E402
from extraction_test import FakeLLM, answer  # noqa: E402
from graph_store import MemoryGraphStore  # noqa: E402
from ingest import parse_document_payload  # noqa: E402
from resolution import Resolver  # noqa: E402
from visibility import Scope  # noqa: E402

PROFILE = domain_profile.load("bv")
CELL = {"ref": "z", "type": "place", "subtype": "cell", "names": ["102. számú zárka"]}


def inst(ref, name):
    return {"ref": ref, "type": "place", "subtype": "institution", "names": [name]}


def person(ref, name):
    return {"ref": ref, "type": "person", "names": [name]}


DOCS = {
    # incident report: "Helyszín: <institution>, <cell>"
    "d1": ("Jelentés rendkívüli eseményről\nHelyszín: Kőhegyi Intézet, 102. számú zárka\n"
           "A 102. számú zárka ellenőrzése során Balogh György kiabált.",
           answer([inst("i", "Kőhegyi Intézet"), CELL, person("p", "Balogh György")],
                  [{"type": "LOCATED_AT", "from": "p", "to": "z",
                    "quote": "A 102. számú zárka ellenőrzése során Balogh György kiabált."}])),
    # admission sheet: one institution in the document
    "d2": ("Befogadási adatlap\nNév: Kiss Péter\nIntézet: Alsóréti Börtön\n"
           "Elhelyezés: 102. számú zárka\nMegjegyzés: nincs.",
           answer([inst("i", "Alsóréti Börtön"), CELL, person("p", "Kiss Péter")],
                  [{"type": "PLACED_IN", "from": "p", "to": "z",
                    "quote": "Elhelyezés: 102. számú zárka"}])),
    # transfer decision: two institutions, the cell is in the receiving one
    "d3": ("Határozat átszállításról\nNagy Ádám elítéltet az Alsóréti Börtön intézetből a "
           "Kőhegyi Intézet intézetbe szállítom át.\n"
           "Elhelyezése a befogadó intézetben: 102. számú zárka.",
           answer([inst("a", "Alsóréti Börtön"), inst("b", "Kőhegyi Intézet"), CELL,
                   person("p", "Nagy Ádám")],
                  [{"type": "PLACED_IN", "from": "p", "to": "z",
                    "quote": "Elhelyezése a befogadó intézetben: 102. számú zárka."}])),
    # placement list without any institution
    "d4": ("Elhelyezési kimutatás\nZárka: 102. számú zárka; Név: Tóth Éva; kezdete: 2025-01-01",
           answer([CELL, person("p", "Tóth Éva")],
                  [{"type": "PLACED_IN", "from": "p", "to": "z",
                    "quote": "Zárka: 102. számú zárka; Név: Tóth Éva"}])),
}


class PlaceScopeTest(unittest.TestCase):
    def setUp(self):
        self.store = MemoryGraphStore()
        resolver = Resolver(PROFILE)
        for doc_id, (text, reply) in DOCS.items():
            meta = {"doc_id": doc_id, "corpus_id": "c1", "source_path": "/c1/%s.txt" % doc_id}
            payload, _ = pipeline.extract_document(text.encode("utf-8"), meta, PROFILE,
                                                   FakeLLM(reply), resolver)
            self.store.put_document(*parse_document_payload(payload, PROFILE))

    def cell_of(self, doc_id):
        [cell] = {m.entity_id for m in self.store.mentions.values()
                  if m.doc_id == doc_id and m.entity_id.startswith("place:102")}
        return cell

    def test_the_same_cell_number_in_two_institutions_is_two_places(self):
        self.assertEqual(self.cell_of("d1"), self.cell_of("d3"))
        self.assertNotEqual(self.cell_of("d1"), self.cell_of("d2"))
        self.assertTrue(self.cell_of("d1").endswith("@place:kohegyi intezet"))
        self.assertEqual("place:102. szamu zarka@doc:d4", self.cell_of("d4"))
        # Nobody of another prison (or of the list without one) shares Balogh's cell.
        people = {r.a for r in self.store.relations.values()
                  if r.type in ("PLACED_IN", "LOCATED_AT") and r.b == self.cell_of("d1")}
        self.assertEqual(2, len(people))

    def test_the_container_is_linked_with_the_text_between_them(self):
        quotes = {r.a: (r.b, [ev.quote for ev in r.evidence])
                  for r in self.store.relations.values() if r.type == "PART_OF"}
        self.assertEqual(("place:kohegyi intezet",
                          ["Kőhegyi Intézet intézetbe szállítom át. Elhelyezése a befogadó "
                           "intézetben: 102. számú zárka",
                           "Kőhegyi Intézet, 102. számú zárka"]),
                         (quotes[self.cell_of("d1")][0], sorted(quotes[self.cell_of("d1")][1])))
        self.assertEqual(["Alsóréti Börtön Elhelyezés: 102. számú zárka"],
                         quotes[self.cell_of("d2")][1])
        self.assertNotIn(self.cell_of("d4"), quotes)

    def test_the_label_names_the_container_from_visible_documents_only(self):
        view = self.store.view(Scope(["c1"]))
        self.assertEqual("102. számú zárka (Kőhegyi Intézet)", view.node(self.cell_of("d1"))["label"])
        self.assertEqual("102. számú zárka", view.node(self.cell_of("d4"))["label"])

    def test_an_explicit_part_of_wins_over_the_nearest_institution(self):
        text = ("Az Alsóréti Börtön 102. számú zárka része a Kőhegyi Intézet épületének? Nem: "
                "a 102. számú zárka az Alsóréti Börtön része.")
        reply = answer([inst("a", "Alsóréti Börtön"), inst("b", "Kőhegyi Intézet"), CELL],
                       [{"type": "PART_OF", "from": "z", "to": "a",
                         "quote": "a 102. számú zárka az Alsóréti Börtön része."}])
        meta = {"doc_id": "d5", "corpus_id": "c1", "source_path": "/c1/d5.txt"}
        payload, _ = pipeline.extract_document(text.encode("utf-8"), meta, PROFILE,
                                               FakeLLM(reply), Resolver(PROFILE))
        cells = {e["id"] for e in payload["entities"] if e["subtype"] == "cell"}
        self.assertEqual({"place:102. szamu zarka@place:alsoreti borton"}, cells)
        self.assertEqual(1, sum(r["type"] == "PART_OF" for r in payload["relations"]))


class WithinValidationTest(unittest.TestCase):
    def profile(self, within, kind="place"):
        raw = {"id": "t", "version": 1, "name": {"en": "T"}, "entity_subtypes": [
            {"id": "site", "type": "place", "label": {"en": "Site"}},
            {"id": "badge", "type": "identifier", "label": {"en": "Badge"}},
            {"id": "room", "type": kind, "label": {"en": "Room"}, "within": within}],
            "relation_types": []}
        return domain_profile.validate(raw)

    def test_within_names_other_place_or_organization_subtypes(self):
        self.assertEqual(["site"], self.profile(["site"])["entity_subtypes"][2]["within"])
        for bad in (["nope"], ["room"], ["badge"], "site", []):
            with self.assertRaises(domain_profile.ProfileError):
                self.profile(bad)
        with self.assertRaises(domain_profile.ProfileError):
            self.profile(["site"], kind="person")


if __name__ == "__main__":
    unittest.main()
