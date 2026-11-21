"""Confidence bands for answers with fields left unfilled ('?').

  Push-Location adapter; python -m unittest confidence_test; Pop-Location
"""
import time
import unittest

import confidence

NOW = time.time()


def ctx(text="Revenue was 42. The contract was signed by Kiss Anna."):
    return {"score": 0.9, "source_path": "/d/f/a.txt", "text": text,
            "metadata": {"mtime": NOW - 86400}}


def score(answer):
    return confidence.score([ctx()], answer, question="revenue?", now=NOW)


class UnfilledFieldsTest(unittest.TestCase):
    def test_an_answer_with_unfilled_fields_is_never_green(self):
        for answer in ("1. Document identity -- Title: Contract | Date: ? | Author: ? [#1]",
                       "| Item | Owner | Date |\n| Revenue 42 [#1] | ? | ? |",
                       "Signed by Kiss Anna [#1]\nDeadline: **?**"):
            c = score(answer)
            self.assertEqual(("amber", 84), (c["band"], c["score"]), answer)
            self.assertIn("unfilled_fields", c["degraded"])
            self.assertFalse(c["suppress"])

    def test_questions_and_complete_answers_stay_as_they_were(self):
        for answer in ("The revenue was 42 [#1].",
                       "Signed by Kiss Anna [#1]. Who approved it? The documents do not say.",
                       "Revenue: 42 [#1]"):
            c = score(answer)
            self.assertEqual("green", c["band"], answer)
            self.assertNotIn("unfilled_fields", c["components"])
            self.assertNotIn("unfilled_fields", c["degraded"])

    def test_a_lower_score_is_not_raised(self):
        c = confidence.score([dict(ctx(), score=0.3)], "Date: ? [#1]", question="revenue?",
                             now=NOW)
        self.assertLess(c["score"], 84)
        self.assertIn("unfilled_fields", c["degraded"])


if __name__ == "__main__":
    unittest.main()
