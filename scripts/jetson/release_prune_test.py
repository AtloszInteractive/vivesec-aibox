"""Tests for the stage/container pruning of 172-release-box.py.

  Push-Location scripts/jetson; python -m unittest release_prune_test; Pop-Location
"""
import importlib.util
import os
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("release_box", os.path.join(HERE, "172-release-box.py"))
release_box = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(release_box)

HOME = "/home/aibox/"
STAMPS = ["20260912-130636", "20261019-195954", "20261019-200606", "20261024-120923",
          "20261024-195015", "20261024-202221", "20261024-213152"]


def stage(stamp):
    return HOME + "vivesec-release-" + stamp + "-abc_123"


def layout():
    stages = {stage(stamp): (stamp, "preparing" if stamp == "20261019-195954" else "deployed") for stamp in STAMPS}
    deployed = [stamp for stamp in STAMPS if stamp != "20261019-195954"]
    containers = {"vivesec-ui": {"running": True, "binds": [stage(STAMPS[-1]) + "/build/.output"]},
                  "vivesec-rag": {"running": True, "binds": ["/data/rag"]},
                  "vivesec-rag-eval": {"running": False, "binds": [stage(STAMPS[0]) + "/eval"]}}
    # Each release parks the containers it replaced; the parked UI binds the
    # previous stage's bundle.
    for previous, stamp in zip(deployed, deployed[1:]):
        containers["vivesec-ui-prev-" + stamp] = {"running": False, "binds": [stage(previous) + "/build/.output"]}
        containers["vivesec-rag-prev-" + stamp] = {"running": False, "binds": []}
    containers["vivesec-adapter-prev-20261024-1236"] = {"running": False, "binds": []}
    containers["vivesec-adapter-prev-20261024-213704"] = {"running": False, "binds": []}
    return stages, containers


class PruneSelectionTest(unittest.TestCase):
    def plan(self, stages, containers, keep=2):
        current = stage(STAMPS[-1])
        parked = release_box.parked_to_remove(stages, current, containers, keep)
        remaining = {name: info for name, info in containers.items() if name not in parked}
        return parked, release_box.stages_to_remove(stages, current, remaining, keep)

    def test_keeps_current_and_previous_release_with_their_parked_containers(self):
        stages, containers = layout()
        parked, removable = self.plan(stages, containers)
        self.assertNotIn("vivesec-ui-prev-20261024-213152", parked)
        self.assertNotIn("vivesec-adapter-prev-20261024-213704", parked)
        self.assertIn("vivesec-ui-prev-20261024-202221", parked)
        self.assertIn("vivesec-adapter-prev-20261024-1236", parked)
        self.assertNotIn("vivesec-ui", parked)
        self.assertNotIn("vivesec-rag-eval", parked)
        # 130636 stays: bound by a container this tool does not manage.
        self.assertEqual(removable, [stage(s) for s in
                                     ("20261019-195954", "20261019-200606", "20261024-120923", "20261024-195015")])

    def test_a_parked_container_that_could_not_be_removed_protects_its_stage(self):
        stages, containers = layout()
        removable = release_box.stages_to_remove(stages, stage(STAMPS[-1]), containers, 2)
        self.assertNotIn(stage("20261024-195015"), removable)
        self.assertIn(stage("20261019-195954"), removable)

    def test_newer_stages_are_never_touched(self):
        stages, _ = layout()
        stages[stage("20261025-080000")] = ("20261025-080000", "prepared")
        self.assertNotIn(stage("20261025-080000"), self.plan(stages, {})[1])

    def test_failed_releases_do_not_count_as_rollback_targets(self):
        stages = {stage("20261024-100000"): ("20261024-100000", "deployed"),
                  stage("20261024-110000"): ("20261024-110000", "rolled-back"),
                  stage("20261024-120000"): ("20261024-120000", "deployed")}
        removable = release_box.stages_to_remove(stages, stage("20261024-120000"), {}, 2)
        self.assertEqual(removable, [stage("20261024-110000")])

    def test_larger_keep_with_few_stages_keeps_them_all(self):
        stages = {stage(s): (s, "deployed") for s in ("20261024-100000", "20261024-110000", "20261024-120000")}
        current = stage("20261024-120000")
        self.assertEqual(release_box.stages_to_remove(stages, current, {}, 5), [])
        self.assertEqual(release_box.stages_to_remove(stages, current, {}, 2), [stage("20261024-100000")])

    def test_running_parked_containers_are_kept(self):
        stages, containers = layout()
        containers["vivesec-ui-prev-20261024-202221"]["running"] = True
        parked, removable = self.plan(stages, containers)
        self.assertNotIn("vivesec-ui-prev-20261024-202221", parked)
        self.assertNotIn(stage("20261024-195015"), removable)

class HybridProbeTest(unittest.TestCase):
    def answers(self, *citations):
        replies = [{"ok": True, "answer": "Rayleigh.", "backend": "ollama:x (hybrid)",
                    "profile": "hybrid", "citations": c} for c in citations]
        asked = []

        def ask(attempt):
            asked.append(attempt)
            return replies[attempt - 1]
        return ask, asked

    def test_an_occasional_citation_is_asked_again(self):
        ask, asked = self.answers([{"path": "/d/kovasz.pdf"}], [])
        result, attempts = release_box.hybrid_probe(ask)
        self.assertEqual((2, [1, 2]), (attempts, asked))
        self.assertEqual([], result["citations"])

    def test_citing_every_time_fails_and_names_the_documents(self):
        ask, asked = self.answers(*([[{"path": "/d/kovasz.pdf"}]] * 3))
        with self.assertRaises(AssertionError) as caught:
            release_box.hybrid_probe(ask)
        self.assertEqual([1, 2, 3], asked)
        self.assertIn("/d/kovasz.pdf", str(caught.exception))

    def test_a_broken_answer_fails_at_once(self):
        for bad in ({"ok": False, "error": "down"}, {"ok": True, "answer": "x", "backend": "extractive",
                                                     "profile": "hybrid", "citations": []},
                    {"ok": True, "answer": "x", "backend": "ollama:x", "profile": "grounded",
                     "citations": []}):
            asked = []
            with self.assertRaises(AssertionError):
                release_box.hybrid_probe(lambda n: asked.append(n) or bad)
            self.assertEqual([1], asked)


class VerificationPurposeTest(unittest.TestCase):
    def test_follows_the_boxs_purpose_setting(self):
        import json
        import tempfile
        with tempfile.TemporaryDirectory() as stage:
            def purpose(config):
                with open(os.path.join(stage, "status.json"), "w", encoding="utf-8") as handle:
                    json.dump({"app": {"purpose": config}} if config is not None else {}, handle)
                return release_box.verification_purpose(stage, {"stamp": "20261025-1200"})

            self.assertEqual({}, purpose(None))
            self.assertEqual({}, purpose({"mode": "off"}))
            self.assertEqual({"legal_basis": "release verification 20261025-1200"},
                             purpose({"mode": "required", "legal_bases": []}))
            self.assertEqual({"legal_basis": "Bv. tv. 21. §"},
                             purpose({"mode": "optional", "legal_bases": ["Bv. tv. 21. §"]}))


class KeepCountTest(unittest.TestCase):
    def test_default_disable_and_fail_safe_values(self):
        for raw, expected in ((None, 2), ("0", 0), ("1", 2), ("3", 3), ("bad", 2), ("", 2)):
            env = {} if raw is None else {"RELEASE_KEEP_STAGES": raw}
            with mock.patch.dict(os.environ, env, clear=True):
                self.assertEqual(release_box.keep_count(), expected, raw)


if __name__ == "__main__":
    unittest.main()
