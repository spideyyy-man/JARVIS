"""In-process event bus. The API layer fans events out to WebSocket clients."""
from __future__ import annotations

import asyncio
import time
from typing import Any


class EventBus:
    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue] = set()
        self.assistant_state = "idle"  # idle | listening | thinking | speaking | executing | error
        self.active_task: dict[str, Any] | None = None

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=500)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subscribers.discard(q)

    def emit(self, type_: str, **data: Any) -> None:
        event = {"type": type_, "ts": time.time(), **data}
        for q in list(self._subscribers):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:  # slow client: drop rather than block the agent loop
                pass

    def set_state(self, state: str, detail: str = "") -> None:
        self.assistant_state = state
        self.emit("assistant.state", state=state, detail=detail)
