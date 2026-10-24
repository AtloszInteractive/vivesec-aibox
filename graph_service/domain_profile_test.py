import copy
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import domain_profile  # noqa: E402


class ProfileTest(unittest.TestCase):
    def test_bundled_templates_are_valid(self):
        ids = [t["id"] for t in domain_profile.templates()]
        self.assertEqual(["bv", "generic", "manufacturing"], ids)

    def test_builtin_relations_are_always_present(self):
        p = domain_profile.load("bv")
        ids = {r["id"] for r in p["relation_types"]}
        self.assertTrue({"RELATED_TO", "CO_MENTIONED", "VISITED", "PLACED_IN"} <= ids)

    def test_every_label_has_all_four_languages(self):
        for t in domain_profile.templates():
            p = domain_profile.load(t["id"])
            labels = [p["name"]] + [x["label"] for k in ("entity_subtypes", "identifier_patterns",
                                                         "relation_types") for x in p[k]]
            for label in labels:
                self.assertEqual({"en", "hu", "da", "de"}, set(label), label)

    def _raw(self):
        import json
        with open(os.path.join(domain_profile.PROFILES_DIR, "bv.json"), encoding="utf-8") as f:
            return json.load(f)

    def test_unknown_key_is_rejected(self):
        raw = self._raw()
        raw["relation_type"] = []
        with self.assertRaises(domain_profile.ProfileError):
            domain_profile.validate(raw)

    def test_bad_regex_is_rejected(self):
        raw = self._raw()
        raw["identifier_patterns"][0]["regex"] = "([unclosed"
        with self.assertRaises(domain_profile.ProfileError):
            domain_profile.validate(raw)

    def test_pattern_must_target_an_identifier_subtype(self):
        raw = self._raw()
        raw["identifier_patterns"][0]["subtype"] = "inmate"
        with self.assertRaises(domain_profile.ProfileError):
            domain_profile.validate(raw)

    def test_builtin_relation_cannot_be_overridden(self):
        raw = self._raw()
        raw["relation_types"].append({"id": "RELATED_TO", "label": {"en": "x"},
                                      "from": ["person"], "to": ["person"]})
        with self.assertRaises(domain_profile.ProfileError):
            domain_profile.validate(raw)

    def test_thresholds_must_be_ordered(self):
        raw = copy.deepcopy(self._raw())
        raw["resolution"]["person"]["review"] = 0.99
        with self.assertRaises(domain_profile.ProfileError):
            domain_profile.validate(raw)

    def test_relation_subtypes_say_who_acts(self):
        visit = next(r for r in domain_profile.load("bv")["relation_types"]
                     if r["id"] == "VISITED")
        self.assertEqual((["visitor"], ["inmate"]), (visit["from_subtypes"], visit["to_subtypes"]))
        flip = domain_profile.reversed_by_subtypes
        self.assertTrue(flip(visit, "inmate", "visitor"))
        self.assertFalse(flip(visit, "visitor", "inmate"))
        self.assertTrue(flip(visit, "", "visitor"))  # the visitor is the one who visits
        self.assertFalse(flip(visit, "", ""))  # nothing to go by: kept
        self.assertFalse(flip(visit, "staff", "staff"))  # fits neither way: kept
        self.assertFalse(flip({"id": "MET"}, "inmate", "visitor"))
        self.assertIn("person (visitor) -> person (inmate)", domain_profile.relation_line(visit))
        for bad in (["ghost"], ["cell"], [], "visitor"):
            raw = self._raw()
            raw["relation_types"] = [dict(visit, from_subtypes=bad)]
            with self.assertRaises(domain_profile.ProfileError):
                domain_profile.validate(raw)

    def test_validation_is_idempotent(self):
        p = domain_profile.load("bv")
        self.assertEqual(p, domain_profile.validate(copy.deepcopy(p)))

    def test_changed_builtin_in_saved_profile_is_rejected(self):
        p = domain_profile.load("bv")
        p["relation_types"][0]["presence"] = True
        with self.assertRaises(domain_profile.ProfileError):
            domain_profile.validate(p)

    def test_patterns_find_hungarian_identifiers(self):
        import re
        p = {x["id"]: x for x in domain_profile.load("bv")["identifier_patterns"]}
        text = "Hívta a +36 30 123 4567 és a 06-20-555-1234 számot; ABC-123 és AA BC-123; 123456AB."
        phones = re.findall(p["phone_hu"]["regex"], text)
        self.assertEqual(["+36 30 123 4567", "06-20-555-1234"], phones)
        self.assertEqual(["+36301234567", "+36205551234"],
                         [domain_profile.normalize_value(x, "phone_hu") for x in phones])
        self.assertEqual(["ABC-123", "AA BC-123"], re.findall(p["plate_hu"]["regex"], text))
        self.assertEqual(["123456AB"], re.findall(p["id_card_hu"]["regex"], text))


if __name__ == "__main__":
    unittest.main()
