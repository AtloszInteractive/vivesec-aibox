"""Token counts and timings the model server reports are kept per client and per call."""
import io
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from llm_client import LLMClient, LLMError, LLMTruncated  # noqa: E402


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def ollama_reply(content, **extra):
    body = {"message": {"content": content}, "done_reason": "stop",
            "prompt_eval_count": 1200, "eval_count": 300,
            "prompt_eval_duration": 2_000_000_000, "eval_duration": 6_000_000_000,
            "load_duration": 500_000_000}
    body.update(extra)
    return _Resp(json.dumps(body).encode("utf-8"))


class LLMClientUsageTest(unittest.TestCase):
    def test_ollama_counts_and_call_log(self):
        llm = LLMClient(api="ollama", url="http://x", model="m", num_ctx=1024)
        llm.call_log = []
        with mock.patch("urllib.request.urlopen", side_effect=[ollama_reply('{"a": 1}'),
                                                               ollama_reply('{"b": 2}')]):
            self.assertEqual(llm.json_call("sys", "user", {}), {"a": 1})
            llm.json_call("sys", "user", {}, max_tokens=8192)
        self.assertEqual((llm.calls, llm.prompt_tokens, llm.output_tokens), (2, 2400, 600))
        self.assertAlmostEqual(llm.prompt_seconds, 4.0)
        self.assertAlmostEqual(llm.eval_seconds, 12.0)
        self.assertAlmostEqual(llm.load_seconds, 1.0)
        self.assertEqual(llm.truncated, 0)
        self.assertEqual([c["max_tokens"] for c in llm.call_log], [4096, 8192])
        self.assertEqual(llm.call_log[0]["input_chars"], len("sys") + len("user"))
        self.assertTrue(all(c["ok"] for c in llm.call_log))

    def test_truncated_reply_is_counted_and_not_retried(self):
        llm = LLMClient(api="ollama", url="http://x", model="m", num_ctx=1024)
        cut = lambda *a, **k: ollama_reply('{"entities": [', done_reason="length")  # noqa: E731
        with mock.patch("urllib.request.urlopen", side_effect=cut):
            with self.assertRaises(LLMTruncated):
                llm.json_call("sys", "user", {}, retries=1)
        self.assertEqual((llm.calls, llm.truncated, llm.output_tokens), (1, 1, 300))

    def test_other_failures_are_retried(self):
        llm = LLMClient(api="ollama", url="http://x", model="m", num_ctx=1024)
        replies = [ollama_reply("not json"), ollama_reply('{"ok": 1}')]
        with mock.patch("urllib.request.urlopen", side_effect=replies):
            self.assertEqual({"ok": 1}, llm.json_call("sys", "user", {}, retries=1))
        self.assertEqual(2, llm.calls)

    def test_failed_call_counts_time_but_no_tokens(self):
        import urllib.error
        llm = LLMClient(api="ollama", url="http://x", model="m", num_ctx=1024)
        llm.call_log = []
        with mock.patch("urllib.request.urlopen", side_effect=urllib.error.URLError("down")):
            with self.assertRaises(LLMError):
                llm.json_call("sys", "user", {}, retries=0)
        self.assertEqual((llm.calls, llm.prompt_tokens), (1, 0))
        self.assertFalse(llm.call_log[0]["ok"])

    def test_openai_usage(self):
        llm = LLMClient(api="openai", url="http://x", model="m", num_ctx=1024)
        body = {"choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 4}}
        with mock.patch("urllib.request.urlopen",
                        return_value=_Resp(json.dumps(body).encode("utf-8"))):
            llm.json_call("s", "u", {})
        self.assertEqual((llm.prompt_tokens, llm.output_tokens, llm.truncated), (10, 4, 0))

    def test_think_is_off_unless_asked_for(self):
        sent = []

        def capture(req, timeout=None):
            sent.append(json.loads(req.data.decode("utf-8")))
            return ollama_reply("{}")
        with mock.patch("urllib.request.urlopen", side_effect=capture):
            with mock.patch.dict(os.environ, {"GRAPH_LLM_THINK": "maybe"}):
                LLMClient(api="ollama", url="http://x", model="m", num_ctx=1).json_call("s", "u", {})
            LLMClient(api="ollama", url="http://x", model="m", num_ctx=1,
                      think=True).json_call("s", "u", {})
        self.assertEqual([False, True], [b["think"] for b in sent])


if __name__ == "__main__":
    unittest.main()
