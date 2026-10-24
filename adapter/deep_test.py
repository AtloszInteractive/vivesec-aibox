"""Full-coverage reading with a fake RAG and a fake model."""
import json
import threading
import unittest
from unittest.mock import patch

import deep
import llm


def chunk(n, text, path="/d/r%02d.txt"):
    return {"chunk_id": "c%d" % n, "doc_id": "d%d" % (n // 2), "corpus_id": "k",
            "source_path": path % (n // 2), "page_number": 1, "title": "t", "text": text}


CHUNKS = [chunk(n, "Bejegyzés %d: Bogdán Balázs látogatója Fazekas Péter volt." % n)
          for n in range(7)]


class FakeRag:
    def __init__(self, chunks, skipped=0):
        self.chunks = chunks
        self.skipped = skipped
        self.calls = []

    def __call__(self, path, body):
        self.calls.append((path, body))
        if path == "/rag/scope_stats":
            return {"stats": {"skipped": self.skipped}}
        page = self.chunks[body["offset"]:body["offset"] + body["limit"]]
        end = body["offset"] + len(page)
        return {"chunks": page, "total": len(self.chunks), "documents": 4,
                "next_offset": end if end < len(self.chunks) else None}


def model(system, user, **kw):
    """Quotes excerpt 1 truly, invents one for excerpt 2, names an excerpt out of range."""
    findings = [{"excerpt": 1, "quote": "Bogdán Balázs látogatója Fazekas Péter",
                 "note": "Fazekas Péter látogatta."},
                {"excerpt": 2, "quote": "Fazekas Péter fegyvert csempészett",
                 "note": "kitalált"},
                {"excerpt": 99, "quote": "Bogdán", "note": "rossz sorszám"}]
    return json.dumps({"findings": findings}), 0, 0


class DeepTest(unittest.TestCase):
    def run_deep(self, chunks=CHUNKS, skipped=0, **kw):
        rag = FakeRag(chunks, skipped)
        calls = []

        def chat(system, user, **opts):
            calls.append((user, opts))
            return model(system, user, **opts)

        steps = []
        contexts, info = deep.read_all(
            rag, {"corpus_ids": ["k"], "access": None}, "Ki látogatta Bogdán Balázst?",
            ["Bogdán Balázs"], lang="Hungarian", chat=chat,
            progress=lambda phase, done, total: steps.append((phase, done, total)), **kw)
        return contexts, info, rag, calls, steps

    def test_every_passage_is_read_and_only_true_quotes_count(self):
        with patch.object(deep, "BATCH_CHARS", 120):
            contexts, info, rag, calls, steps = self.run_deep()
        self.assertEqual(7, info["read"])
        self.assertEqual(len(calls), info["groups"])
        self.assertTrue(all(o["response_format"] and o["stream"] is False for _, o in calls))
        # One true finding per group, the invented and the out-of-range ones dropped.
        findings = [c for c in contexts if c["origin"] == "deep"]
        self.assertEqual(info["groups"], len(findings))
        self.assertTrue(all(c["chunk_id"] and "Fazekas Péter látogatta" in c["text"]
                            for c in findings))
        note = contexts[0]
        self.assertEqual("search-note", note["origin"])
        self.assertIn("7 r\u00e9szlet 4 dokumentumb\u00f3l", note["text"])
        self.assertFalse(info["partial"])
        self.assertEqual(("collect", 0, 0), steps[0])
        self.assertEqual(("write", info["groups"], info["groups"]), steps[-1])
        self.assertEqual([("read", i, info["groups"]) for i in range(info["groups"])],
                         steps[1:-1])

    def test_a_cap_makes_the_result_partial_and_says_so(self):
        with patch.object(deep, "MAX_CHUNKS", 3), patch.object(deep, "PAGE", 2):
            contexts, info, rag, _, _ = self.run_deep()
        self.assertEqual((7, 3, True), (info["matched"], info["read"], info["partial"]))
        self.assertEqual([0, 2], [b["offset"] for p, b in rag.calls if p == "/rag/match_chunks"])
        self.assertIn("R\u00e9szleges eredm\u00e9ny: 7 r\u00e9szlet", contexts[0]["text"])

    def test_documents_without_text_are_named_in_the_note(self):
        contexts, info, rag, _, _ = self.run_deep(skipped=5)
        self.assertEqual(5, info["unreadable"])
        self.assertIn("5-nak nincs olvashat\u00f3 sz\u00f6vege", contexts[0]["text"])
        stats = next(b for p, b in rag.calls if p == "/rag/scope_stats")
        self.assertEqual({"corpus_ids": ["k"], "access": None}, stats)
        contexts, info, _, _, _ = self.run_deep()
        self.assertNotIn("olvashat\u00f3 sz\u00f6vege", contexts[0]["text"])

    def test_no_match_reads_nothing(self):
        contexts, info, _, calls, _ = self.run_deep(chunks=[])
        self.assertEqual([], calls)
        self.assertEqual(1, len(contexts))
        self.assertIn("egyik r\u00e9szlete sem tartalmazza", contexts[0]["text"])

    def test_cancel_stops_before_the_next_group(self):
        stop = threading.Event()
        stop.set()
        with self.assertRaises(llm.GenerationCancelled):
            self.run_deep(cancel_event=stop)

    def test_garbage_from_the_model_is_no_finding(self):
        group = CHUNKS[:1]
        for bad in ("", "nem json", '{"findings": "x"}', '[1, 2]',
                    '{"findings": [{"excerpt": true, "quote": "Bogdán", "note": "x"}]}'):
            self.assertEqual([], deep.parse_findings(bad, group), bad)
        fenced = '```json\n{"findings": [{"excerpt": 1, "quote": "BOGDÁN  BALÁZS", "note": "x"}]}\n```'
        self.assertEqual(1, len(deep.parse_findings(fenced, group)))


if __name__ == "__main__":
    unittest.main()
