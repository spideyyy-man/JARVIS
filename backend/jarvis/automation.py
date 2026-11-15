"""Automation engine (scheduled, conditional, multi-step workflows with retries and logs),
the background system monitor, and the daily productivity summary.

Workflow steps run through the same ToolExecutor as chat, so a scheduled workflow gets NO extra
privileges: a confirm-level step pauses the run until the user approves it (unless allowlisted)."""
from __future__ import annotations

import asyncio
import re
import time
from collections import deque
from datetime import datetime
from typing import TYPE_CHECKING, Any

from croniter import croniter

from .db import new_id
from .security import Level
from .tools.registry import Tool, ToolContext, ToolRegistry, ToolResult
from .tools.system import snapshot

if TYPE_CHECKING:
    from .app import Jarvis

OPS = {">": lambda a, b: a > b, ">=": lambda a, b: a >= b, "<": lambda a, b: a < b, "<=": lambda a, b: a <= b,
       "==": lambda a, b: a == b, "!=": lambda a, b: a != b, "contains": lambda a, b: b in a, "exists": lambda a, b: a is not None}
_TEMPLATE = re.compile(r"\{\{\s*([\w.\-]+)\s*\}\}")


def lookup(context: dict, ref: str) -> Any:
    cur: Any = context
    for part in ref.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
            cur = cur[int(part)]
        else:
            return None
    return cur


def render(value: Any, context: dict) -> Any:
    """Substitute {{step_id.path}} references. A string that is exactly one reference keeps its type."""
    if isinstance(value, str):
        whole = _TEMPLATE.fullmatch(value.strip())
        if whole:
            return lookup(context, whole.group(1))
        return _TEMPLATE.sub(lambda m: str(lookup(context, m.group(1))), value)
    if isinstance(value, dict):
        return {k: render(v, context) for k, v in value.items()}
    if isinstance(value, list):
        return [render(v, context) for v in value]
    return value


def condition_holds(when: dict | None, context: dict) -> bool:
    """Structured conditions only ({ref, op, value}) - there is deliberately no expression eval."""
    if not when:
        return True
    left = lookup(context, when["ref"])
    try:
        return bool(OPS[when.get("op", "exists")](left, when.get("value")))
    except TypeError:
        return False


def next_run(schedule: dict, after: float | None = None) -> float | None:
    now = after or time.time()
    kind = schedule.get("type", "manual")
    if kind == "interval":
        return now + max(float(schedule["seconds"]), 5)
    if kind == "cron":
        return croniter(schedule["expr"], datetime.fromtimestamp(now)).get_next(float)
    if kind == "once":
        return float(schedule["at"]) if float(schedule["at"]) > now else None
    return None


class AutomationEngine:
    def __init__(self, app: "Jarvis"):
        self.app = app
        self._running: set[str] = set()
        self._tasks: set[asyncio.Task] = set()
        self._loop_task: asyncio.Task | None = None

    # -- definition ---------------------------------------------------------------
    def validate(self, schedule: dict, steps: list[dict]) -> None:
        kind = schedule.get("type")
        if kind not in ("interval", "cron", "once", "manual"):
            raise ValueError("schedule.type must be interval | cron | once | manual")
        if kind == "interval" and float(schedule.get("seconds", 0)) < 5:
            raise ValueError("interval must be at least 5 seconds")
        if kind == "cron" and not croniter.is_valid(schedule.get("expr", "")):
            raise ValueError(f"invalid cron expression: {schedule.get('expr')!r}")
        if not steps:
            raise ValueError("a workflow needs at least one step")
        seen = set()
        for i, step in enumerate(steps):
            step.setdefault("id", f"s{i + 1}")
            if step["id"] in seen:
                raise ValueError(f"duplicate step id {step['id']}")
            seen.add(step["id"])
            if "tool" in step:
                if self.app.tools.get(step["tool"]) is None:
                    raise ValueError(f"step {step['id']}: unknown tool {step['tool']!r}")
                if step["tool"] in ("delegate", "create_workflow"):
                    raise ValueError(f"step {step['id']}: {step['tool']} cannot be used inside a workflow")
            elif "notify" not in step and "agent_prompt" not in step:
                raise ValueError(f"step {step['id']}: needs one of tool | notify | agent_prompt")
            if step.get("when") and step["when"].get("op", "exists") not in OPS:
                raise ValueError(f"step {step['id']}: unknown operator")

    def create(self, name: str, schedule: dict, steps: list[dict], description: str = "") -> dict:
        self.validate(schedule, steps)
        row = {"id": new_id("wf_"), "name": name, "description": description, "schedule": schedule, "steps": steps,
               "enabled": 1, "next_run": next_run(schedule), "last_run": None, "created": time.time()}
        self.app.db.insert("workflows", row)
        self.app.bus.emit("workflow.changed", workflow_id=row["id"])
        return row

    def list(self) -> list[dict]:
        return self.app.db.query("SELECT * FROM workflows ORDER BY created DESC")

    def set_enabled(self, wf_id: str, enabled: bool) -> None:
        wf = self._get(wf_id)
        self.app.db.update("workflows", wf_id, enabled=int(enabled), next_run=next_run(wf["schedule"]) if enabled else None)
        self.app.bus.emit("workflow.changed", workflow_id=wf_id)

    def delete(self, wf_id: str) -> None:
        self.app.db.execute("DELETE FROM workflows WHERE id=?", (wf_id,))
        self.app.bus.emit("workflow.changed", workflow_id=wf_id)

    def runs(self, wf_id: str | None = None, limit: int = 30) -> list[dict]:
        if wf_id:
            return self.app.db.query("SELECT * FROM workflow_runs WHERE workflow_id=? ORDER BY started DESC LIMIT ?", (wf_id, limit))
        return self.app.db.query("SELECT r.*, w.name AS workflow_name FROM workflow_runs r LEFT JOIN workflows w ON w.id=r.workflow_id ORDER BY r.started DESC LIMIT ?", (limit,))

    def _get(self, wf_id: str) -> dict:
        wf = self.app.db.one("SELECT * FROM workflows WHERE id=?", (wf_id,))
        if wf is None:
            raise KeyError(f"No workflow {wf_id}")
        return wf

    # -- execution -----------------------------------------------------------------
    async def run(self, wf_id: str, trigger: str = "manual") -> dict:
        wf = self._get(wf_id)
        run = {"id": new_id("run_"), "workflow_id": wf_id, "status": "running", "trigger": trigger, "cursor": 0,
               "context": {}, "log": [], "started": time.time(), "finished": None}
        self.app.db.insert("workflow_runs", run)
        self.app.db.update("workflows", wf_id, last_run=run["started"])
        return await self._execute(wf, run)

    async def _execute(self, wf: dict, run: dict) -> dict:
        steps, context, log = wf["steps"], run["context"], run["log"]
        status = "succeeded"
        i = run["cursor"]
        while i < len(steps):
            step = steps[i]
            entry = {"step": step["id"], "kind": step.get("tool") or ("notify" if "notify" in step else "agent"), "started": time.time(), "attempts": 0}
            if not condition_holds(step.get("when"), context):
                entry.update(status="skipped", detail="condition not met")
                log.append(entry)
                i += 1
                continue
            result = ToolResult(False, error="not run")
            retries = int(step.get("retries", 0))
            for attempt in range(retries + 1):
                entry["attempts"] = attempt + 1
                result = await self._run_step(wf, run, step, i, context)
                if result.ok or result.pending:
                    break
                if attempt < retries:
                    await asyncio.sleep(float(step.get("retry_delay", 1)) * (2 ** attempt))
            entry["duration_s"] = round(time.time() - entry["started"], 3)
            if result.pending:
                entry.update(status="waiting_approval", detail=f"approval {result.approval_id}")
                log.append(entry)
                status = "waiting_approval"
                run["cursor"] = i
                break
            if result.ok:
                context[step["id"]] = result.data
                entry.update(status="ok", detail=result.to_model(300))
            else:
                entry.update(status="failed", detail=result.error)
                if not step.get("continue_on_error"):
                    log.append(entry)
                    status = "failed"
                    break
            log.append(entry)
            i += 1
        finished = time.time() if status != "waiting_approval" else None
        self.app.db.update("workflow_runs", run["id"], status=status, cursor=run["cursor"], context=context, log=log, finished=finished)
        run.update(status=status, finished=finished)
        self.app.bus.emit("workflow.run", run_id=run["id"], workflow_id=wf["id"], name=wf["name"], status=status)
        if status == "failed":
            self.app.db.notify("error", f"Workflow '{wf['name']}' failed", log[-1].get("detail") or "")
        return run

    async def _run_step(self, wf: dict, run: dict, step: dict, index: int, context: dict) -> ToolResult:
        try:
            if "notify" in step:
                note = self.app.db.notify(step.get("level", "info"), wf["name"], str(render(step["notify"], context)))
                self.app.bus.emit("notification", notification=note)
                return ToolResult(True, data={"notified": note["body"]})
            if "agent_prompt" in step:
                reply = await self.app.chat(str(render(step["agent_prompt"], context)), conversation_id=None, actor=f"workflow:{wf['id']}")
                return ToolResult(True, data={"reply": reply["text"], "conversation_id": reply["conversation_id"]})
            ctx = ToolContext(self.app, agent="automation", actor=f"workflow:{wf['id']}", origin={"run_id": run["id"], "step_index": index})
            return await self.app.executor.call(step["tool"], render(step.get("args", {}), context) or {}, ctx)
        except Exception as e:
            return ToolResult(False, error=f"{type(e).__name__}: {e}")

    async def on_approval(self, approval: dict, result: ToolResult) -> None:
        """Resume a run that was paused on a confirm-level step."""
        origin = approval.get("origin") or {}
        run = self.app.db.one("SELECT * FROM workflow_runs WHERE id=?", (origin.get("run_id", ""),))
        if not run or run["status"] != "waiting_approval":
            return
        wf = self._get(run["workflow_id"])
        step = wf["steps"][run["cursor"]]
        entry = {"step": step["id"], "kind": step.get("tool"), "started": time.time(), "attempts": 1}
        if result.ok:
            run["context"][step["id"]] = result.data
            entry.update(status="ok", detail="approved by user: " + result.to_model(300))
            run["log"].append(entry)
            run["cursor"] += 1
            await self._execute(wf, run)
        else:
            entry.update(status="failed", detail=result.error)
            run["log"].append(entry)
            self.app.db.update("workflow_runs", run["id"], status="failed", log=run["log"], finished=time.time())
            self.app.bus.emit("workflow.run", run_id=run["id"], workflow_id=wf["id"], name=wf["name"], status="failed")

    # -- scheduler -----------------------------------------------------------------
    def start(self) -> None:
        self._loop_task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        for t in [self._loop_task, *self._tasks]:
            if t:
                t.cancel()

    async def _loop(self) -> None:
        while True:
            try:
                self.tick()
            except Exception as e:  # the scheduler must survive a bad workflow row
                self.app.db.notify("error", "Scheduler error", str(e))
            await asyncio.sleep(1)

    def tick(self) -> list[str]:
        due = self.app.db.query("SELECT * FROM workflows WHERE enabled=1 AND next_run IS NOT NULL AND next_run<=?", (time.time(),))
        started = []
        for wf in due:
            nxt = next_run(wf["schedule"])
            self.app.db.update("workflows", wf["id"], next_run=nxt, **({} if nxt else {"enabled": 0} if wf["schedule"].get("type") == "once" else {}))
            if wf["id"] in self._running:  # never overlap two runs of the same workflow
                continue
            self._running.add(wf["id"])
            task = asyncio.create_task(self._guarded(wf["id"]))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
            started.append(wf["id"])
        return started

    async def _guarded(self, wf_id: str) -> None:
        try:
            await self.run(wf_id, trigger="schedule")
        finally:
            self._running.discard(wf_id)


class Monitor:
    """Monitoring agent's background half: samples metrics, keeps a rolling window, raises alerts."""

    THRESHOLDS = {"cpu": 90.0, "memory": 90.0, "disk": 92.0}

    def __init__(self, app: "Jarvis", interval: float = 2.0, window: int = 900):
        self.app, self.interval = app, interval
        self.history: deque[dict] = deque(maxlen=window)
        self._last_alert: dict[str, float] = {}
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()

    async def _loop(self) -> None:
        while True:
            self.sample()
            await asyncio.sleep(self.interval)

    def sample(self) -> dict:
        snap = snapshot()
        point = {"ts": snap["ts"], "cpu": snap["cpu"]["percent"], "memory": snap["memory"]["percent"], "disk": snap["disk"]["percent"]}
        self.history.append(point)
        self.app.bus.emit("metrics", metrics=snap)
        for key, limit in self.THRESHOLDS.items():
            recent = [p[key] for p in list(self.history)[-5:]]
            if len(recent) == 5 and min(recent) > limit and time.time() - self._last_alert.get(key, 0) > 600:  # sustained, with cooldown
                self._last_alert[key] = time.time()
                note = self.app.db.notify("warning", f"High {key} usage", f"{key} has stayed above {limit:.0f}% (now {point[key]:.0f}%).")
                self.app.bus.emit("notification", notification=note)
        return snap

    def summary(self) -> dict:
        pts = list(self.history)
        if not pts:
            return {}
        return {"window_minutes": round((pts[-1]["ts"] - pts[0]["ts"]) / 60, 1), "samples": len(pts),
                **{f"{k}_avg": round(sum(p[k] for p in pts) / len(pts), 1) for k in ("cpu", "memory")},
                **{f"{k}_max": max(p[k] for p in pts) for k in ("cpu", "memory")}}


def daily_summary(ctx: ToolContext) -> dict:
    """Built only from recorded facts (audit log, conversations, tasks, git) - nothing is inferred."""
    db = ctx.app.db
    start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    tools = db.query("SELECT tool, COUNT(*) AS n FROM audit_log WHERE ts>=? AND outcome='ok' AND tool NOT IN ('delegate') GROUP BY tool ORDER BY n DESC LIMIT 12", (start,))
    out = {
        "date": datetime.now().strftime("%A %d %B %Y"),
        "conversations": db.one("SELECT COUNT(DISTINCT conversation_id) AS n FROM messages WHERE created>=?", (start,))["n"],
        "requests": db.one("SELECT COUNT(*) AS n FROM messages WHERE created>=? AND role='user'", (start,))["n"],
        "topics": [r["title"] for r in db.query("SELECT title FROM conversations WHERE updated>=? ORDER BY updated DESC LIMIT 8", (start,))],
        "actions_executed": tools,
        "approved_changes": [r["preview"].splitlines()[0] for r in db.query("SELECT preview FROM approvals WHERE status='executed' AND decided>=?", (start,))],
        "tasks_completed": [r["title"] for r in db.query("SELECT title FROM dev_tasks WHERE status='done' AND updated>=?", (start,))],
        "tasks_open": [r["title"] for r in db.query("SELECT title FROM dev_tasks WHERE status!='done' ORDER BY COALESCE(due, 9e18) LIMIT 10")],
        "workflow_runs": db.query("SELECT status, COUNT(*) AS n FROM workflow_runs WHERE started>=? GROUP BY status", (start,)),
        "documents_indexed": db.one("SELECT COUNT(*) AS n FROM kb_documents WHERE created>=?", (start,))["n"],
    }
    project = db.get_setting("default_project")
    if project:
        try:
            from .tools.coding import git_log

            out["commits_today"] = [c["subject"] for c in git_log(ctx, project, 30, "midnight")["commits"]]
        except Exception:
            out["commits_today"] = []
    return out


def register(reg: ToolRegistry, app: "Jarvis") -> None:
    S = {"type": "string"}
    step_schema = {"type": "object", "properties": {
        "id": S, "tool": S, "args": {"type": "object"}, "notify": S, "agent_prompt": S, "level": S,
        "when": {"type": "object", "properties": {"ref": S, "op": {"type": "string", "enum": list(OPS)}, "value": {}}, "required": ["ref"]},
        "retries": {"type": "integer", "minimum": 0, "maximum": 5}, "retry_delay": {"type": "number"}, "continue_on_error": {"type": "boolean"}}}
    sched_schema = {"type": "object", "description": "{type:'interval',seconds:N} | {type:'cron',expr:'0 9 * * 1-5'} | {type:'once',at:<unix ts>} | {type:'manual'}",
                    "properties": {"type": {"type": "string", "enum": ["interval", "cron", "once", "manual"]}, "seconds": {"type": "number"}, "expr": S, "at": {"type": "number"}}, "required": ["type"]}

    def create_workflow(ctx: ToolContext, name: str, schedule: dict, steps: list, description: str = "") -> dict:
        wf = app.automation.create(name, schedule, steps, description)
        return {"id": wf["id"], "name": name, "next_run": wf["next_run"]}

    def _preview(a: dict) -> str:
        app.automation.validate(a["schedule"], a["steps"])
        lines = [f"Create workflow '{a['name']}' - schedule {a['schedule']}"]
        for s in a["steps"]:
            tool = app.tools.get(s.get("tool", ""))
            gate = " [needs approval on each run]" if tool and tool.level is Level.CONFIRM and s["tool"] not in app.executor.trusted_tools() else ""
            lines.append(f"  {s['id']}: {s.get('tool') or ('notify' if 'notify' in s else 'agent prompt')}{gate}")
        return "\n".join(lines)

    async def run_workflow(ctx: ToolContext, id: str) -> dict:
        run = await app.automation.run(id, trigger=f"agent:{ctx.agent}")
        return {"run_id": run["id"], "status": run["status"], "log": run["log"]}

    reg.register(Tool("list_workflows", "List automation workflows with schedule and last/next run.", {}, lambda ctx: {"workflows": [{k: w[k] for k in ("id", "name", "schedule", "enabled", "next_run", "last_run")} for w in app.automation.list()]}))
    reg.register(Tool("workflow_runs", "Execution log of recent workflow runs.", {"id": S}, lambda ctx, id="": {"runs": [{k: r[k] for k in ("id", "workflow_id", "status", "trigger", "started", "finished", "log")} for r in app.automation.runs(id or None, 10)]}))
    reg.register(Tool("run_workflow", "Run a workflow now. Its steps are still individually permission-checked.", {"id": S}, run_workflow, required=["id"], timeout=600))
    reg.register(Tool("create_workflow", "Create a scheduled or manual multi-step automation. Steps call tools, send notifications or prompt the assistant; "
                      "later steps can reference earlier results as {{step_id.path}} and be conditional via `when`.",
                      {"name": S, "description": S, "schedule": sched_schema, "steps": {"type": "array", "items": step_schema}},
                      create_workflow, required=["name", "schedule", "steps"], level=Level.CONFIRM, trust_eligible=True, preview=_preview))
    reg.register(Tool("daily_summary", "Factual summary of today's activity: conversations, executed actions, approved changes, tasks, commits.", {}, daily_summary))
    reg.register(Tool("daily_briefing", "Spoken-style briefing: time, open/overdue/due-today tasks, pending approvals, next workflow, system health.", {}, lambda ctx: {"text": briefing_text(app)}))
    reg.register(Tool("metrics_history", "Rolling CPU/memory statistics collected by the background monitor.", {}, lambda ctx: app.monitor.summary()))


def briefing_text(app: "Jarvis") -> str:
    """Spoken-style status built only from recorded facts."""
    db, now = app.db, time.time()
    snap = snapshot()
    tasks = db.query("SELECT title, due FROM dev_tasks WHERE status!='done' ORDER BY COALESCE(due, 9e18)")
    overdue = [t["title"] for t in tasks if t["due"] and t["due"] < now]
    end_of_day = datetime.now().replace(hour=23, minute=59, second=59).timestamp()
    today = [t["title"] for t in tasks if t["due"] and now <= t["due"] <= end_of_day]
    parts = [f"It's {datetime.now().strftime('%A, %d %B, %H:%M')}."]
    if tasks:
        parts.append(f"You have {len(tasks)} open task{'s' if len(tasks) != 1 else ''}" + (f", {len(overdue)} overdue: {'; '.join(overdue[:3])}" if overdue else "") + ".")
        if today:
            parts.append("Due today: " + "; ".join(today[:4]) + ".")
    else:
        parts.append("You have no open tasks.")
    pending = db.one("SELECT COUNT(*) AS n FROM approvals WHERE status='pending'")["n"]
    if pending:
        parts.append(f"{pending} action{'s are' if pending != 1 else ' is'} waiting for your approval.")
    nxt = db.one("SELECT name, next_run FROM workflows WHERE enabled=1 AND next_run IS NOT NULL ORDER BY next_run LIMIT 1")
    if nxt:
        parts.append(f"Next scheduled workflow: {nxt['name']} at {datetime.fromtimestamp(nxt['next_run']).strftime('%H:%M')}.")
    health = [f"CPU {snap['cpu']['percent']:.0f}%", f"memory {snap['memory']['percent']:.0f}%", f"{snap['disk']['free_gb']:.0f} GB disk free"]
    parts.append("System: " + ", ".join(health) + ("." if snap["disk"]["percent"] < 90 else ". Disk space is running low."))
    return " ".join(parts)


class Proactive:
    """Lets JARVIS speak first: due reminders and an optional daily briefing. It only announces -
    it never executes anything on its own."""

    def __init__(self, app: "Jarvis", interval: float = 15.0):
        self.app, self.interval = app, interval
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()

    async def _loop(self) -> None:
        while True:
            try:
                self.check()
            except Exception as e:
                self.app.db.notify("error", "Proactive check failed", str(e))
            await asyncio.sleep(self.interval)

    def announce(self, title: str, text: str, kind: str) -> None:
        note = self.app.db.notify("info", title, text)
        self.app.bus.emit("notification", notification=note)
        self.app.bus.emit("assistant.announce", text=text, kind=kind, title=title)

    def check(self, now: float | None = None) -> list[str]:
        now = now or time.time()
        db, said = self.app.db, []
        for t in db.query("SELECT id, title FROM dev_tasks WHERE due IS NOT NULL AND due<=? AND status!='done' AND COALESCE(reminded,0)=0", (now,)):
            db.update("dev_tasks", t["id"], reminded=1)  # mark first: a reminder must never repeat every tick
            text = f"Reminder: {t['title']}"
            self.announce("Reminder", text, "reminder")
            said.append(text)
        cfg = db.get_setting("proactive", {})
        at = cfg.get("briefing_time") or ""
        today = datetime.fromtimestamp(now).strftime("%Y-%m-%d")
        if at and datetime.fromtimestamp(now).strftime("%H:%M") >= at and db.get_setting("last_briefing") != today:
            db.set_setting("last_briefing", today)
            text = "Good morning. " + briefing_text(self.app)
            self.announce("Daily briefing", text, "briefing")
            said.append(text)
        return said
