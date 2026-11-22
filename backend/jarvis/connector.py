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
    copied = True
    try:  # clip.exe starts in a moment; PowerShell's Set-Clipboard timed out on the busy laptop (2026-11-22)
        subprocess.run(["clip"], input=str(extension_dir).encode("utf-16-le"), creationflags=flags, timeout=10, check=True)
    except Exception:  # noqa: BLE001 - the folder is open in Explorer anyway
        copied = False
    return {"browser": browser, "folder": str(extension_dir), "opened": True, "copied": copied}


class ConnectorHub:
    TIMEOUT = 20.0

    def __init__(self):
        # Every connected browser (the owner has several Chrome profiles and Edge). Commands go to the one the owner used
        # last: the extension reports when one of its windows gets focus, and a popup button counts as use.
        self._socks: dict = {}  # socket -> {"info", "connected_at", "used_at"}
        self._pending: dict[int, tuple[asyncio.Future, object]] = {}
        self._ids = itertools.count(1)
        self.attempts: list[dict] = []  # the last connection attempts and how they ended (shown in Settings)
        self._pairing: dict[str, asyncio.Future] = {}  # extension origin -> approved?
        self._form_sock = None

    @property
    def _sock(self):
        if not self._socks:
            return None
        return max(self._socks.items(), key=lambda kv: (kv[1]["used_at"], kv[1]["connected_at"]))[0]

    @property
    def info(self) -> dict:
        s = self._sock
        return self._socks[s]["info"] if s is not None else {}

    @property
    def connected_at(self) -> float:
        s = self._sock
        return self._socks[s]["connected_at"] if s is not None else 0.0

    def connected(self) -> bool:
        return bool(self._socks)

    def status(self) -> dict:
        return {"connected": self.connected(), "browser": self.info.get("browser", ""), "version": self.info.get("version", ""),
                "since": self.connected_at or None, "last_attempt": self.attempts[-1] if self.attempts else None,
                "pairing": bool(self._pairing), "browsers": len(self._socks), "attempts": self.attempts[-6:],
                "all": [{"browser": v["info"].get("browser", "")[-60:], "version": v["info"].get("version", ""),
                         "active": s is self._sock} for s, v in self._socks.items()]}

    def used(self, sock) -> None:
        """This browser was just used (a window focused, a popup button): it gets the next commands."""
        if sock in self._socks:
            self._socks[sock]["used_at"] = time.time()

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
        """A newly connected browser is the one used until another is focused."""
        now = time.time()
        self._socks[sock] = {"info": {"browser": str(hello.get("browser", ""))[:200], "version": str(hello.get("version", ""))[:20]},
                             "connected_at": now, "used_at": now}

    def detach(self, sock) -> None:
        if self._socks.pop(sock, None) is None:
            return
        for cid, (fut, s) in list(self._pending.items()):
            if s is sock:
                self._pending.pop(cid, None)
                if not fut.done():
                    fut.set_exception(ConnectorError("the browser connector disconnected"))

    def deliver(self, msg: dict) -> None:
        fut, _ = self._pending.pop(msg.get("id"), (None, None))
        if fut is None or fut.done():
            return
        if msg.get("ok"):
            fut.set_result(msg.get("result"))
        else:
            fut.set_exception(ConnectorError(str(msg.get("error") or "the browser could not do that")[:300]))

    async def call(self, cmd: str, args: dict | None = None, timeout: float | None = None):
        sock = self._sock
        if cmd == "fill" and self._form_sock in self._socks:
            sock = self._form_sock  # the fill goes to the browser whose form was read (and shown in the approval)
        if sock is None:
            raise ConnectorError(NOT_CONNECTED)
        if cmd == "read_form":
            self._form_sock = sock
        cid = next(self._ids)
        fut = asyncio.get_running_loop().create_future()
        self._pending[cid] = (fut, sock)
        try:
            await sock.send_json({"id": cid, "cmd": cmd, "args": args or {}})
            return await asyncio.wait_for(fut, timeout or self.TIMEOUT)
        except asyncio.TimeoutError:
            raise ConnectorError(f"the browser did not answer within {timeout or self.TIMEOUT:.0f} s") from None
        finally:
            self._pending.pop(cid, None)
