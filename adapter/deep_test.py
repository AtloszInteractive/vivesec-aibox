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


def finding(n, path, note="Látogatás.", quote="látogatója"):
    return {"chunk": {"chunk_id": "c%d" % n, "corpus_id": "k", "source_path": path,
                      "page_number": 1, "text": "x"}, "quote": quote, "note": note}


class Recorder:
    """A fake model that answers by prompt kind and records what it was asked."""

    def __init__(self, condense=None, digest="Digest.", section=None):
        self.calls = []
        self.condense, self.digest, self.section = condense, digest, section

    def __call__(self, system, user, **opts):
        self.calls.append((system, user, opts))
        if system == deep.MAP_SYSTEM:
            return model(system, user)
        if system == deep.CONDENSE_SYSTEM:
            return self.condense(user), 0, 0
        if system == deep.SECTION_SYSTEM:
            return self.section(user), 0, 0
        return self.digest, 0, 0

    def kinds(self):
        return [("condense" if s == deep.CONDENSE_SYSTEM else "section"
                 if s == deep.SECTION_SYSTEM else "map" if s == deep.MAP_SYSTEM else "digest")
                for s, _, _ in self.calls]


class ReduceTest(unittest.TestCase):
    def test_a_document_over_its_share_is_condensed_the_others_kept_as_found(self):
        long_note = "Kiss Anna meglátogatta Nagy Bélát, a látogatás rendben zajlott le. " * 2
        found = ([finding(i, "/d/a.csv", long_note) for i in range(20)]
                 + [finding(50, "/d/b.csv"), finding(51, "/d/c.csv")])
        fake = Recorder(condense=lambda user: json.dumps(
            {"documents": [{"document": 1, "summary": "Kiss Anna 20 alkalommal látogatta."}]}))
        contexts, result = deep.reduce_findings("Ki?", found, fake, budget=3000)
        self.assertEqual(["condense"], fake.kinds())
        self.assertIn("/d/a.csv (limit:", fake.calls[0][1])
        self.assertEqual(["/d/a.csv", "/d/b.csv", "/d/c.csv"],
                         [c["source_path"] for c in contexts])
        self.assertEqual("Kiss Anna 20 alkalommal látogatta.", contexts[0]["text"])
        self.assertEqual('- Látogatás. "látogatója" (p.1)', contexts[1]["text"])
        self.assertEqual({"level": 1, "lost": 0, "digests": 0, "trimmed": 0}, result)

    def test_an_unusable_summary_keeps_the_findings_cut_and_counts_as_lost(self):
        found = [finding(i, "/d/a.csv", "Hosszú megállapítás szövege. " * 4) for i in range(30)]
        contexts, result = deep.reduce_findings("Ki?", found, Recorder(condense=lambda u: "x"),
                                                budget=1000)
        self.assertEqual(1, result["lost"])
        self.assertLessEqual(len(contexts[0]["text"]), 1000)
        self.assertTrue(contexts[0]["text"].startswith("- Hosszú"))

    def test_too_many_documents_are_merged_into_digests_that_name_them(self):
        found = [finding(i, "/d/r%02d.csv" % i, "Megállapítás %d, hosszabb leírással." % i)
                 for i in range(40)]
        fake = Recorder(digest="  Összesítés  a  dokumentumokról. ")
        with patch.object(deep, "MAX_FINDINGS", 10), patch.object(deep, "BATCH_CHARS", 1200):
            contexts, result = deep.reduce_findings("Ki?", found, fake, budget=20000)
        self.assertTrue(set(fake.kinds()) == {"digest"})
        self.assertLessEqual(len(contexts), 10)
        self.assertEqual(2, result["level"])
        self.assertEqual(len(contexts), result["digests"])
        self.assertTrue(all(c["source_path"] is None for c in contexts))
        self.assertTrue(contexts[0]["text"].startswith("(/d/r00.csv; /d/r01.csv"))
        self.assertIn("Összesítés a dokumentumokról.", contexts[0]["text"])
        # Every document reached a digest.
        named = " ".join(c["text"] for c in contexts)
        self.assertTrue(all("/d/r%02d.csv" % i in named for i in range(40)))

    def test_read_all_condenses_instead_of_cutting(self):
        rag = FakeRag(CHUNKS)
        fake = Recorder()
        # 7 findings in 4 documents: over the cap of 5 findings, within it as documents.
        with patch.object(deep, "BATCH_CHARS", 70), patch.object(deep, "MAX_FINDINGS", 5):
            contexts, info = deep.read_all(
                rag, {"corpus_ids": ["k"], "access": None}, "Ki?", ["Bogdán Balázs"],
                lang="Hungarian", chat=fake)
        self.assertEqual(1, info["condensed"])
        self.assertFalse(info["partial"])
        self.assertEqual(4, len(contexts) - 1)
        self.assertEqual(sorted({c["source_path"] for c in contexts[1:]}),
                         [c["source_path"] for c in contexts[1:]])
        self.assertIn("dokumentumonk\u00e9nt \u00f6sszes\u00edtettem", contexts[0]["text"])
        self.assertNotIn("ker\u00fclt a v\u00e1laszba", contexts[0]["text"])


def page_chunk(n, page):
    return {"chunk_id": "p%d" % n, "corpus_id": "k", "source_path": "/d/szerzodes.pdf",
            "page_number": page, "text": "%d. pont: A bérleti díj %d000 forint." % (n, n)}


class ReadDocumentTest(unittest.TestCase):
    def test_every_part_is_summarised_with_page_references(self):
        chunks = [page_chunk(n, n // 2 + 1) for n in range(12)]
        replies = iter(["nem json", "megint nem"] + [None] * 20)

        def section(user):
            reply = next(replies)
            if reply is not None:
                return reply
            return json.dumps({"findings": [{"excerpt": 1, "quote": "A bérleti díj",
                                             "note": "Díj."}]})

        fake = Recorder(section=section)
        steps = []
        with patch.object(deep, "DOC_BATCH_CHARS", 120):
            contexts, info = deep.read_document(
                chunks, "Elemezd a szerződést.", "Hungarian", "szerzodes.pdf", chat=fake,
                num_ctx=65536, progress=lambda *s: steps.append(s))
        groups = info["groups"]
        self.assertGreater(groups, 2)
        # The first part failed twice (retried once), every other one was read.
        self.assertEqual(groups + 1, len(fake.calls))
        self.assertTrue(all(o["num_ctx"] == 65536 for _, _, o in fake.calls))
        self.assertTrue(all("TASK: Elemezd" in u for _, u, _ in fake.calls))
        self.assertEqual((1, groups - 1, True), (info["failed"], info["parts"], info["partial"]))
        parts = contexts[1:]
        self.assertTrue(all(p["source_path"] == "/d/szerzodes.pdf" for p in parts))
        self.assertTrue(parts[0]["text"].startswith("pp. "), parts[0]["text"])
        self.assertIn("12 r\u00e9szletet (szerzodes.pdf)", contexts[0]["text"])
        self.assertIn("1 r\u00e9szt a(z) %d k\u00f6z\u00fcl" % groups, contexts[0]["text"])
        self.assertEqual([("read", i, groups) for i in range(groups)], steps[:-1])

    def test_part_summaries_that_do_not_fit_are_merged_keeping_the_document(self):
        chunks = [page_chunk(n, n + 1) for n in range(30)]
        fake = Recorder(section=lambda user: json.dumps({"findings": [
            {"excerpt": 1, "quote": "A bérleti díj", "note": "Díj, hosszan kifejtve. " * 6}]}))
        with patch.object(deep, "DOC_BATCH_CHARS", 60), patch.object(deep, "BATCH_CHARS", 2000):
            contexts, info = deep.read_document(chunks, "Elemezd.", "Hungarian", "x.pdf",
                                                chat=fake, budget=3000)
        self.assertGreaterEqual(info["condensed"], 1)
        self.assertIn("digest", fake.kinds())
        self.assertTrue(all(c["source_path"] == "/d/szerzodes.pdf" for c in contexts[1:]))
        self.assertFalse(info["partial"])

    def test_a_capped_document_is_partial(self):
        fake = Recorder(section=lambda user: '{"findings": []}')
        contexts, info = deep.read_document([page_chunk(1, 1)], "Elemezd.", "English", "x.pdf",
                                            chunks_total=5, chat=fake)
        self.assertTrue(info["partial"])
        self.assertIn("Only the first 1 of the document's 5 passages", contexts[0]["text"])
        self.assertEqual(1, len(contexts))


if __name__ == "__main__":
    unittest.main()
