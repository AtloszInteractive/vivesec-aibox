"""Focused tests for the streaming Ollama response adapter."""
import json
import os
import sys
import threading
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import llm


class Response:
    def __init__(self, chunks):
        self.chunks = iter(chunks)
        self.closed = False

    def readline(self):
        try:
            return next(self.chunks)
        except StopIteration:
            return b""

    def close(self):
        self.closed = True


class BlockingResponse:
    def __init__(self):
        self.closed = threading.Event()

    def readline(self):
        self.closed.wait(2)
        return b""

    def close(self):
        self.closed.set()


class StreamTest(unittest.TestCase):
    @staticmethod
    def line(payload):
        return (json.dumps(payload) + "\n").encode("utf-8")

    def test_stream_aggregates_content_thinking_and_final_metrics(self):
        response = Response([
            self.line({"message": {"content": "hello "}, "eval_count": 2}),
            self.line({"message": {"content": "world", "thinking": "step"}}),
            self.line({"message": {}, "eval_count": 7, "eval_duration": 1000000000,
                       "done": True}),
        ])
        progress = []
        with mock.patch.object(llm.urllib.request, "urlopen", return_value=response):
            result = llm._chat(
                "system", "user", stream=True,
                progress_callback=lambda chars, tokens: progress.append((chars, tokens)),
            )
        self.assertEqual(result, ("hello world", 7, 1000000000))
        self.assertEqual(progress[-1], (15, 7))
        self.assertTrue(response.closed)

    def test_cancel_stops_before_next_chunk(self):
        cancel = threading.Event()
        cancel.set()
        response = Response([self.line({"message": {"content": "never"}})])
        with mock.patch.object(llm.urllib.request, "urlopen", return_value=response):
            with self.assertRaises(llm.GenerationCancelled):
                llm._chat("system", "user", stream=True, cancel_event=cancel)
        self.assertTrue(response.closed)

    def test_cancel_closes_blocking_stream(self):
        cancel = threading.Event()
        response = BlockingResponse()
        errors = []

        def run():
            try:
                llm._chat("system", "user", stream=True, cancel_event=cancel)
            except Exception as error:  # noqa: BLE001
                errors.append(error)

        with mock.patch.object(llm.urllib.request, "urlopen", return_value=response):
            thread = threading.Thread(target=run)
            thread.start()
            self.assertTrue(thread.is_alive())
            cancel.set()
            thread.join(1)
        self.assertFalse(thread.is_alive())
        self.assertTrue(response.closed.is_set())
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], llm.GenerationCancelled)


if __name__ == "__main__":
    unittest.main()
