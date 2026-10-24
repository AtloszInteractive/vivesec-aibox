"""Table layouts are mapped once and applied to every row by rule."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import domain_profile  # noqa: E402
import pipeline  # noqa: E402
from resolution import Resolver  # noqa: E402
from table_mapping import (TableMapper, apply_mapping, blank_tables, find_tables,  # noqa: E402
                           parse_mapping)

PROFILE = domain_profile.load("bv")
CSV = ("Látogatási napló\n"
       "Dátum;Fogvatartott neve;Látogató neve;Látogató okmányszáma;Kapcsolat\n"
       "2026-04-08;Farkas Gábor;Takács Bence;499529TT;ismerőse\n"
       "2026-04-10;Németh Zsolt;Kardos Máté;806665UA;unokatestvére\n"
       "2026-04-16;Bogdán Balázs;Fazekas Péter;167243KN;nagybátyja\n"
       "Összesen: 3 látogatás, nincs rendkívüli esemény.")
MAPPING = {"columns": [
    {"column": "Dátum", "role": "date"},
    {"column": "Fogvatartott neve", "role": "entity", "entity_type": "person", "subtype": "inmate"},
    {"column": "Látogató neve", "role": "entity", "entity_type": "person", "subtype": "visitor"},
    {"column": "Látogató okmányszáma", "role": "entity", "entity_type": "identifier",
     "subtype": "id_card"},
    {"column": "Kapcsolat", "role": "relation_label"},
    {"column": "Nincs ilyen", "role": "entity", "entity_type": "person"}],
    "relations": [
        {"type": "VISITED", "from": "Látogató neve", "to": "Fogvatartott neve"},
        {"type": "USES", "from": "Látogató okmányszáma", "to": "Látogató neve"},
        {"type": "MET", "from": "Dátum", "to": "Látogató neve"}]}


class FakeLLM:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = 0
        self.seconds = 0.0
        self.users = []

    def json_call(self, system, user, schema, max_tokens=4096, retries=1):
        self.calls += 1
        self.users.append(user)
        return self.answers.pop(0) if self.answers else {"entities": [], "relations": []}


class FindTablesTest(unittest.TestCase):
    def test_delimited(self):
        [t] = find_tables(1, CSV)
        self.assertEqual("Fogvatartott neve", t.header[1])
        self.assertEqual(3, len(t.rows))
        self.assertEqual("Takács Bence", t.rows[0].cells[2])
        self.assertTrue(CSV[t.rows[0].start:].startswith("2026-04-08;Farkas"))

    def test_sheet_records_with_missing_cells(self):
        text = ("Sheet: Elhelyezés\nColumns: Név | Zárka | Kezdete | Vége\n"
                "Név: Kiss Anna; Zárka: 12; Kezdete: 2026-01-01; Vége: 2026-02-01\n"
                "Név: Tóth Ádám; Zárka: 14; Kezdete: 2026-01-03")
        [t] = find_tables(1, text)
        self.assertEqual({0: "Tóth Ádám", 1: "14", 2: "2026-01-03"}, t.rows[1].cells)

    def test_markdown(self):
        text = "| Név | Telefon |\n| --- | --- |\n| Kiss Anna | +36 30 111 2222 |\n"
        [t] = find_tables(1, text)
        self.assertEqual({0: "Kiss Anna", 1: "+36 30 111 2222"}, t.rows[0].cells)

    def test_prose_with_commas_is_not_a_table(self):
        self.assertEqual([], find_tables(1, "Miskolc, Kossuth utca, 3. emelet.\nEgy, kettő.\n"))

    def test_blanking_keeps_offsets(self):
        tables = find_tables(1, CSV)
        blank = blank_tables(CSV, tables)
        self.assertEqual(len(CSV), len(blank))
        self.assertIn("Összesen", blank)
        self.assertNotIn("Farkas", blank)


class MappingTest(unittest.TestCase):
    def setUp(self):
        self.table = find_tables(1, CSV)[0]
        self.mapping = parse_mapping(MAPPING, self.table.header, PROFILE)

    def test_validation_drops_unknown_columns_and_impossible_relations(self):
        self.assertEqual(3, len(self.mapping.entities))
        self.assertEqual([("VISITED", 2, 1), ("USES", 2, 3)], self.mapping.relations)

    def test_a_visit_mapped_backwards_is_turned_round_by_the_profile_subtypes(self):
        backwards = dict(MAPPING, relations=[
            {"type": "VISITED", "from": "Fogvatartott neve", "to": "Látogató neve"}])
        mapping = parse_mapping(backwards, self.table.header, PROFILE)
        self.assertEqual([("VISITED", 2, 1)], mapping.relations)
        # Without subtypes on the columns there is nothing to go by: kept as given.
        plain = dict(backwards, columns=[
            dict(c, subtype="") if c.get("role") == "entity" else c for c in MAPPING["columns"]])
        self.assertEqual([("VISITED", 1, 2)],
                         parse_mapping(plain, self.table.header, PROFILE).relations)

    def test_registry_number_column_becomes_an_attribute_of_the_inmate(self):
        text = ("Dátum;Fogvatartott neve;Nyilvántartási szám;Látogató neve\n"
                "2026-04-16;Bogdán Balázs;BV-0047;Fazekas Péter\n"
                "2026-04-22;Bogdán Balázs;BV-0047;Fazekas Péter\n")
        table = find_tables(1, text)[0]
        raw = {"columns": [
            {"column": "Dátum", "role": "date"},
            {"column": "Fogvatartott neve", "role": "entity", "entity_type": "person",
             "subtype": "inmate"},
            {"column": "Nyilvántartási szám", "role": "attribute",
             "attribute": "registry_number", "of": "Fogvatartott neve"},
            {"column": "Látogató neve", "role": "entity", "entity_type": "person",
             "subtype": "visitor"}],
            "relations": [{"type": "VISITED", "from": "Látogató neve",
                           "to": "Fogvatartott neve"}]}
        ents, _ = apply_mapping(table, parse_mapping(raw, table.header, PROFILE), PROFILE, "t")
        inmate = next(e for e in ents if e.subtype == "inmate")
        self.assertEqual("BV-0047", inmate.attrs["registry_number"][0])

    def test_rows_become_dated_relations_with_the_row_as_quote(self):
        ents, rels = apply_mapping(self.table, self.mapping, PROFILE, "t0")
        self.assertEqual(9, len(ents))
        visit = rels[0]
        self.assertEqual(("VISITED", "2026-04-08", "ismerőse"),
                         (visit.type, visit.start, visit.raw_label))
        self.assertEqual("2026-04-08;Farkas Gábor;Takács Bence;499529TT;ismerőse", visit.quote)
        self.assertEqual("structured", visit.method)
        card = next(e for e in ents if e.type == "identifier")
        self.assertEqual(("id_card", "499529TT"), (card.subtype, card.value))

    def test_mapper_asks_once_per_layout(self):
        llm = FakeLLM(MAPPING)
        mapper = TableMapper(PROFILE, llm)
        self.assertIsNotNone(mapper.mapping_for(self.table))
        self.assertIsNotNone(mapper.mapping_for(find_tables(1, CSV)[0]))
        self.assertEqual(1, llm.calls)
        pinned = TableMapper(PROFILE, FakeLLM(), mapper.export())
        self.assertEqual(self.mapping.relations, pinned.mapping_for(self.table).relations)

    def test_a_learned_mapping_can_be_pinned_in_the_profile(self):
        mapper = TableMapper(PROFILE, FakeLLM(MAPPING))
        mapper.mapping_for(self.table)
        raw = dict(PROFILE, version=2, table_mappings={"latogatas": next(iter(
            mapper.export().values()))})
        profile = domain_profile.validate(raw)
        llm = FakeLLM()
        pinned = TableMapper(profile, llm, profile["table_mappings"])
        self.assertEqual(self.mapping.relations, pinned.mapping_for(self.table).relations)
        self.assertEqual(0, llm.calls)
        bad = dict(raw, table_mappings={"x": {"header": ["A"], "columns": [],
                                              "relations": [{"type": "BRIBED", "from": "A",
                                                             "to": "A"}]}})
        with self.assertRaises(domain_profile.ProfileError):
            domain_profile.validate(bad)


class PipelineTablesTest(unittest.TestCase):
    def test_table_rows_by_rule_rest_by_model(self):
        llm = FakeLLM(MAPPING)
        meta = {"doc_id": "d1", "corpus_id": "c1", "source_path": "/s/naplo.txt"}
        payload, stats = pipeline.extract_document(CSV.encode("utf-8"), meta, PROFILE, llm,
                                                   Resolver(PROFILE), TableMapper(PROFILE, llm))
        self.assertEqual(2, llm.calls)  # mapping + the prose around the table
        self.assertNotIn("Farkas", llm.users[1])
        self.assertEqual((1, 3), (stats["tables"], stats["table_rows"]))
        visits = [r for r in payload["relations"] if r["type"] == "VISITED"]
        self.assertEqual(3, len(visits))
        self.assertTrue(all(r["evidence"][0]["method"] == "structured" for r in visits))
        people = {e["label"] for e in payload["entities"] if e["type"] == "person"}
        self.assertIn("Fazekas Péter", people)


if __name__ == "__main__":
    unittest.main()
