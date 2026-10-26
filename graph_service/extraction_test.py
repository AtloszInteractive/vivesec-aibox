"""Extraction checks with a scripted model: nothing the text does not say gets in."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import domain_profile  # noqa: E402
import pipeline  # noqa: E402
from extraction import (Chunk, LocalEntity, chunk_pages, co_mentions, find_identifiers,  # noqa: E402
                        llm_extract)
from resolution import Resolver  # noqa: E402

PROFILE = domain_profile.load("bv")
TEXT = ("2026. 03. 12. 14:20 körül a Fő utca 5. címen Kovács János és Nagy Éva találkozott. "
        "Nevezett a +36 30 123 4567 hívószámról telefonált.")


class FakeLLM:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = 0
        self.seconds = 0.0

    def json_call(self, system, user, schema, max_tokens=4096, retries=1):
        self.calls += 1
        return self.answers.pop(0) if self.answers else {"entities": [], "relations": []}


def answer(entities, relations):
    return {"entities": entities, "relations": relations}


class LlmValidationTest(unittest.TestCase):
    def run_one(self, raw):
        return llm_extract(Chunk(1, 0, TEXT), PROFILE, FakeLLM(raw))

    def test_names_and_quotes_must_occur_in_the_text(self):
        ents, rels = self.run_one(answer(
            [{"ref": "a", "type": "person", "names": ["Kovács János", "Kovács Jani"]},
             {"ref": "b", "type": "person", "names": ["Nagy Éva"]},
             {"ref": "c", "type": "person", "names": ["Szabó Péter"]}],
            [{"type": "MET", "from": "a", "to": "b",
              "quote": "Kovács János és Nagy Éva találkozott.", "date": "2026-03-12"},
             {"type": "MET", "from": "a", "to": "b", "quote": "They met twice."}]))
        self.assertEqual([["Kovács János"], ["Nagy Éva"]], [e.names for e in ents])
        self.assertEqual(1, len(rels))
        self.assertEqual("2026-03-12", rels[0].start)

    def test_dates_not_in_the_text_are_dropped(self):
        _, rels = self.run_one(answer(
            [{"ref": "a", "type": "person", "names": ["Kovács János"]},
             {"ref": "b", "type": "person", "names": ["Nagy Éva"]}],
            [{"type": "MET", "from": "a", "to": "b", "date": "2025-01-01",
              "quote": "Kovács János és Nagy Éva találkozott."}]))
        self.assertIsNone(rels[0].start)

    def test_role_words_are_not_person_names(self):
        ents, _ = self.run_one(answer([{"ref": "a", "type": "person", "names": ["nevezett"]}], []))
        self.assertEqual([], ents)

    def test_reversed_endpoints_are_turned_around(self):
        _, rels = self.run_one(answer(
            [{"ref": "a", "type": "person", "names": ["Kovács János"]},
             {"ref": "p", "type": "place", "names": ["Fő utca 5."]}],
            [{"type": "LOCATED_AT", "from": "p", "to": "a",
              "quote": "a Fő utca 5. címen Kovács János és Nagy Éva találkozott."}]))
        self.assertEqual(("a", "p"), (rels[0].a, rels[0].b))

    def test_unknown_relation_type_is_ignored(self):
        _, rels = self.run_one(answer(
            [{"ref": "a", "type": "person", "names": ["Kovács János"]},
             {"ref": "b", "type": "person", "names": ["Nagy Éva"]}],
            [{"type": "BRIBED", "from": "a", "to": "b",
              "quote": "Kovács János és Nagy Éva találkozott."}]))
        self.assertEqual([], rels)


class CompactOutputTest(unittest.TestCase):
    """GRAPH_EXTRACT_COMPACT: the model is asked for minified JSON (on by default)."""

    def test_prompt_asks_for_minified_json_unless_switched_off(self):
        llm = SpyLLM()
        llm_extract(Chunk(1, 0, TEXT), PROFILE, llm)
        self.assertIn("minified", llm.system)
        llm_extract(Chunk(1, 0, TEXT), PROFILE, llm, compact=False)
        self.assertNotIn("minified", llm.system)
        self.assertIn(TEXT, llm.user)
        self.assertIn("quote", llm.schema["properties"]["relations"]["items"]["required"])


class SpyLLM(FakeLLM):
    def json_call(self, system, user, schema, max_tokens=4096, retries=1):
        self.system, self.user, self.schema = system, user, schema
        return super().json_call(system, user, schema, max_tokens, retries)


class RulesAndChunksTest(unittest.TestCase):
    def test_identifier_patterns_normalise(self):
        hits = find_identifiers([(1, TEXT)], PROFILE)
        self.assertIn(("phone", "+36 30 123 4567", "+36301234567"), [h[:3] for h in hits])

    def test_tables_are_chunked_with_the_header_repeated(self):
        rows = ["Dátum;Fogvatartott;Látogató"] + ["2026-01-%02d;A B;C D" % i for i in range(1, 31)]
        chunks = chunk_pages([(1, "\n".join(rows))])
        self.assertEqual(2, len(chunks))
        self.assertTrue(all(c.text.startswith("Dátum;") for c in chunks))

    def test_co_mentions_need_two_entities_in_one_sentence(self):
        rels = co_mentions([(1, "A B és C D jött. E F maradt.")],
                           {"x": [(1, 0, 3)], "y": [(1, 7, 10)], "z": [(1, 17, 20)]}, 12)
        self.assertEqual([("x", "y")], [(r.a, r.b) for r in rels])


class PipelineTest(unittest.TestCase):
    def test_document_payload_ingests_cleanly(self):
        llm = FakeLLM(answer(
            [{"ref": "a", "type": "person", "names": ["Kovács János"]},
             {"ref": "b", "type": "person", "names": ["Nagy Éva"]},
             {"ref": "t", "type": "identifier", "names": ["+36 30 123 4567"]}],
            [{"type": "MET", "from": "a", "to": "b", "quote": "Kovács János és Nagy Éva találkozott."},
             {"type": "USES", "from": "a", "to": "t",
              "quote": "Nevezett a +36 30 123 4567 hívószámról telefonált."}]))
        meta = {"doc_id": "d1", "corpus_id": "c1", "source_path": "/s/feljegyzes.txt"}
        payload, stats = pipeline.extract_document(TEXT.encode("utf-8"), meta, PROFILE, llm,
                                                   Resolver(PROFILE))
        from graph_store import MemoryGraphStore
        from ingest import parse_document_payload
        store = MemoryGraphStore()
        store.put_document(*parse_document_payload(payload, PROFILE))
        self.assertIn("id:phone:+36301234567", store.entities)
        self.assertEqual(2, stats["relations"])


def person(names, **attrs):
    return LocalEntity("r", "person", "", names, {k: (v, 1) for k, v in attrs.items()})


class ResolverTest(unittest.TestCase):
    def setUp(self):
        self.r = Resolver(PROFILE)
        self.first = self.r.resolve("d1", person(["Kovács János"], birth_date="1980-01-02"))

    def test_same_name_and_birth_date_merge(self):
        self.assertEqual(self.first, self.r.resolve("d2", person(["KOVÁCS JÁNOS"],
                                                                 birth_date="1980-01-02")))

    def test_same_name_other_birth_date_is_another_person(self):
        other = self.r.resolve("d2", person(["Kovács János"], birth_date="1990-05-05"))
        self.assertNotEqual(self.first, other)
        self.assertEqual([], self.r.review)

    def test_name_only_goes_to_review_not_merge(self):
        other = self.r.resolve("d3", person(["Kovacs Janos"]))
        self.assertNotEqual(self.first, other)
        self.assertEqual([(other, self.first)], [(x["a"], x["b"]) for x in self.r.review])

    def test_repeated_name_only_mentions_pair_with_the_settled_person(self):
        later = [self.r.resolve("d%d" % i, person(["Kovács János"])) for i in range(10, 15)]
        self.assertEqual(5, len(self.r.review))
        self.assertEqual({self.first}, {x["b"] for x in self.r.review})
        self.assertEqual(later, [x["a"] for x in self.r.review])

    def test_same_id_number_merges_even_with_a_typo(self):
        a = self.r.resolve("d4", person(["Tóth Ádám"], id_number="123456AB"))
        self.assertEqual(a, self.r.resolve("d5", person(["Tóht Ádám"], id_number="123456AB")))

    def test_held_id_card_links_the_same_name(self):
        card = frozenset({"id_card:499529TT"})
        a = self.r.resolve("d6", person(["Takács Bence"]), card)
        self.assertEqual(a, self.r.resolve("d7", person(["TAKÁCS BENCE"]), card))

    def test_held_id_card_under_another_name_is_only_a_lead(self):
        card = frozenset({"id_card:499529TT"})
        a = self.r.resolve("d6", person(["Takács Bence"]), card)
        b = self.r.resolve("d7", person(["Szűcs Lili"]), card)
        self.assertNotEqual(a, b)
        self.assertEqual("same id_card, different name", self.r.review[-1]["reason"])

    def test_registry_number_links_records_that_only_share_it_with_the_name(self):
        # The placement list gives number and birth date, the visit log the number.
        a = self.r.resolve("d8", person(["Bogdán Balázs"], registry_number="BV-0047",
                                        birth_date="1981-08-09"))
        self.assertEqual(a, self.r.resolve("d9", person(["Bogdán Balázs"],
                                                        registry_number="BV-0047")))
        self.assertEqual(a, self.r.resolve("d10", person(["Bogdán Balász"],
                                                         registry_number="bv-0047")))
        self.assertEqual([], self.r.review)

    def test_registry_number_under_another_name_is_only_a_lead(self):
        a = self.r.resolve("d8", person(["Bogdán Balázs"], registry_number="BV-0047"))
        b = self.r.resolve("d9", person(["Fazekas Péter"], registry_number="BV-0047"))
        self.assertNotEqual(a, b)
        self.assertEqual("same registry_number, different name", self.r.review[-1]["reason"])

    def test_different_registry_numbers_are_different_people(self):
        a = self.r.resolve("d8", person(["Nagy Béla"], registry_number="BV-0001"))
        b = self.r.resolve("d9", person(["Nagy Béla"], registry_number="BV-0002"))
        self.assertNotEqual(a, b)
        self.assertEqual([], self.r.review)  # not even "may be the same"


if __name__ == "__main__":
    unittest.main()
