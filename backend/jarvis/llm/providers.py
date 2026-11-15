"""Concrete hosted/local model providers."""
from __future__ import annotations

import json

import httpx

from .base import LLMError, LLMResponse, OnText, ToolCall


class AnthropicProvider:
    """Claude via the official SDK: streaming, adaptive thinking, client tools.

    Not exercised against the live API in this repository's test run (needs a key) - the request
    shape follows the SDK documentation; the adapter logic around it is unit-tested with a stub."""

    name = "anthropic"

    def __init__(self, model: str, effort: str = "medium", api_key: str | None = None, refusal_fallbacks: bool = True):
        import anthropic

        self._anthropic = anthropic
        # api_key=None lets the SDK resolve ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN / `ant auth login` profiles.
        self.client = anthropic.AsyncAnthropic(api_key=api_key) if api_key else anthropic.AsyncAnthropic()
        self.model, self.effort, self.refusal_fallbacks = model, effort, refusal_fallbacks

    def _convert(self, messages: list[dict]) -> list[dict]:
        out: list[dict] = []
        for m in messages:
            if m["role"] == "user":
                out.append({"role": "user", "content": m["content"]})
            elif m["role"] == "assistant":
                if m.get("raw") is not None and m.get("provider") == self.name:
                    out.append({"role": "assistant", "content": m["raw"]})  # unchanged: keeps thinking blocks valid
                else:
                    blocks = ([{"type": "text", "text": m["content"]}] if m["content"] else []) + [
                        {"type": "tool_use", "id": c.id, "name": c.name, "input": c.args} for c in m.get("tool_calls", [])]
                    out.append({"role": "assistant", "content": blocks or [{"type": "text", "text": "(no content)"}]})
            else:  # all results of one assistant turn go back in a single user message
                out.append({"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": r["id"], "content": r["content"], "is_error": bool(r.get("is_error"))} for r in m["results"]]})
        return out

    async def complete(self, system, messages, tools, on_text: OnText | None = None, meta=None) -> LLMResponse:
        a = self._anthropic
        kwargs = dict(
            model=self.model, max_tokens=32000,
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=self._convert(messages),
            thinking={"type": "adaptive"}, output_config={"effort": self.effort},
        )
        if tools:  # eager_input_streaming: inputs are schema-validated again by ToolExecutor before running
            kwargs["tools"] = [{**t, "eager_input_streaming": True} for t in tools]
        try:
            if self.refusal_fallbacks:
                stream_cm = self.client.beta.messages.stream(**kwargs, betas=["server-side-fallback-2026-07-01"], fallbacks="default")
            else:
                stream_cm = self.client.messages.stream(**kwargs)
            async with stream_cm as stream:
                async for text in stream.text_stream:
                    if on_text:
                        on_text(text)
                msg = await stream.get_final_message()
        except a.AuthenticationError as e:
            raise LLMError(f"Anthropic authentication failed: {e}") from e
        except a.RateLimitError as e:
            raise LLMError(f"Anthropic rate limit: {e}", retryable=True) from e
        except a.APIStatusError as e:
            raise LLMError(f"Anthropic API error {e.status_code}: {e}", retryable=e.status_code >= 500) from e
        except a.APIConnectionError as e:
            raise LLMError(f"Cannot reach Anthropic API: {e}", retryable=True) from e
        except ValueError as e:  # un-parseable streamed tool input
            raise LLMError(f"Malformed tool input from model: {e}", retryable=True) from e

        text = "".join(b.text for b in msg.content if b.type == "text")
        calls = [ToolCall(b.id, b.name, dict(b.input)) for b in msg.content if b.type == "tool_use"]
        if msg.stop_reason == "refusal":
            return LLMResponse(text=text or "I can't help with that request.", stop_reason="refusal", provider=self.name, model=msg.model)
        if msg.stop_reason == "max_tokens" and calls:
            # A truncated tool input can parse as a valid partial object. Never run it.
            return LLMResponse(text=(text + "\n\n[Response was cut off before a tool call completed; nothing was executed.]").strip(),
                               stop_reason="max_tokens", provider=self.name, model=msg.model)
        return LLMResponse(text=text, tool_calls=calls, stop_reason=msg.stop_reason or "end_turn", raw=msg.content, provider=self.name, model=msg.model,
                           usage={"input": msg.usage.input_tokens, "output": msg.usage.output_tokens})


class OpenAICompatProvider:
    """Any server that speaks the OpenAI chat-completions protocol (Ollama, LM Studio, vLLM, OpenAI).
    This is the integration point for local models. Non-streaming: the text arrives in one piece."""

    name = "openai_compat"

    def __init__(self, base_url: str, model: str, api_key: str | None = None, transport: httpx.AsyncBaseTransport | None = None):
        self.base_url, self.model, self.api_key, self.transport = base_url.rstrip("/"), model, api_key, transport

    def _convert(self, system: str, messages: list[dict]) -> list[dict]:
        out = [{"role": "system", "content": system}]
        for m in messages:
            if m["role"] == "user":
                out.append({"role": "user", "content": m["content"]})
            elif m["role"] == "assistant":
                msg: dict = {"role": "assistant", "content": m["content"] or None}
                if m.get("tool_calls"):
                    msg["tool_calls"] = [{"id": c.id, "type": "function", "function": {"name": c.name, "arguments": json.dumps(c.args)}} for c in m["tool_calls"]]
                out.append(msg)
            else:
                out.extend({"role": "tool", "tool_call_id": r["id"], "content": r["content"]} for r in m["results"])
        return out

    async def complete(self, system, messages, tools, on_text: OnText | None = None, meta=None) -> LLMResponse:
        body: dict = {"model": self.model, "messages": self._convert(system, messages)}
        if tools:
            body["tools"] = [{"type": "function", "function": {"name": t["name"], "description": t["description"], "parameters": t["input_schema"]}} for t in tools]
        try:
            async with httpx.AsyncClient(timeout=300, transport=self.transport) as client:
                r = await client.post(f"{self.base_url}/chat/completions", json=body,
                                      headers={"Authorization": f"Bearer {self.api_key}"} if self.api_key else {})
        except httpx.HTTPError as e:
            raise LLMError(f"Cannot reach {self.base_url}: {e}", retryable=True) from e
        if r.status_code >= 400:
            raise LLMError(f"{self.base_url} returned {r.status_code}: {r.text[:200]}", retryable=r.status_code in (408, 429) or r.status_code >= 500)
        msg = r.json()["choices"][0]["message"]
        calls = []
        for c in msg.get("tool_calls") or []:
            try:
                calls.append(ToolCall(c["id"], c["function"]["name"], json.loads(c["function"]["arguments"] or "{}")))
            except ValueError as e:
                raise LLMError(f"Malformed tool arguments from model: {e}", retryable=True) from e
        text = msg.get("content") or ""
        if text and on_text:
            on_text(text)
        return LLMResponse(text=text, tool_calls=calls, provider=self.name, model=self.model)
