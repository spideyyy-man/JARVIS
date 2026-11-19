"""In-process event bus. The API layer fans events out to WebSocket clients."""
from __future__ import annotations

import asyncio
import collections
import time
from typing import Any


def _short(text: Any, n: int = 90) -> str:
    t = " ".join(str(text or "").split())
    return t if len(t) <= n else t[: n - 1] + "…"


def describe(event: dict) -> str | None:
    """One readable line for the activity log (the floating icon's hover panel), or None for noise."""
    t = event["type"]
    if t == "voice.wake":
        return "Listening (" + event.get("trigger", "wake word") + ")"
    if t == "voice.heard":
        return f"Heard [{event.get('language', '?')}, {event.get('seconds', '?')} s, {event.get('model', '?')}]: {_short(event.get('text'))}"
    if t == "voice.slow":
        return f"Too slow ({event.get('seconds', 0):.0f} s), not acted on: {_short(event.get('text'))}"
    if t == "voice.state" and event.get("note"):
        return "Voice: " + event["note"]
    if t == "voice.state" and event.get("state") == "error":
        return "Voice error: " + _short(event.get("error"))
    if t == "chat.start":
        return "Request: " + _short((event.get("message") or {}).get("content"))
    if t == "chat.done":
        return "Reply: " + _short((event.get("message") or {}).get("content"))
    if t == "tool.executed":
        return f"{'Ran' if event.get('ok') else 'Failed'}: {event.get('tool')}"
    if t == "approval.requested":
        ap = event.get("approval") or {}
        return f"Needs approval: {ap.get('tool')} - {_short((ap.get('preview') or '').splitlines()[0] if ap.get('preview') else '', 70)}"
    if t == "approval.decided":
        ap = event.get("approval") or {}
        return f"Approval {ap.get('status')}: {ap.get('tool')}"
    if t == "assistant.state" and event.get("state") == "executing":
        return "Working: " + _short(event.get("detail"))
    if t == "selftest.progress" and event.get("line"):
        return "Self-test: " + _short(event["line"])
    return None


class EventBus:
    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue] = set()
        self.assistant_state = "idle"  # idle | listening | thinking | speaking | executing | error
        self.state_detail = ""
        self.active_task: dict[str, Any] | None = None
        self.activity: collections.deque = collections.deque(maxlen=60)  # readable recent history: [{"ts", "text"}]

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=500)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subscribers.discard(q)

    def emit(self, type_: str, **data: Any) -> None:
        event = {"type": type_, "ts": time.time(), **data}
        line = describe(event)
        if line:
            self.activity.append({"ts": event["ts"], "text": line})
        for q in list(self._subscribers):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:  # slow client: drop rather than block the agent loop
                pass

    def set_state(self, state: str, detail: str = "") -> None:
        self.assistant_state = state
        self.state_detail = detail
        self.emit("assistant.state", state=state, detail=detail)
