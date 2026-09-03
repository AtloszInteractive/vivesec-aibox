"""Indexing a large document must not make the service go silent.

Embedding a multi-thousand-chunk document takes minutes. It used to run while
the store lock was held, so every search queued behind it and timed out. These
tests pin the behaviour down with a deliberately slow embedder:

    python rag_service/concurrency_test.py
"""
import os
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "poc"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import embeddings
import store


EMBED_SECONDS = 1.0


class SlowEmbedStore(unittest.TestCase):
    def setUp(self):
        self.store = store.RagStore()
        self.real_embed_many = embeddings.embed_many
        self.embedding = threading.Event()

        def slow_embed_many(texts, use_ollama):
            # Only the bulk ingest call is slowed; the single-text query
            # embedding stays fast, as it is against a real model server.
            if len(texts) > 1:
                self.embedding.set()
                time.sleep(EMBED_SECONDS)
            return self.real_embed_many(texts, use_ollama)

        embeddings.embed_many = slow_embed_many

    def tearDown(self):
        embeddings.embed_many = self.real_embed_many

    def _ingest_in_background(self, text):
        thread = threading.Thread(
            target=self.store.ingest_text,
            args=("drive_hr", "default", "/drive_hr/big.txt", "big", text, {}),
            daemon=True,
        )
        thread.start()
        return thread

    def test_search_answers_while_a_document_is_embedding(self):
        # Seed one small document so there is something to search for.
        self.store.ingest_text("drive_hr", "default", "/drive_hr/policy.txt",
                               "policy", "vacation policy is twenty days", {})

        thread = self._ingest_in_background(" ".join(["word"] * 5000))
        self.assertTrue(self.embedding.wait(timeout=5), "ingest never started embedding")

        start = time.time()
        contexts, _ = self.store.search_context("drive_hr", "vacation policy", top_k=3)
        elapsed = time.time() - start

        self.assertLess(elapsed, EMBED_SECONDS * 0.5,
                        "search waited for the ingest to finish (%.2fs)" % elapsed)
        self.assertTrue(contexts)
        thread.join(timeout=30)

    def test_stats_answers_while_a_document_is_embedding(self):
        thread = self._ingest_in_background(" ".join(["word"] * 5000))
        self.assertTrue(self.embedding.wait(timeout=5), "ingest never started embedding")

        start = time.time()
        self.store.stats()
        elapsed = time.time() - start

        self.assertLess(elapsed, EMBED_SECONDS * 0.5,
                        "stats waited for the ingest to finish (%.2fs)" % elapsed)
        thread.join(timeout=30)

    def test_document_is_fully_indexed_after_the_slow_embed(self):
        # The document must not become visible in pieces: it appears once,
        # complete, when the write section runs.
        thread = self._ingest_in_background(" ".join(["alpha"] * 5000))
        self.assertTrue(self.embedding.wait(timeout=5), "ingest never started embedding")

        during = self.store.stats()
        thread.join(timeout=30)
        after = self.store.stats()

        self.assertEqual(0, during.get("documents", 0),
                         "a half-embedded document was already visible")
        self.assertEqual(1, after.get("documents", 0))
        self.assertGreater(after.get("chunks", 0), 1)


if __name__ == "__main__":
    unittest.main()
