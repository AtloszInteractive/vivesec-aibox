import unittest

import graph


def picture(nodes):
    ev = [
        {"doc_id": "d1", "method": "llm", "page": 2, "quote": "long quote one", "confidence": 0.6},
        {"doc_id": "d1", "method": "llm", "page": 5, "quote": "long quote two", "confidence": 0.9},
        {"doc_id": "d1", "method": "structured", "page": None, "quote": "row", "confidence": 1},
        {"doc_id": "d2", "method": "llm", "page": 1, "quote": "other", "confidence": 0.4},
    ]
    return {
        "nodes": [{"id": "n%d" % i, "label": "N%d" % i} for i in range(nodes)] + [None],
        "edges": [{"id": "e1", "a": "n0", "b": "n1", "type": "MET", "weak": False,
                   "evidence": ev}],
        "documents": {"d1": {"source_path": "/a"}, "d2": {"source_path": "/b"}},
        "truncated": False,
    }


class CompactPictureTest(unittest.TestCase):
    def test_small_picture_is_unchanged(self):
        res = picture(3)
        self.assertIs(res, graph.compact_picture(res, above=3))

    def test_large_picture_drops_quotes_and_keeps_what_decides_the_drawing(self):
        res = picture(4)
        out = graph.compact_picture(res, above=3)
        self.assertTrue(out["compact"])
        [edge] = out["edges"]
        self.assertEqual(
            [("d1", "llm", 0.9), ("d1", "structured", 1), ("d2", "llm", 0.4)],
            [(e["doc_id"], e["method"], e["confidence"]) for e in edge["evidence"]])
        self.assertEqual({""}, {e["quote"] for e in edge["evidence"]})
        self.assertEqual((res["nodes"], res["documents"]), (out["nodes"], out["documents"]))
        # The full answer is not touched (it was logged and checked as it is).
        self.assertEqual(4, len(res["edges"][0]["evidence"]))
        self.assertNotIn("compact", res)


if __name__ == "__main__":
    unittest.main()
