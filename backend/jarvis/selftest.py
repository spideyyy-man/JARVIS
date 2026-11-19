"""End-to-end self-test: talk to JARVIS like the user would and check what really happened on disk and in the browser.

    1. create  - "Create a file ... an HTML page with a script that works out 6 x 7"   -> the file exists and has a script
    2. browser - "Open it in your browser and tell me what it says"                    -> the page ran the script: "42"
    3. delete  - "Delete the folder"                                                    -> the folder is gone (JARVIS trash)

Every request goes through the normal chat path (model, tools, approvals). Approvals are decided by the test, which the
user started from the floating icon: it approves only the actions it expects for the step, on paths inside its own
throw-away folder, and denies anything else (recorded in the audit log as actor "selftest"). Results are kept in
<data_dir>/selftest/last.json.
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

STEP_TIMEOUT = 8 * 60  # a slow local model under load can take minutes per step


class SelfTest:
    def __init__(self, app):
        self.app = app
        stamp = time.strftime("%Y%m%d-%H%M%S")
        self.folder: Path = app.guard.roots[0] / f"JARVIS-selftest-{stamp}"
        self.page = self.folder / "hello.html"
        self.conversation = f"c_selftest_{stamp}"
        self.steps = [{"name": n, "title": t, "status": "pending", "seconds": None, "reply": "", "detail": ""} for n, t in (
            ("create", "Create an HTML file with code"), ("browser", "Run it in the browser"), ("delete", "Delete it"))]
        self.state = {"running": False, "started": None, "finished": None, "folder": str(self.folder), "conversation_id": self.conversation, "steps": self.steps}
        self._allowed: set[str] = set()

    # -- progress ----------------------------------------------------------------------------------------------------
    def _report(self, line: str) -> None:
        self.app.bus.emit("selftest.progress", line=line, state=self.state)

    def summary(self) -> dict:
        return self.state

    # -- approvals ------------------------------------------------------------------------------------------------------
    def _inside(self, value: str) -> bool:
        try:
            p = Path(value).expanduser().resolve()
        except (OSError, ValueError):
            return False
        return p == self.folder or self.folder in p.parents

    def acceptable(self, tool: str, args: dict) -> bool:
        """Approve only what this step needs, and only inside the test folder."""
        if tool not in self._allowed:
            return False
        paths = [v for k, v in args.items() if isinstance(v, str) and k in ("path", "source", "destination")]
        return bool(paths) and all(self._inside(p) for p in paths)

    async def _approvals(self) -> None:
        while True:
            for ap in self.app.db.query("SELECT * FROM approvals WHERE status='pending' AND conversation_id=?", (self.conversation,)):
                args = ap["args"] if isinstance(ap["args"], dict) else json.loads(ap["args"] or "{}")
                ok = self.acceptable(ap["tool"], args)
                try:
                    await self.app.executor.decide(ap["id"], ok, by="selftest")
                except Exception as e:  # noqa: BLE001 - e.g. already decided by the user on screen
                    self._report(f"could not decide {ap['tool']}: {e}")
                    continue
                self._report(f"{'approved' if ok else 'DENIED (not expected)'} {ap['tool']} {json.dumps(args)[:120]}")
                if not ok:
                    self._current.setdefault("unexpected", []).append(ap["tool"])
            await asyncio.sleep(0.4)

    # -- running one request ---------------------------------------------------------------------------------------------
    async def _settle(self, deadline: float) -> None:
        """Wait until the request, its approvals and the automatic continuation after them have all finished."""
        quiet_since = None
        while time.time() < deadline:
            pending = self.app.db.one("SELECT id FROM approvals WHERE status='pending' AND conversation_id=?", (self.conversation,))
            task = self.app._chat_tasks.get(self.conversation)
            busy = pending or (task and not task.done()) or self.app._background
            if busy:
                quiet_since = None
            elif quiet_since is None:
                quiet_since = time.time()
            elif time.time() - quiet_since > 1.5:
                return
            await asyncio.sleep(0.3)
        raise TimeoutError(f"no answer within {STEP_TIMEOUT // 60} minutes")

    async def _ask(self, step: dict, prompt: str, allowed: set[str]) -> tuple[str, list[dict]]:
        self._allowed = allowed
        started = time.time()
        # Its own task, like a request from the window or the voice listener: JARVIS's automatic continuation after an
        # approval waits for the task that ran the request, and that must not be this test (it would wait for itself).
        turn = asyncio.create_task(self.app.chat(prompt, self.conversation, source="selftest"))
        await asyncio.wait_for(turn, STEP_TIMEOUT)
        await self._settle(started + STEP_TIMEOUT)
        rows = self.app.db.query("SELECT content, meta FROM messages WHERE conversation_id=? AND role='assistant' AND created>=? ORDER BY created", (self.conversation, started))
        tools = [t for r in rows for t in ((r["meta"] if isinstance(r["meta"], dict) else json.loads(r["meta"] or "{}")).get("tools") or [])]
        reply = rows[-1]["content"] if rows else ""
        step["reply"] = reply[:600]
        return reply, tools

    # -- the test ------------------------------------------------------------------------------------------------------------
    async def run(self) -> dict:
        self.state.update(running=True, started=time.time())
        self._report(f"started in {self.folder}")
        watcher = asyncio.create_task(self._approvals())
        try:
            for step in self.steps:
                self._current = step
                step["status"] = "running"
                self._report(step["title"] + " ...")
                t0 = time.time()
                try:
                    await getattr(self, "_step_" + step["name"])(step)
                except Exception as e:  # noqa: BLE001 - a failed step is reported, the next one still runs
                    step["status"], step["detail"] = "failed", (step["detail"] + f" {type(e).__name__}: {e}").strip()
                if step.get("unexpected"):  # JARVIS tried something it was not asked to do: that is a failure too
                    step["status"] = "failed"
                    step["detail"] = (step["detail"] + " Unexpected action denied: " + ", ".join(step["unexpected"]) + ".").strip()
                step["seconds"] = round(time.time() - t0, 1)
                self._report(f"{step['title']}: {step['status'].upper()} in {step['seconds']} s. {step['detail']}".strip())
        finally:
            watcher.cancel()
            self._allowed = set()
            self.state.update(running=False, finished=time.time(), leftover=self.folder.exists())
            if self.folder.exists():
                self._report(f"the test folder is still there: {self.folder}")
            self._save()
            self._report("finished: " + ", ".join(f"{s['name']} {s['status']}" for s in self.steps))
        return self.state

    async def _step_create(self, step: dict) -> None:
        reply, tools = await self._ask(step, (
            f"Create the file {self.page} with a small HTML page: a heading 'JARVIS self-test' and a script that calculates 6 * 7 "
            f"and writes 'Result: ' followed by the answer into a paragraph with the id result."), {"write_file", "create_directory"})
        if not self.page.is_file():
            step["status"], step["detail"] = "failed", (step["detail"] + " The file was not created. Tools used: " + (", ".join(t["tool"] for t in tools) or "none")).strip()
            return
        html = self.page.read_text(encoding="utf-8", errors="replace").lower()
        if "<script" not in html:
            step["status"], step["detail"] = "failed", "The file exists but has no script."
            return
        step["status"], step["detail"] = "passed", f"{self.page.name} written ({len(html)} characters, has a script)."

    async def _step_browser(self, step: dict) -> None:
        if not self.page.is_file():
            step["status"], step["detail"] = "skipped", "No page to open (the previous step failed)."
            return
        if not self.app.capabilities().get("browser_control"):
            step["status"], step["detail"] = "skipped", "Browser control is off. Turn it on (Settings > Capabilities, or the button in the panel) and run the test again."
            return
        reply, tools = await self._ask(step, f"Open {self.page.as_uri()} in your browser and tell me exactly what the page shows.", set())
        opened = [t for t in tools if t["tool"] == "browser_open"]
        if not opened or not any(t.get("ok") for t in opened):
            step["status"], step["detail"] = "failed", "JARVIS did not open the page in its browser. Tools used: " + (", ".join(t["tool"] for t in tools) or "none")
        elif "42" not in reply:
            step["status"], step["detail"] = "failed", "The page was opened but the reply does not mention the script's result (42)."
        else:
            step["status"], step["detail"] = "passed", "The page ran its script in the browser and JARVIS read the result (42)."
        try:
            await self.app.browser.close()
        except Exception:  # noqa: BLE001
            pass

    async def _step_delete(self, step: dict) -> None:
        if not self.folder.exists():
            step["status"], step["detail"] = "skipped", "Nothing to delete (the folder was never created)."
            return
        await self._ask(step, f"Delete the folder {self.folder}.", {"delete_path"})
        if self.folder.exists():
            step["status"], step["detail"] = "failed", (step["detail"] + " The folder still exists.").strip()
        else:
            step["status"], step["detail"] = "passed", "The folder is gone (moved to JARVIS trash, restorable)."

    def _save(self) -> None:
        out = self.app.settings.data_dir / "selftest"
        out.mkdir(parents=True, exist_ok=True)
        (out / "last.json").write_text(json.dumps(self.state, indent=2, default=str), encoding="utf-8")
