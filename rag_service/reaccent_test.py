"""Unit tests for reaccent -- no service or model needed.

    python rag_service/reaccent_test.py
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import reaccent

HUNGARIAN = [
    "A napidíj mértéke Magyarországon 32 EUR/nap, Németországban 38 EUR/nap.",
    "A költségtérítési igényt az utazás befejezésétől számított 30 napon belül "
    "kell benyújtani, az utazási rendszerben.",
    "A szabadság mértéke évi 25 munkanap; a hosszabb szabadságot két héttel "
    "előre kell kérni.",
]


class FoldTest(unittest.TestCase):
    def test_hungarian_vowels_lose_their_accents(self):
        self.assertEqual("utazasi koltsegterites napidij",
                         reaccent.fold("Utazási költségtérítés napidíj"))

    def test_danish_and_german_letters_fold_to_what_is_typed(self):
        self.assertEqual("godtgorelse maltider", reaccent.fold("godtgørelse måltider"))
        self.assertEqual("ubernachtung strasse", reaccent.fold("Übernachtung Straße"))

    def test_plain_text_is_only_lowercased(self):
        self.assertEqual("gross margin 2025", reaccent.fold("Gross Margin 2025"))


class RepairTest(unittest.TestCase):
    def setUp(self):
        self.vocabulary = reaccent.build(HUNGARIAN)

    def test_exact_word_gets_its_accents_back(self):
        self.assertEqual("napidíj", self.vocabulary.repair("napidij"))

    def test_inflected_query_word_is_repaired_from_a_longer_corpus_word(self):
        # The corpus has "költségtérítési"; the user types the shorter stem.
        self.assertEqual("költségtérítés", self.vocabulary.repair("koltsegterites"))

    def test_the_suffix_the_user_typed_is_kept(self):
        # The corpus has "szabadság"; "szabadsagra" keeps its case ending.
        self.assertEqual("szabadságra", self.vocabulary.repair("szabadsagra"))

    def test_an_already_accented_query_is_untouched(self):
        q = "Mennyi a napidíj mértéke?"
        self.assertEqual(q, self.vocabulary.repair(q))

    def test_a_word_the_corpus_does_not_have_is_left_alone(self):
        self.assertEqual("kulfoldi", self.vocabulary.repair("kulfoldi"))

    def test_short_words_are_never_touched(self):
        self.assertEqual("nap es ev", self.vocabulary.repair("nap es ev"))

    def test_punctuation_and_numbers_survive(self):
        self.assertEqual("napidíj: 32 EUR/nap!",
                         self.vocabulary.repair("napidij: 32 EUR/nap!"))

    def test_a_word_that_exists_as_typed_is_not_rewritten(self):
        # "utazas" would fold onto "utazási", but the corpus itself spells
        # "utazas" nowhere -- while "napon" it does, so that must stay.
        vocabulary = reaccent.build(["A hatalom és a hatalom, napon és napon."])
        self.assertEqual("napon", vocabulary.repair("napon"))

    def test_an_ambiguous_spelling_is_not_guessed(self):
        # Both spellings are equally common, so neither can be assumed.
        vocabulary = reaccent.build(["orult orult", "őrült őrült"])
        self.assertEqual("orult", vocabulary.repair("orult"))

    def test_an_empty_corpus_repairs_nothing(self):
        vocabulary = reaccent.build([])
        self.assertEqual("napidij", vocabulary.repair("napidij"))
        self.assertEqual("", vocabulary.repair(""))

    def test_english_query_against_a_hungarian_corpus_is_untouched(self):
        q = "How many vacation days does an employee get?"
        self.assertEqual(q, self.vocabulary.repair(q))

    def test_a_coincidental_short_prefix_does_not_trigger_a_repair(self):
        # "napalm" shares only "nap" with "napidíj" -- far too little.
        self.assertEqual("napalm", self.vocabulary.repair("napalm"))

    def test_words_that_share_an_opening_but_diverge_are_left_alone(self):
        # "europe" and "európai" share five letters and mean different things.
        vocabulary = reaccent.build(["Az európai piacokon értékesítünk."])
        self.assertEqual("europe", vocabulary.repair("europe"))
        self.assertEqual("european", vocabulary.repair("european"))
        self.assertEqual("európai", vocabulary.repair("europai"))

    def test_the_shorter_corpus_word_is_used_when_the_longer_one_diverges(self):
        # "szabadságot" diverges from "szabadsagra", but "szabadság" does not.
        vocabulary = reaccent.build(["szabadságot és szabadság, szabályzat"])
        self.assertEqual("szabadságra", vocabulary.repair("szabadsagra"))

    def test_capitalisation_of_the_query_is_preserved(self):
        self.assertEqual("Napidíj", self.vocabulary.repair("Napidij"))


class DanishAndGermanTest(unittest.TestCase):
    """These languages lose far less than Hungarian (measured: 0.02 of score
    against 0.20), but the same repair applies when the accents are dropped."""

    def test_danish_letters_are_repaired(self):
        vocabulary = reaccent.build(["rejsepolitik godtgørelse for måltider"])
        self.assertEqual("godtgørelse", vocabulary.repair("godtgorelse"))
        self.assertEqual("måltider", vocabulary.repair("maltider"))

    def test_german_umlaut_is_repaired(self):
        vocabulary = reaccent.build(["Erstattung der Übernachtung und Verpflegung"])
        self.assertEqual("übernachtung", vocabulary.repair("ubernachtung"))

    def test_the_oe_and_aa_transliteration_is_not_a_repair_target(self):
        # "godtgoerelse" is a different spelling, not an accent-less one, and
        # the measurement showed the model handles it on its own.
        vocabulary = reaccent.build(["rejsepolitik godtgørelse for måltider"])
        self.assertEqual("godtgoerelse", vocabulary.repair("godtgoerelse"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
