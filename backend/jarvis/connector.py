"""The link to the JARVIS Connector browser extension (`extension/` in the repository).

The extension runs in the owner's own Chrome or Edge, where they are signed in, and keeps a WebSocket open to this
server (`/ws/connector`, authenticated with the connector key - a separate key that only opens this socket). JARVIS
sends it one command at a time ("read the form on the active tab", "fill these fields") and waits for the answer.
Everything that changes a page goes through the tools in `tools/tab.py`, i.e. through the approval rules.
"""
from __future__ import annotations

import asyncio
import itertools
import time


class ConnectorError(Exception):
    pass


class ConnectorHub:
    TIMEOUT = 20.0

    def __init__(self):
        self._sock = None
        self.info: dict = {}
        self._pending: dict[int, asyncio.Future] = {}
        self._ids = itertools.count(1)
        self.connected_at = 0.0
        self.attempts: list[dict] = []  # the last connection attempts and how they ended (shown in Settings)
        self._pairing: dict[str, asyncio.Future] = {}  # extension origin -> approved?

    def connected(self) -> bool:
        return self._sock is not None

    def status(self) -> dict:
        return {"connected": self.connected(), "browser": self.info.get("browser", ""), "version": self.info.get("version", ""),
                "since": self.connected_at or None, "last_attempt": self.attempts[-1] if self.attempts else None,
                "pairing": bool(self._pairing)}

    def attempt(self, result: str, origin: str = "") -> None:
        self.attempts = (self.attempts + [{"at": time.strftime("%H:%M:%S"), "result": result, "origin": origin[:60]}])[-10:]

    def pairing_future(self, origin: str) -> asyncio.Future:
        fut = asyncio.get_running_loop().create_future()
        self._pairing[origin] = fut
        return fut

    def end_pairing(self, origin: str) -> None:
        self._pairing.pop(origin, None)

    def answer_pairing(self, origin: str, allowed: bool) -> bool:
        fut = self._pairing.get(origin)
        if fut is None or fut.done():
            return False
        fut.set_result(allowed)
        return True

    def attach(self, sock, hello: dict) -> None:
        """A newer connection replaces an older one (only one browser is driven)."""
        self._sock = sock
        self.info = {"browser": str(hello.get("browser", ""))[:200], "version": str(hello.get("version", ""))[:20]}
        self.connected_at = time.time()

    def detach(self, sock) -> None:
        if self._sock is sock:
            self._sock = None
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_exception(ConnectorError("the browser connector disconnected"))
            self._pending.clear()

    def deliver(self, msg: dict) -> None:
        fut = self._pending.pop(msg.get("id"), None)
        if fut is None or fut.done():
            return
        if msg.get("ok"):
            fut.set_result(msg.get("result"))
        else:
            fut.set_exception(ConnectorError(str(msg.get("error") or "the browser could not do that")[:300]))

    async def call(self, cmd: str, args: dict | None = None, timeout: float | None = None):
        if self._sock is None:
            raise ConnectorError("the JARVIS Connector browser extension is not connected - install it and paste the key from "
                                 "JARVIS Settings > Browser connector")
        cid = next(self._ids)
        fut = asyncio.get_running_loop().create_future()
        self._pending[cid] = fut
        try:
            await self._sock.send_json({"id": cid, "cmd": cmd, "args": args or {}})
            return await asyncio.wait_for(fut, timeout or self.TIMEOUT)
        except asyncio.TimeoutError:
            raise ConnectorError(f"the browser did not answer within {timeout or self.TIMEOUT:.0f} s") from None
        finally:
            self._pending.pop(cid, None)
