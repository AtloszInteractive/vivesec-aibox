"""JSON-constrained calls to the local language model (E26 extraction).

Two wire formats, chosen by GRAPH_LLM_API, so the same pipeline runs against
the shared Ollama (default) or a vLLM server (OpenAI-compatible):
  ollama  POST {GRAPH_LLM_URL}/api/chat              think (GRAPH_LLM_THINK), format:<schema>
  openai  POST {GRAPH_LLM_URL}/v1/chat/completions   response_format json_schema
On the appliance GRAPH_LLM_URL points at the adapter's GPU broker, not at the
model server directly, so extraction queues behind interactive chat.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request


class LLMError(RuntimeError):
    pass


class LLMTruncated(LLMError):
    """The reply ran into max_tokens. Not retried: at temperature 0 the same
    input runs into it again; a shorter input (half the excerpt) may not."""


class LLMClient:
    def __init__(self, api: str | None = None, url: str | None = None, model: str | None = None,
                 timeout: float | None = None, num_ctx: int | None = None,
                 think: bool | None = None):
        self.api = (api or os.environ.get("GRAPH_LLM_API", "ollama")).strip().lower()
        if self.api not in ("ollama", "openai"):
            raise ValueError("GRAPH_LLM_API must be ollama or openai")
        self.url = (url or os.environ.get("GRAPH_LLM_URL", "http://127.0.0.1:11434")).rstrip("/")
        self.model = model or os.environ.get("GRAPH_LLM_MODEL", "qwen3.6:35b")
        self.timeout = float(timeout or os.environ.get("GRAPH_LLM_TIMEOUT", "600"))
        self.num_ctx = int(num_ctx or os.environ.get("GRAPH_LLM_NUM_CTX", "16384"))
        # Reasoning before the answer: off unless GRAPH_LLM_THINK=on (it multiplies the
        # tokens generated, and generation is nearly all of the extraction time).
        self.think = (think if think is not None else
                      os.environ.get("GRAPH_LLM_THINK", "off").strip().lower() == "on")
        self.calls = 0
        self.seconds = 0.0
        # What the model server reports (Ollama: *_count, *_duration; OpenAI: usage).
        self.prompt_tokens = 0
        self.output_tokens = 0
        self.prompt_seconds = 0.0
        self.eval_seconds = 0.0
        self.load_seconds = 0.0
        self.truncated = 0  # replies cut off at max_tokens
        # When a list, one dict per call is appended (measurement runs).
        self.call_log: list | None = None

    def json_call(self, system: str, user: str, schema: dict, max_tokens: int = 4096,
                  retries: int = 1) -> dict:
        for attempt in range(retries + 1):
            try:
                return self._json_call(system, user, schema, max_tokens)
            except LLMTruncated:
                raise
            except LLMError:
                if attempt == retries:
                    raise

    def _json_call(self, system: str, user: str, schema: dict, max_tokens: int) -> dict:
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        if self.api == "ollama":
            path = "/api/chat"
            body = {"model": self.model, "messages": messages, "stream": False,
                    "think": self.think, "format": schema,
                    "options": {"temperature": 0, "num_ctx": self.num_ctx,
                                "num_predict": max_tokens}}
        else:
            path = "/v1/chat/completions"
            body = {"model": self.model, "messages": messages, "temperature": 0,
                    "max_tokens": max_tokens,
                    "response_format": {"type": "json_schema",
                                        "json_schema": {"name": "extraction", "schema": schema}},
                    "chat_template_kwargs": {"enable_thinking": self.think}}
        req = urllib.request.Request(self.url + path, data=json.dumps(body).encode("utf-8"),
                                     headers={"Content-Type": "application/json"}, method="POST")
        started = time.time()
        payload = None
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                payload = json.loads(resp.read())
        except (urllib.error.URLError, TimeoutError, ValueError) as e:
            raise LLMError("model call failed: %s" % e)
        finally:
            elapsed = time.time() - started
            self.calls += 1
            self.seconds += elapsed
            usage = self._usage(payload) if isinstance(payload, dict) else {}
            self.prompt_tokens += usage.get("prompt_tokens", 0)
            self.output_tokens += usage.get("output_tokens", 0)
            self.prompt_seconds += usage.get("prompt_seconds", 0.0)
            self.eval_seconds += usage.get("eval_seconds", 0.0)
            self.load_seconds += usage.get("load_seconds", 0.0)
            self.truncated += 1 if usage.get("truncated") else 0
            if self.call_log is not None:
                self.call_log.append(dict(usage, seconds=round(elapsed, 3),
                                          input_chars=len(system) + len(user),
                                          max_tokens=max_tokens, ok=payload is not None))
        content = payload["message"]["content"] if self.api == "ollama" \
            else payload["choices"][0]["message"]["content"]
        if usage.get("truncated"):
            raise LLMTruncated("model reply cut off at %d tokens" % max_tokens)
        try:
            return json.loads(content)
        except ValueError:
            raise LLMError("model returned invalid JSON")

    def _usage(self, payload: dict) -> dict:
        if self.api == "ollama":
            ns = 1e-9
            return {"prompt_tokens": int(payload.get("prompt_eval_count") or 0),
                    "output_tokens": int(payload.get("eval_count") or 0),
                    "prompt_seconds": (payload.get("prompt_eval_duration") or 0) * ns,
                    "eval_seconds": (payload.get("eval_duration") or 0) * ns,
                    "load_seconds": (payload.get("load_duration") or 0) * ns,
                    "truncated": payload.get("done_reason") == "length"}
        usage = payload.get("usage") or {}
        choices = payload.get("choices") or [{}]
        return {"prompt_tokens": int(usage.get("prompt_tokens") or 0),
                "output_tokens": int(usage.get("completion_tokens") or 0),
                "truncated": choices[0].get("finish_reason") == "length"}
