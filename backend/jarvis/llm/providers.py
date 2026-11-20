"""Concrete hosted/local model providers."""
from __future__ import annotations

import json
import time

import httpx

from .base import LLMError, LLMResponse, OnText, ToolCall


def _strip_think(text: str) -> str:
    import re

    return re.sub(r"<think>.*?(</think>|$)", "", text, flags=re.S).strip()


def visible_text(text: str) -> str:
    """What may be shown of a reply that is still streaming: <think> blocks removed (an unclosed one hides everything
    after it), leading whitespace dropped, and a trailing fragment that could be the start of a tag held back."""
    import re

    visible = re.sub(r"<think>.*?(</think>|$)", "", text, flags=re.S).lstrip()
    for k in range(min(len(visible), len("</think>")), 0, -1):
        tail = visible[-k:]
        if "<think>".startswith(tail) or "</think>".startswith(tail):
            return visible[:-k]
    return visible


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
    This is the integration point for local models. Streams tokens (and tool-call fragments) over SSE; set JARVIS_OPENAI_STREAM=0 for servers that cannot."""

    name = "openai_compat"

    def __init__(self, base_url: str, model: str, api_key: str | None = None, transport: httpx.AsyncBaseTransport | None = None, stream: bool = True,
                 keep_alive: str = "", max_tokens: int = 0):
        self.base_url, self.model, self.api_key, self.transport, self.stream = base_url.rstrip("/"), model, api_key, transport, stream
        # Ollama only: how long the model stays loaded after a request ("2h", "-1"). Unloading means the next request
        # reloads it and re-reads the whole prompt - 20 s or more on a CPU. Other servers ignore the field.
        self.keep_alive = keep_alive
        # Cap on one reply's length (0 = server default). A small model sometimes never stops: on the owner's laptop one
        # Tamil answer ran past 15 minutes. JARVIS's answers are meant to be spoken, so a cap costs nothing.
        self.max_tokens = max_tokens

    def _tool_specs(self, tools: list[dict]) -> list[dict]:
        return [{"type": "function", "function": {"name": t["name"], "description": t["description"], "parameters": t["input_schema"]}} for t in tools]

    async def warm(self, system: str, tools: list[dict], keep_alive: str | None = None) -> dict:
        """Load the model and have it read `system` + `tools` once, generating a single token. Ollama keeps the
        processed prompt in its cache, so a later request that starts with the same system prompt and tools only
        pays for its own new tokens (measured: 1.1 s instead of 21 s for a 900-token prefix). Returns the usage."""
        body: dict = {"model": self.model, "messages": [{"role": "system", "content": system}, {"role": "user", "content": "hi"}],
                      "max_tokens": 1, "stream": False}
        if tools:
            body["tools"] = self._tool_specs(tools)
        ka = self.keep_alive if keep_alive is None else keep_alive
        if ka:
            body["keep_alive"] = ka
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(600, connect=10), transport=self.transport) as client:
                r = await client.post(f"{self.base_url}/chat/completions", json=body, headers=headers)
        except httpx.HTTPError as e:
            raise LLMError(f"Cannot reach {self.base_url}: {e}", retryable=True) from e
        self._raise_for_status(r.status_code, r.text)
        return r.json().get("usage") or {}

    async def unload(self) -> None:
        """Ask Ollama to free the model's memory now (best effort; other servers have no such call)."""
        root = self.base_url.removesuffix("/v1")
        try:
            async with httpx.AsyncClient(timeout=5, transport=self.transport) as client:
                await client.post(f"{root}/api/generate", json={"model": self.model, "keep_alive": 0})
        except httpx.HTTPError:
            pass

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
            body["tools"] = self._tool_specs(tools)
        if self.keep_alive:
            body["keep_alive"] = self.keep_alive
        if self.max_tokens:
            body["max_tokens"] = self.max_tokens
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        url = f"{self.base_url}/chat/completions"
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(600, connect=10), transport=self.transport) as client:
                if self.stream:
                    resp = await self._streamed(client, url, {**body, "stream": True, "stream_options": {"include_usage": True}}, headers, on_text)
                    if resp.text or resp.tool_calls:
                        return resp
                    # Nothing came back. Some servers silently drop tool calls when streaming: ask again in one piece.
                r = await client.post(url, json=body, headers=headers)
        except httpx.HTTPError as e:
            raise LLMError(f"Cannot reach {self.base_url}: {e}", retryable=True) from e
        self._raise_for_status(r.status_code, r.text)
        msg = r.json()["choices"][0]["message"]
        calls = [ToolCall(c.get("id") or f"call_{i}", c["function"]["name"], self._args(c["function"].get("arguments"))) for i, c in enumerate(msg.get("tool_calls") or [])]
        text = _strip_think(msg.get("content") or "")
        if text and on_text:
            on_text(text)
        return LLMResponse(text=text, tool_calls=calls, provider=self.name, model=self.model, usage=_usage(r.json().get("usage")))

    def _raise_for_status(self, status: int, text: str) -> None:  # noqa: D401
        if status >= 400:
            raise LLMError(f"{self.base_url} returned {status}: {text[:200]}", retryable=status in (408, 429) or status >= 500)

    @staticmethod
    def _args(raw):
        """Arguments arrive as a JSON string. Leave unparsable input as-is: the repair layer and the
        executor's validation turn it into an error the model can correct, instead of killing the turn."""
        if isinstance(raw, dict):
            return raw
        try:
            return json.loads(raw or "{}")
        except ValueError:
            return raw

    async def _streamed(self, client, url, body, headers, on_text) -> LLMResponse:
        text, slots, shown, usage = "", {}, 0, {}
        t0, first = time.time(), None
        async with client.stream("POST", url, json=body, headers=headers) as r:
            if r.status_code >= 400:
                self._raise_for_status(r.status_code, (await r.aread()).decode(errors="replace"))
            async for line in r.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except ValueError:
                    continue
                if chunk.get("usage"):
                    usage = chunk["usage"]
                choices = chunk.get("choices") or []
                if not choices:
                    continue
                delta = choices[0].get("delta") or {}
                piece = delta.get("content") or ""
                if piece or delta.get("tool_calls"):
                    first = first or time.time()
                if piece:
                    text += piece
                    # Private working in <think>...</think> (reasoning models; local models also work Tamil answers out
                    # in English there) is never shown or spoken - even when a tag arrives split across pieces.
                    visible = visible_text(text)
                    if on_text and len(visible) > shown:
                        on_text(visible[shown:])
                        shown = len(visible)
                for tc in delta.get("tool_calls") or []:
                    slot = slots.setdefault(tc.get("index", 0), {"id": "", "name": "", "args": ""})
                    fn = tc.get("function") or {}
                    slot["id"] = tc.get("id") or slot["id"]
                    slot["name"] += fn.get("name") or ""
                    args = fn.get("arguments")
                    slot["args"] += args if isinstance(args, str) else (json.dumps(args) if args else "")
        calls = [ToolCall(s["id"] or f"call_{i}", s["name"], self._args(s["args"])) for i, s in sorted(slots.items()) if s["name"]]
        out = _usage(usage)
        out.update(seconds=round(time.time() - t0, 1), first_token=round(first - t0, 1) if first else None)
        return LLMResponse(text=_strip_think(text), tool_calls=calls, provider=self.name, model=self.model, usage=out)


def _usage(u: dict | None) -> dict:
    """Token counts from an OpenAI-style usage block: prompt, how much of it came from the server's cache, answer."""
    u = u or {}
    return {k: v for k, v in {"prompt": u.get("prompt_tokens"), "cached": (u.get("prompt_tokens_details") or {}).get("cached_tokens"),
                               "output": u.get("completion_tokens")}.items() if v is not None}
