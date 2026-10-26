"""The throughput bench: corpus sampling, a run against a scripted model, the report."""
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import extract_bench  # noqa: E402
from llm_client_test import ollama_reply  # noqa: E402

NOTE_1 = "Kovács János (szül. 1980. 01. 02.) a 102. zárkában van elhelyezve."
NOTE_2 = "Kovács János és Nagy Éva 2026. 03. 12-én találkozott."


def scripted(req, timeout=None):
    user = json.loads(req.data.decode("utf-8"))["messages"][1]["content"]
    if "zárkában" in user:
        content = {"entities": [{"ref": "a", "type": "person", "names": ["Kovács János"]},
                                {"ref": "c", "type": "place", "subtype": "cell",
                                 "names": ["102. zárkában"]}],
                   "relations": [{"type": "PLACED_IN", "from": "a", "to": "c", "quote": NOTE_1}]}
    else:
        content = {"entities": [{"ref": "a", "type": "person", "names": ["Kovács János"]},
                                {"ref": "b", "type": "person", "names": ["Nagy Éva"]}],
                   "relations": [{"type": "MET", "from": "a", "to": "b", "quote": NOTE_2}]}
    return ollama_reply(json.dumps(content, ensure_ascii=False))


class ExtractBenchTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.src = os.path.join(self.tmp.name, "src")
        files = {"jelentesek/feljegyzesek/2025/a.txt": NOTE_1,
                 "jelentesek/feljegyzesek/2026/b.txt": NOTE_2,
                 "jelentesek/feljegyzesek/2026/c.txt": NOTE_2,
                 "egyeb/x.txt": NOTE_1}
        for rel, text in files.items():
            path = os.path.join(self.src, *rel.split("/"))
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with io.open(path, "w", encoding="utf-8") as f:
                f.write(text)

    def tearDown(self):
        self.tmp.cleanup()

    def test_kind_is_folder_to_depth_and_extension(self):
        kinds = {r["path"]: r["kind"] for r in extract_bench.collect(self.src, 2)}
        self.assertEqual(kinds["jelentesek/feljegyzesek/2025/a.txt"], "jelentesek/feljegyzesek/*.txt")
        self.assertEqual(kinds["egyeb/x.txt"], "egyeb/*.txt")

    def test_sample_takes_per_kind_and_interleaves(self):
        rows = extract_bench.collect(self.src, 2)
        picked = extract_bench.sample(rows, 1, seed=1)
        self.assertEqual(sorted(r["kind"] for r in picked),
                         ["egyeb/*.txt", "jelentesek/feljegyzesek/*.txt"])
        self.assertEqual(len(extract_bench.sample(rows, 0, seed=1)), 4)

    def test_run_records_tokens_and_reports_by_kind(self):
        out = os.path.join(self.tmp.name, "run.jsonl")
        argv = ["--source", self.src, "--out", out, "--profile", "bv", "--per-kind", "2",
                "--llm-url", "http://model", "--model", "m", "--num-ctx", "2048",
                "--label", "baseline", "--project", "1000"]
        with mock.patch("urllib.request.urlopen", side_effect=scripted), \
                redirect_stdout(io.StringIO()) as printed:
            self.assertEqual(extract_bench.main(argv), 0)
        with io.open(out, encoding="utf-8") as f:
            records = [json.loads(line) for line in f]
        self.assertEqual(len(records), 3)
        for r in records:
            self.assertIsNone(r["error"])
            self.assertTrue(r["payload"]["relations"])
            self.assertEqual(r["config"]["compact"], True)
            self.assertEqual(r["stats"]["llm_calls"], 1)
            self.assertEqual(r["stats"]["llm_output_tokens"], 300)
            self.assertEqual(r["stats"]["llm_prompt_tokens"], 1200)
            self.assertEqual(len(r["calls"]), 1)
            self.assertEqual(r["mix"], {"egyeb/*.txt": 1, "jelentesek/feljegyzesek/*.txt": 3})
        summary = extract_bench.summarize(records)
        self.assertEqual(summary["calls"]["extraction"], 3)
        self.assertAlmostEqual(summary["calls"]["output_tok_per_s"], 50.0)
        self.assertEqual(summary["overall"]["source_documents"], 4)
        text = printed.getvalue()
        self.assertIn("## baseline", text)
        self.assertIn("| jelentesek/feljegyzesek/*.txt | 2 / 3 |", text)
        fixed = os.path.join(self.tmp.name, "fixed.jsonl")
        with io.open(fixed, "w", encoding="utf-8") as f:
            for i, r in enumerate(records):
                f.write(json.dumps(dict(r, wall=10.0, started_at=10.0 * i,
                                        finished_at=10.0 * i + 10), ensure_ascii=False) + "\n")
        self.assertIn("10.0 s/document, 360 documents/hour; 1000 documents in one queue: 0.1 days",
                      extract_bench.report([fixed], 1000))
        # Three documents of 10 s each, all in flight at once: 3.3 s each in effect.
        with io.open(fixed, "w", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(dict(r, wall=10.0, started_at=0.0, finished_at=10.0),
                                   ensure_ascii=False) + "\n")
        overall = extract_bench.summarize(extract_bench._load(fixed))["overall"]
        self.assertAlmostEqual(overall["overlap"], 3.0)
        self.assertAlmostEqual(overall["effective_s"], 10.0 / 3)

        # A second run resumes: nothing left to do, no model call.
        with mock.patch("urllib.request.urlopen", side_effect=AssertionError("no call")), \
                redirect_stdout(io.StringIO()):
            extract_bench.main(argv)
        report = extract_bench.report([out, out])
        self.assertIn("## Comparison (mean s/document)", report)

    def test_kinds_filter(self):
        out = os.path.join(self.tmp.name, "only.jsonl")
        with mock.patch("urllib.request.urlopen", side_effect=scripted), \
                redirect_stdout(io.StringIO()):
            extract_bench.main(["--source", self.src, "--out", out, "--profile", "bv",
                                "--per-kind", "0", "--kinds", "egyeb/*.txt", "--parallel", "3",
                                "--llm-url", "http://model"])
        with io.open(out, encoding="utf-8") as f:
            self.assertEqual(["egyeb/x.txt"], [json.loads(line)["path"] for line in f])


if __name__ == "__main__":
    unittest.main()
