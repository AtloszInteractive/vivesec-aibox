"""E10a: an analysis that stops before its end continues where it stopped.

  Push-Location adapter; python -m unittest analysis_checkpoint_test; Pop-Location
"""
import json
import os
import tempfile
import threading
import time
import unittest
from unittest import mock

import analysis_checkpoint
import deep
import llm
from deep_test import Recorder, page_chunk

SETTINGS = {"part_chars": 120}


def finding(user):
    return json.dumps({"findings": [{"excerpt": 1, "quote": "A bérleti díj", "note": "Díj."}]})


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = os.path.join(self.tmp.name, "analysis")
        self.chunks = [page_chunk(n, n + 1) for n in range(4)]

    def store(self):
        return analysis_checkpoint.CheckpointStore(self.root)

    def open(self, store=None, user="u1", chunks=None, task="Elemezd.", lang="Hungarian",
             model="m", settings=None):
        return (store or self.store()).open(user, chunks or self.chunks, task, lang, model,
                                            settings or SETTINGS)

    def test_parts_survive_a_restart_and_only_this_analysis_sees_them(self):
        self.open().put(0, {"text": "p. 1: díj"})
        self.open().put(1, None)
        # A new store object is a restarted adapter.
        again = self.open()
        self.assertEqual((True, {"text": "p. 1: díj"}), again.get(0))
        self.assertEqual((True, None), again.get(1))
        self.assertEqual((False, None), again.get(2))
        changed = [dict(c) for c in self.chunks]
        changed[3]["text"] += " Módosítva."
        for other in (self.open(user="u2"), self.open(task="Más feladat."),
                      self.open(lang="English"), self.open(model="m2"),
                      self.open(settings={"part_chars": 240}), self.open(chunks=changed)):
            self.assertEqual((False, None), other.get(0))
        if os.name == "posix":
            (name,) = os.listdir(self.root)
            self.assertEqual(0o600, os.stat(os.path.join(self.root, name)).st_mode & 0o777)

    def test_old_entries_expire_and_a_broken_store_does_not_stop_the_analysis(self):
        store = analysis_checkpoint.CheckpointStore(self.root, retention_days=1)
        self.open(store).put(0, None)
        (name,) = os.listdir(self.root)
        old = time.time() - 2 * 86400
        os.utime(os.path.join(self.root, name), (old, old))
        self.assertEqual(1, store.sweep(now=time.time() + 7200))
        self.assertEqual([], os.listdir(self.root))
        checkpoint = self.open(store)
        os.rmdir(self.root)  # the volume went away under it
        checkpoint.put(0, None)
        self.assertEqual((True, None), checkpoint.get(0))

    def test_from_env(self):
        jobs = os.path.join(self.tmp.name, "jobs")
        store = analysis_checkpoint.CheckpointStore.from_env({"ADAPTER_JOBS_DIR": jobs + "/"})
        self.assertEqual(os.path.join(self.tmp.name, "analysis"), store.root)
        self.assertFalse(analysis_checkpoint.CheckpointStore.from_env(
            {"ADAPTER_JOBS_DIR": jobs, "ADAPTER_ANALYSIS_DIR": "off"}).enabled())
        self.assertFalse(analysis_checkpoint.CheckpointStore.from_env({}).enabled())
        self.assertIsNone(analysis_checkpoint.CheckpointStore.from_env({}).open(
            "u", self.chunks, "t", None, "m", {}))
        store = analysis_checkpoint.CheckpointStore.from_env(
            {"ADAPTER_ANALYSIS_DIR": self.root, "ADAPTER_ANALYSIS_RETENTION_DAYS": "x"})
        self.assertEqual((self.root, 7 * 86400), (store.root, store.retention_seconds))


class ResumeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = analysis_checkpoint.CheckpointStore(self.tmp.name)
        self.chunks = [page_chunk(n, n // 2 + 1) for n in range(12)]

    def read(self, chat, user="u1", cancel_event=None):
        checkpoint = self.store.open(user, self.chunks, "Elemezd.", "Hungarian", "m", SETTINGS)
        with mock.patch.object(deep, "DOC_BATCH_CHARS", 120):
            return deep.read_document(self.chunks, "Elemezd.", "Hungarian", "szerzodes.pdf",
                                      chat=chat, cancel_event=cancel_event,
                                      checkpoint=checkpoint)

    def test_an_interrupted_analysis_reads_only_the_rest(self):
        stop = threading.Event()
        seen = []

        def section(user):
            seen.append(user)
            if len(seen) == 3:
                stop.set()  # the user cancels while the third part is being read
            return finding(user)

        with self.assertRaises(llm.GenerationCancelled):
            self.read(Recorder(section=section), cancel_event=stop)
        self.assertEqual(3, len(seen))
        fake = Recorder(section=finding)
        contexts, info = self.read(fake)
        groups = info["groups"]
        self.assertEqual((3, groups - 3), (info["resumed"], len(fake.calls)))
        self.assertEqual((groups, False), (info["parts"], info["partial"]))
        self.assertIn("3 r\u00e9szt a(z) %d k\u00f6z\u00fcl ennek az elemz\u00e9snek" % groups,
                      contexts[0]["text"])
        # The parts come in reading order, the resumed ones first as in the document.
        pages = [p["page_number"] for p in contexts[1:]]
        self.assertEqual(sorted(pages), pages)
        # Another user starts from the beginning.
        fake = Recorder(section=finding)
        _, info = self.read(fake, user="u2")
        self.assertEqual((0, groups), (info["resumed"], len(fake.calls)))

    def test_a_failed_part_is_read_again(self):
        replies = iter(["nem json", "megint nem"])

        def section(user):
            return next(replies, None) or finding(user)

        _, info = self.read(Recorder(section=section))
        self.assertEqual(1, info["failed"])
        fake = Recorder(section=finding)
        contexts, info = self.read(fake)
        self.assertEqual((info["groups"] - 1, 1, 0), (info["resumed"], len(fake.calls),
                                                      info["failed"]))
        self.assertFalse(info["partial"])


if __name__ == "__main__":
    unittest.main()
