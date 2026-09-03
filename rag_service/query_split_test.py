"""Unit tests for query_split -- no service or model needed.

    python rag_service/query_split_test.py
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import query_split


class SplitQuestionTest(unittest.TestCase):
    def test_plain_question_is_left_alone(self):
        q = "What is the maximum fine for a transport company?"
        self.assertEqual([q], query_split.split_question(q))

    def test_clause_boundary_splits(self):
        q = ("Hvad var den monetære fortabelse for Metropolitan Radio Group, "
             "og hvilken retspraksis henvises der til?")
        parts = query_split.split_question(q, mode="clause")
        self.assertEqual(2, len(parts))
        self.assertTrue(parts[0].startswith("Hvad var"))
        self.assertTrue(parts[1].startswith("hvilken retspraksis"))
        self.assertTrue(all(p.endswith("?") for p in parts))

    def test_list_is_not_a_clause_boundary(self):
        # "eyes, teeth and fingers" is a list, not two questions.
        q = "Which dysplasia is related to eyes, teeth and fingers?"
        self.assertEqual([q], query_split.split_question(q, mode="clause"))

    def test_bare_conjunction_only_splits_in_conjunction_mode(self):
        q = ("Milyen halászati szabályozások vonatkoznak Alaszkára "
             "és mennyi volt az éves teljes kifogott mennyiség?")
        self.assertEqual([q], query_split.split_question(q, mode="clause"))
        self.assertEqual(2, len(query_split.split_question(q, mode="conjunction")))

    def test_bare_conjunction_needs_substantial_halves(self):
        q = "Show me the budget and the plan?"
        self.assertEqual([q], query_split.split_question(q, mode="conjunction"))

    def test_off_mode_never_splits(self):
        q = "Was ist X, und was ist Y?"
        self.assertEqual([q], query_split.split_question(q, mode="off"))

    def test_only_the_first_conjunction_splits(self):
        q = "Was ist A, und was ist B, und was ist C?"
        self.assertEqual(2, len(query_split.split_question(q, mode="clause")))

    def test_empty_question(self):
        self.assertEqual([""], query_split.split_question(""))


class InterleaveTest(unittest.TestCase):
    def test_alternates_between_runs(self):
        a = [("a1", 0.9), ("a2", 0.8)]
        b = [("b1", 0.7), ("b2", 0.6)]
        self.assertEqual(["a1", "b1", "a2", "b2"],
                         [c for c, _ in query_split.interleave([a, b], 4)])

    def test_respects_the_limit(self):
        a = [("a1", 0.9), ("a2", 0.8)]
        b = [("b1", 0.7)]
        self.assertEqual(["a1", "b1"],
                         [c for c, _ in query_split.interleave([a, b], 2)])

    def test_drops_duplicates_keeping_the_first(self):
        a = [("x", 0.9)]
        b = [("x", 0.5), ("y", 0.4)]
        self.assertEqual([("x", 0.9), ("y", 0.4)],
                         query_split.interleave([a, b], 5))

    def test_uneven_runs_do_not_lose_the_tail(self):
        a = [("a1", 0.9)]
        b = [("b1", 0.8), ("b2", 0.7), ("b3", 0.6)]
        self.assertEqual(["a1", "b1", "b2", "b3"],
                         [c for c, _ in query_split.interleave([a, b], 10)])

    def test_no_runs(self):
        self.assertEqual([], query_split.interleave([], 5))


if __name__ == "__main__":
    unittest.main()
