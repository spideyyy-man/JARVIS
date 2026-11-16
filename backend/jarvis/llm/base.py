"""Provider-neutral LLM interface plus the failover router.

Neutral message format (what agents build and what every provider adapter consumes):
  {"role": "user", "content": str}
  {"role": "assistant", "content": str, "tool_calls": [ToolCall], "raw": <provider blocks>, "provider": str}
  {"role": "tool", "results": [{"id", "name", "content", "is_error"}]}
"""
from __future__ import annotations

import asyncio
import contextvars
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

OnText = Callable[[str], None]
# Set for the duration of one turn to pin it to a provider (the instant rule-engine "fast path").
# A ContextVar is inherited by the sub-agent tasks a turn spawns, and is invisible to other turns.
FORCE_PROVIDER: contextvars.ContextVar[str | None] = contextvars.ContextVar("jarvis_force_provider", default=None)


@dataclass
class ToolCall:
    id: str
    name: str
    args: dict


@dataclass
class LLMResponse:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    stop_reason: str = "end_turn"
    raw: Any = None
    provider: str = ""
    model: str = ""
    usage: dict = field(default_factory=dict)

    def as_message(self) -> dict:
        return {"role": "assistant", "content": self.text, "tool_calls": self.tool_calls, "raw": self.raw, "provider": self.provider}


class LLMError(Exception):
    def __init__(self, message: str, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


class LLMProvider(Protocol):
    name: str
    model: str

    async def complete(self, system: str, messages: list[dict], tools: list[dict], on_text: OnText | None = None, meta: dict | None = None) -> LLMResponse: ...


class LLMRouter:
    """Tries providers in order. Retryable errors are retried with backoff; anything else (auth,
    bad request, exhausted retries) fails over to the next provider. The offline provider is always
    last, so the assistant degrades instead of dying when an API is down."""

    def __init__(self, providers: list[LLMProvider], on_event: Callable[..., None] | None = None, retries: int = 2, backoff: float = 0.5, cooldown: float = 60.0, timeout: float = 300.0):
        self.providers, self.on_event, self.retries, self.backoff = providers, on_event, retries, backoff
        self.last_used: str = providers[0].name if providers else ""
        self.failures: list[dict] = []
        self.cooldown, self.timeout = cooldown, timeout
        self._down_until: dict[str, float] = {}  # circuit breaker: skip a failed provider for a while

    @property
    def primary(self) -> LLMProvider:
        return self.providers[0]

    def current(self) -> LLMProvider:
        """The provider the next call will try first."""
        forced = FORCE_PROVIDER.get()
        for index, provider in enumerate(self.providers):
            if forced and provider.name != forced:
                continue
            if index == len(self.providers) - 1 or self._down_until.get(provider.name, 0) <= time.monotonic():
                return provider
        return self.providers[-1]

    async def complete(self, system: str, messages: list[dict], tools: list[dict], on_text: OnText | None = None, meta: dict | None = None) -> LLMResponse:
        last_error: Exception | None = None
        forced = FORCE_PROVIDER.get()
        for index, provider in enumerate(self.providers):
            if forced and provider.name != forced and any(p.name == forced for p in self.providers):
                continue
            is_last = index == len(self.providers) - 1
            if not is_last and self._down_until.get(provider.name, 0) > time.monotonic():
                continue  # recently failed: do not make every call of this turn wait on it again
            for attempt in range(self.retries + 1):
                try:
                    resp = await asyncio.wait_for(provider.complete(system, messages, tools, on_text, meta), self.timeout)
                    self.last_used = provider.name
                    return resp
                except asyncio.CancelledError:
                    raise
                except asyncio.TimeoutError:
                    last_error = LLMError(f"{provider.name} did not answer within {self.timeout:.0f}s")
                    self.failures.append({"provider": provider.name, "error": str(last_error), "retried": False})
                    if self.on_event:
                        self.on_event("llm.failover", provider=provider.name, error=str(last_error), retrying=False,
                                      next=self.providers[index + 1].name if index + 1 < len(self.providers) else None)
                    self._down_until[provider.name] = time.monotonic() + self.cooldown
                    break
                except Exception as e:
                    last_error = e
                    retryable = getattr(e, "retryable", False) and attempt < self.retries
                    self.failures.append({"provider": provider.name, "error": f"{type(e).__name__}: {e}"[:300], "retried": retryable})
                    if self.on_event:
                        nxt = provider.name if retryable else (self.providers[index + 1].name if index + 1 < len(self.providers) else None)
                        self.on_event("llm.failover", provider=provider.name, error=str(e)[:300], retrying=retryable, next=nxt)
                    if not retryable:
                        self._down_until[provider.name] = time.monotonic() + self.cooldown
                        break
                    await asyncio.sleep(self.backoff * (2 ** attempt))
        raise LLMError(f"All language-model providers failed. Last error: {last_error}")
