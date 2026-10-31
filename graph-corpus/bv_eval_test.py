import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import bv_eval  # noqa: E402


def item(kind, value, alt=()):
    return bv_eval._item(kind, value, alt=alt)


class ScoringTest(unittest.TestCase):
    def test_a_name_in_either_order_and_inflected(self):
        it = item("person", "Kardos Gyula")
        self.assertTrue(bv_eval.found(it, "Kapcsolattartója Kardos Gyulának is."))
        self.assertTrue(bv_eval.found(it, "Gyula Kardos (testvére)"))
        self.assertTrue(bv_eval.found(it, "KARDOS GYULA"))
        self.assertFalse(bv_eval.found(it, "Kardos András és Szabó Gyula"))

    def test_a_phone_number_in_any_format(self):
        it = item("phone", "+36 20 932 3980")
        for text in ("+36 20 932 3980 (2026-05-29).", "06-20/932-3980", "+36209323980",
                     "tel.: 20 932 3980"):
            self.assertTrue(bv_eval.found(it, text), text)
        self.assertFalse(bv_eval.found(it, "+36 20 932 3981"))
        # The start of a longer number is not the number.
        self.assertFalse(bv_eval.found(item("phone", "+36 20 932 39"), "+36 20 932 3980"))

    def test_registry_number_address_and_alternative_wording(self):
        self.assertTrue(bv_eval.found(item("registry", "NY-466344"), "(ny. sz.: NY 466344)"))
        self.assertFalse(bv_eval.found(item("registry", "NY-466344"), "NY-4663441"))
        addr = item("address", "Nyíregyháza, Hunyadi utca 53.")
        self.assertTrue(bv_eval.found(addr, "4400 Nyíregyháza, Hunyadi u. 53."))
        self.assertFalse(bv_eval.found(addr, "Nyíregyháza, Hunyadi utca 35."))
        fight = bv_eval.incident_item("verekedés")
        self.assertTrue(bv_eval.found(fight, "dulakodás történt"))
        self.assertTrue(bv_eval.found(fight, "verekedésben vett részt"))

    def test_score_counts_misses_and_extra_people(self):
        q = {"id": "q", "seed": {"name": "Csonka Tibor"}, "acceptable": ["Sipos Attila"],
             "must": [item("person", "Kardos Gyula"), item("registry", "NY-466344")]}
        s = bv_eval.score(q, "Csonka Tibor (NY-466344) kapcsolattartója Sipos Attila és "
                             "Nagy Béla.", names=["Kardos Gyula", "Sipos Attila", "Nagy Béla",
                                                  "Csonka Tibor"])
        self.assertEqual((1, 2, 0.5), (s["hits"], s["must"], s["recall"]))
        self.assertEqual(["Nagy Béla"], s["extra"])
        self.assertEqual("Kardos Gyula", s["missed"][0]["value"])

    def test_a_negative_control_passes_only_with_no_data_and_nobody_named(self):
        q = {"id": "q", "no_data": True, "must": []}
        names, phones = ["Kovács Csaba"], ["+36 20 111 2222"]
        ok = bv_eval.score(q, "Az iratokban nincs ilyen e-mail-cím.", names, phones)
        self.assertTrue(ok["no_data_ok"])
        named = bv_eval.score(q, "Nincs adat, de Kovács Csaba lehet.", names, phones)
        self.assertFalse(named["no_data_ok"])
        silent = bv_eval.score(q, "A +36 20 111 2222 szám használója ismeretlen.", names, phones)
        self.assertFalse(silent["no_data_ok"])


class EvalSetTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = bv_eval.build_eval()
        cls.by_id = {q["id"]: q for q in cls.data["questions"]}

    def test_both_agents_and_every_bv_step_are_covered(self):
        steps = {(q["agent"], q["bv_step"]) for q in self.data["questions"]}
        for step in ("1.1", "1.2.1", "1.2.2", "1.2.3", "1.3", "1.4", "1.5"):
            self.assertIn(("person", step), steps)
        for step in ("2.1", "2.2", "2.3", "2.5"):
            self.assertIn(("identifier", step), steps)

    def test_gold_items_exist_in_the_world(self):
        names = set(self.data["names"])
        for q in self.data["questions"]:
            for it in q["must"]:
                if it["kind"] == "person":
                    self.assertIn(it["value"], names, q["id"])
            if not q["no_data"] and q["reference_plan"]:
                self.assertTrue(q["must"], q["id"])
            seed = q["seed"] or {}
            if seed.get("name"):
                self.assertEqual(1, sum(1 for p in self.data["names"] if p == seed["name"]))

    def test_negative_controls_ask_about_values_that_are_absent(self):
        for qid in ("I-2.1-email", "I-2.1-szamlaszam"):
            q = self.by_id[qid]
            self.assertTrue(q["no_data"])
            self.assertNotIn(q["seed"]["value"], self.data["phones"])

    def test_registry_lookup_asks_for_the_seed_and_its_contacts(self):
        q = self.by_id["P-1.1-nyilvszam"]
        self.assertIn(q["seed"]["registry_number"], q["question"])
        self.assertNotIn(q["seed"]["name"], q["question"])
        self.assertEqual(q["seed"]["name"], q["must"][0]["value"])


class ToolCeilingTest(unittest.TestCase):
    """The reference plans on a perfect-extraction graph, through the adapter's
    orchestrator: every graph-side gold item must reach the answer model."""

    @classmethod
    def setUpClass(cls):
        import bv_toolcheck
        data = bv_eval.build_eval()
        state, corpora = bv_toolcheck.load_graph()
        cls.rows = {r["id"]: r for r in bv_toolcheck.check(data, state, corpora, 40, True)}
        cls.investigated = {r["id"]: r for r in bv_toolcheck.check(
            data, state, corpora, 40, True, "investigate")}
        cls.plans = {q["id"]: q.get("investigate_plan") for q in data["questions"]}

    def test_graph_side_questions_reach_every_gold_item(self):
        for rows in (self.rows, self.investigated):
            self.check_rows(rows)

    def test_one_investigation_call_answers_each_chain_question(self):
        for qid in bv_eval.INVESTIGATE:
            self.assertEqual(["investigate"], [c["tool"] for c in self.plans[qid]], qid)
            calls = self.investigated[qid]["capped"]["calls"]
            self.assertEqual([("investigate", True)], [(c["tool"], c["facts"] > 0)
                                                       for c in calls], qid)

    def check_rows(self, rows):
        for qid, row in rows.items():
            if row.get("capability"):
                continue
            capped = row["capped"]
            if capped["not_checked"]:
                continue  # needs the document side as well
            if "no_data_ok" in capped:
                self.assertTrue(capped["no_data_ok"], qid)
                self.assertIsNone(capped["clarify"], qid)
                continue
            self.assertEqual(capped["must"], capped["hits"], (qid, capped["missed"]))


if __name__ == "__main__":
    unittest.main()
