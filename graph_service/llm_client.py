"""JSON-constrained calls to the local language model (E26 extraction).

Two wire formats, chosen by GRAPH_LLM_API, so the same pipeline runs against
the shared Ollama (default) or a vLLM server (OpenAI-compatible):
  ollama  POST {GRAPH_LLM_URL}/api/chat              think:false, format:<schema>
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


class LLMClient:
    def __init__(self, api: str | None = None, url: str | None = None, model: str | None = None,
                 timeout: float | None = None, num_ctx: int | None = None):
        self.api = (api or os.environ.get("GRAPH_LLM_API", "ollama")).strip().lower()
        if self.api not in ("ollama", "openai"):
            raise ValueError("GRAPH_LLM_API must be ollama or openai")
        self.url = (url or os.environ.get("GRAPH_LLM_URL", "http://127.0.0.1:11434")).rstrip("/")
        self.model = model or os.environ.get("GRAPH_LLM_MODEL", "qwen3.6:35b")
        self.timeout = float(timeout or os.environ.get("GRAPH_LLM_TIMEOUT", "600"))
        self.num_ctx = int(num_ctx or os.environ.get("GRAPH_LLM_NUM_CTX", "16384"))
        self.calls = 0
        self.seconds = 0.0

    def json_call(self, system: str, user: str, schema: dict, max_tokens: int = 4096,
                  retries: int = 1) -> dict:
        for attempt in range(retries + 1):
            try:
                return self._json_call(system, user, schema, max_tokens)
            except LLMError:
                if attempt == retries:
                    raise

    def _json_call(self, system: str, user: str, schema: dict, max_tokens: int) -> dict:
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        if self.api == "ollama":
            path = "/api/chat"
            body = {"model": self.model, "messages": messages, "stream": False, "think": False,
                    "format": schema,
                    "options": {"temperature": 0, "num_ctx": self.num_ctx,
                                "num_predict": max_tokens}}
        else:
            path = "/v1/chat/completions"
            body = {"model": self.model, "messages": messages, "temperature": 0,
                    "max_tokens": max_tokens,
                    "response_format": {"type": "json_schema",
                                        "json_schema": {"name": "extraction", "schema": schema}},
                    "chat_template_kwargs": {"enable_thinking": False}}
        req = urllib.request.Request(self.url + path, data=json.dumps(body).encode("utf-8"),
                                     headers={"Content-Type": "application/json"}, method="POST")
        started = time.time()
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                payload = json.loads(resp.read())
        except (urllib.error.URLError, TimeoutError, ValueError) as e:
            raise LLMError("model call failed: %s" % e)
        finally:
            self.calls += 1
            self.seconds += time.time() - started
        content = payload["message"]["content"] if self.api == "ollama" \
            else payload["choices"][0]["message"]["content"]
        try:
            return json.loads(content)
        except ValueError:
            raise LLMError("model returned invalid JSON")
