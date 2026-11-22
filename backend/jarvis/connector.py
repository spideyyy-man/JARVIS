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
from pathlib import Path


EXTENSION_DIR = Path(__file__).resolve().parents[2] / "extension"
NOT_CONNECTED = ("your browser isn't connected to me yet. Say \"set up the browser connector\" and I'll open everything you "
                 "need - it takes about a minute")


class ConnectorError(Exception):
    pass


def default_browser() -> str:
    """'edge' or 'chrome' - whichever opens web links on this computer (Windows), else 'edge'."""
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\Shell\Associations\UrlAssociations\https\UserChoice") as k:
            prog = winreg.QueryValueEx(k, "ProgId")[0]
        return "chrome" if prog.lower().startswith("chrome") else "edge"
    except Exception:  # noqa: BLE001
        return "edge"


def open_setup(extension_dir: Path = EXTENSION_DIR) -> dict:
    """"Set up the browser connector": open the default browser's extensions page, show the extension folder in Explorer
    and put its path on the clipboard (for "Load unpacked"). Windows only; started only by the user's own words."""
    import os
    import subprocess

    browser = default_browser()
    if os.name != "nt":
        return {"browser": browser, "folder": str(extension_dir), "opened": False}
    flags = 0x08000000  # CREATE_NO_WINDOW
    page = "edge://extensions" if browser == "edge" else "chrome://extensions"
    exe = "msedge" if browser == "edge" else "chrome"
    subprocess.Popen(["cmd", "/c", "start", "", exe, page], creationflags=flags)
    subprocess.Popen(["explorer", f"/select,{extension_dir}"])
    subprocess.run(["powershell", "-NoProfile", "-Command", "Set-Clipboard -Value $args[0]", str(extension_dir)],
                   creationflags=flags, timeout=15, check=False)
    return {"browser": browser, "folder": str(extension_dir), "opened": True}


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
            raise ConnectorError(NOT_CONNECTED)
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
