import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.append(os.path.join(os.path.dirname(HERE), "demo-corpus"))

import generate  # noqa: E402
import world  # noqa: E402
from render import Renderer  # noqa: E402


def _build(seed=5, persons=60):
    w = world.build(persons, seed)
    docs = Renderer(w, seed).render_all()
    return w, docs, generate.build_answer_key(w, docs, seed)


class GraphCorpusTest(unittest.TestCase):
    def test_every_mention_is_in_its_document(self):
        _, docs, _ = _build()
        for d in docs:
            text = d.text()
            for pid, surface in d.mentions:
                self.assertIn(surface, text, "%s %s" % (d.key, pid))

    def test_every_relation_type_is_evidenced(self):
        w, _, _ = _build()
        missing = {r.type for r in w.relations if not r.evidence}
        self.assertEqual(set(), missing)

    def test_evidence_points_to_documents_that_cite_it(self):
        w, docs, _ = _build()
        by_key = {d.key: d for d in docs}
        for r in w.relations:
            for key in r.evidence:
                self.assertIn(r.id, by_key[key].evidence)

    def test_name_twins_share_a_name_but_not_an_identity(self):
        w, _, key = _build()
        self.assertTrue(key["questions"]["must_not_merge"])
        for twin, original in key["questions"]["must_not_merge"]:
            a, b = w.persons[twin], w.persons[original]
            self.assertEqual(a.name, b.name)
            self.assertNotEqual(a.id_number, b.id_number)

    def test_gold_paths_follow_supported_edges(self):
        _, _, key = _build()
        edges = {tuple(sorted((e["a"], e["b"]))) for e in key["person_edges"]}
        self.assertTrue(key["questions"]["path"])
        for q in key["questions"]["path"]:
            path = q["example_path"]
            self.assertEqual(q["distance"], len(path) - 1)
            for a, b in zip(path, path[1:]):
                self.assertIn(tuple(sorted((a, b))), edges)

    def test_same_seed_same_answer_key(self):
        self.assertEqual(_build(seed=9)[2], _build(seed=9)[2])


if __name__ == "__main__":
    unittest.main()
