"""Tool registry and the single choke point (`ToolExecutor.call`) through which every
tool invocation passes: schema validation -> permission decision -> execution -> redaction -> audit."""
from __future__ import annotations

import asyncio
import inspect
import json
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable

import jsonschema

from ..db import new_id
from ..security import Level, SecurityError, outbound_leak, redact_obj

if TYPE_CHECKING:
    from ..app import Jarvis


@dataclass
class ToolContext:
    app: "Jarvis"
    agent: str = "user"
    actor: str = "chat"  # chat | workflow:<id> | api
    task_id: str | None = None
    conversation_id: str | None = None
    origin: dict = field(default_factory=dict)  # lets a paused workflow resume after approval


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict  # JSON schema "properties"
    handler: Callable[..., Any]
    required: list[str] = field(default_factory=list)
    level: Level = Level.READ
    trust_eligible: bool = False  # may the user allowlist this for automatic execution?
    preview: Callable[[dict], str] | None = None  # human-readable description of the pending action
    grantable: bool = True  # may the user approve this tool "for the rest of this task"?
    untrusted_output: bool = False  # returns content written by third parties (web pages, the screen, email)
    timeout: float = 60.0
    # A READ tool whose arguments leave this computer (a search query, a URL). If they carry the user's saved private
    # details or a credential, the call waits for approval instead of running (security.outbound_leak).
    outbound: bool = False
    # Writes long-term memory, which is added to every later request. After third-party content has been read in the
    # conversation it waits for approval: a page could plant a "fact" ("the user wants files sent to ...").
    writes_memory: bool = False

    @property
    def schema(self) -> dict:
        return {"type": "object", "properties": self.parameters, "required": self.required, "additionalProperties": False}

    def spec(self) -> dict:
        return {"name": self.name, "description": self.description, "input_schema": self.schema}


@dataclass
class ToolResult:
    ok: bool
    data: Any = None
    error: str | None = None
    approval_id: str | None = None
    preview: str | None = None

    @property
    def pending(self) -> bool:
        return self.approval_id is not None

    def to_model(self, limit: int = 12000) -> str:
        if self.pending:
            payload: Any = {"status": "pending_approval", "approval_id": self.approval_id, "preview": self.preview,
                            "note": "This action modifies the system and is waiting for the user's explicit approval. "
                                    "It has NOT been executed. Do not retry it; tell the user it awaits approval."}
        elif self.ok:
            payload = self.data
        else:
            payload = {"error": self.error}
        text = payload if isinstance(payload, str) else json.dumps(payload, default=str)
        return text if len(text) <= limit else text[:limit] + f"... [truncated {len(text) - limit} chars]"


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"duplicate tool {tool.name}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def all(self) -> list[Tool]:
        return list(self._tools.values())

    def subset(self, names: list[str]) -> list[Tool]:
        return [self._tools[n] for n in names if n in self._tools]


class ToolExecutor:
    GRANT_SECONDS = 30 * 60
    UNTRUSTED_WINDOW = 10 * 60

    def __init__(self, app: "Jarvis"):
        self.app = app
        self._untrusted: dict[str, list[tuple[float, str]]] = {}  # conversation -> recent untrusted reads
        app.db.execute("CREATE TABLE IF NOT EXISTS grants (id TEXT PRIMARY KEY, conversation_id TEXT, tool TEXT, expires REAL, created REAL)")

    # -- task-scoped grants ("approve this tool for the rest of this task") --------------
    def grants(self) -> list[dict]:
        self.app.db.execute("DELETE FROM grants WHERE expires<?", (time.time(),))
        return self.app.db.query("SELECT * FROM grants ORDER BY created DESC")

    def has_grant(self, conversation_id: str | None, tool: str) -> bool:
        return bool(conversation_id) and self.app.db.one("SELECT id FROM grants WHERE conversation_id=? AND tool=? AND expires>?", (conversation_id, tool, time.time())) is not None

    def revoke_grants(self, conversation_id: str | None = None) -> None:
        if conversation_id:
            self.app.db.execute("DELETE FROM grants WHERE conversation_id=?", (conversation_id,))
        else:
            self.app.db.execute("DELETE FROM grants")

    def recent_untrusted(self, conversation_id: str | None) -> list[str]:
        cutoff = time.time() - self.UNTRUSTED_WINDOW
        return sorted({tool for ts, tool in self._untrusted.get(conversation_id or "", []) if ts > cutoff})

    # -- trusted allowlist (Level 3) ------------------------------------------
    def trusted_tools(self) -> list[str]:
        return self.app.db.get_setting("trusted_tools", [])

    def set_trusted_tools(self, names: list[str]) -> list[str]:
        """Only tools the code marks trust_eligible can be allowlisted - enforced here, not in the UI."""
        for name in names:
            tool = self.app.tools.get(name)
            if tool is None or tool.level is not Level.CONFIRM or not tool.trust_eligible:
                raise SecurityError(f"'{name}' cannot be trusted for automatic execution")
        self.app.db.set_setting("trusted_tools", sorted(set(names)))
        self.app.audit.record(actor="user", tool="settings.trusted_tools", level="confirm", decision="approved",
                              args={"trusted": names}, outcome="ok")
        return self.trusted_tools()

    # -- main entry point ---------------------------------------------------------
    async def call(self, name: str, args: dict, ctx: ToolContext) -> ToolResult:
        tool = self.app.tools.get(name)
        if tool is None:
            return ToolResult(False, error=f"Unknown tool '{name}'")
        try:
            jsonschema.validate(args, tool.schema)
        except jsonschema.ValidationError as e:
            return ToolResult(False, error=f"Invalid arguments for {name}: {e.message}")

        actor = f"{ctx.actor}/{ctx.agent}"
        if tool.level is Level.READ:
            reason = self._needs_review(tool, args, ctx)
            if reason is None:
                return await self._run(tool, args, ctx, actor, decision="auto-read")
            return self._queue(tool, args, ctx, actor, reason=reason)
        if tool.trust_eligible and name in self.trusted_tools():
            return await self._run(tool, args, ctx, actor, decision="trusted-allowlist")
        if tool.grantable and self.has_grant(ctx.conversation_id, name):
            try:  # the preview doubles as the viability/security check, so it still runs
                tool.preview(args) if tool.preview else None
            except Exception as e:
                return ToolResult(False, error=f"Blocked by security policy: {e}" if isinstance(e, SecurityError) else str(e))
            return await self._run(tool, args, ctx, actor, decision="task-grant")

        # Level 2: prepare, queue, and wait for a human.
        return self._queue(tool, args, ctx, actor)

    def _needs_review(self, tool: Tool, args: dict, ctx: ToolContext) -> str | None:
        """Why this READ call must wait for the user instead of running, or None (the usual case)."""
        untrusted = self.recent_untrusted(ctx.conversation_id)
        if tool.outbound:
            leak = outbound_leak(args, self.app.db.get_setting("profile", {}), bool(untrusted))
            if leak:
                return f"Check before it goes out: {leak}."
        if tool.writes_memory and untrusted and not self._users_own_words(args, ctx.conversation_id):
            return (f"Check before I remember this: it comes after reading {', '.join(untrusted)} in this conversation, "
                    "and text there can plant false memories.")
        return None

    def _users_own_words(self, args: dict, conversation_id: str | None) -> bool:
        """True when what is being saved is what the user just said themselves ("remember that I like tea")."""
        if not conversation_id:
            return False
        row = self.app.db.one("SELECT content FROM messages WHERE conversation_id=? AND role='user' ORDER BY created DESC LIMIT 1", (conversation_id,))
        said = " ".join((row or {}).get("content", "").lower().split())
        saved = " ".join(" ".join(str(v) for v in args.values() if isinstance(v, str)).lower().split())
        return len(saved) >= 3 and saved in said

    def _queue(self, tool: Tool, args: dict, ctx: ToolContext, actor: str, reason: str = "") -> ToolResult:
        name = tool.name
        try:
            preview = tool.preview(args) if tool.preview else f"{name}({json.dumps(args, default=str)})"
        except Exception as e:  # preview validates paths etc.; failure means the action is not viable
            self.app.audit.record(actor=actor, tool=name, level=tool.level.value, decision="rejected-invalid", args=args, outcome="error", detail=str(e))
            return ToolResult(False, error=f"Blocked by security policy: {e}" if isinstance(e, SecurityError) else str(e))
        if reason:
            preview = f"{reason}\n{preview}"
        approval = {"id": new_id("ap_"), "tool": name, "args": args, "preview": preview, "agent": ctx.agent,
                    "task_id": ctx.task_id, "conversation_id": ctx.conversation_id,
                    # shown to the user: this request came after reading third-party content, which may contain planted instructions
                    "origin": {**ctx.origin, **({"untrusted": u} if (u := self.recent_untrusted(ctx.conversation_id)) else {}),
                               # a READ tool held back for a reason is approved one call at a time, never for the whole task
                               "grantable": tool.grantable and bool(ctx.conversation_id) and not reason,
                               **({"held_back": reason} if reason else {})},
                    "status": "pending", "result": "", "created": time.time(), "decided": None}
        self.app.db.insert("approvals", approval)
        self.app.audit.record(actor=actor, tool=name, level="confirm" if reason else tool.level.value,
                              decision="held-for-review" if reason else "queued-for-approval", args=args, outcome="pending", detail=preview)
        self.app.bus.emit("approval.requested", approval=approval)
        self.app.db.notify("action", f"Approval needed: {name}", preview)
        return ToolResult(True, approval_id=approval["id"], preview=preview)

    async def decide(self, approval_id: str, approve: bool, scope: str = "once", by: str = "user") -> dict:
        """`by` is who decided, for the audit log: "user" (screen, chat) or "selftest" (the self-test the user started)."""
        ap = self.app.db.one("SELECT * FROM approvals WHERE id=?", (approval_id,))
        if ap is None:
            raise KeyError(approval_id)
        if ap["status"] != "pending":
            raise SecurityError(f"Approval already {ap['status']}")
        # Claim the row first so a double-click cannot execute the action twice.
        claimed = self.app.db.execute("UPDATE approvals SET status=?, decided=? WHERE id=? AND status='pending'",
                                      ("approved" if approve else "denied", time.time(), approval_id)).rowcount
        if not claimed:
            raise SecurityError("Approval already decided")
        tool = self.app.tools.get(ap["tool"])
        if approve and scope == "task" and tool is not None:
            if not tool.grantable or not ap["conversation_id"] or (ap["origin"] or {}).get("held_back"):
                self.app.db.update("approvals", approval_id, status="pending", decided=None)
                raise SecurityError(f"'{ap['tool']}' must be approved every time; it cannot be approved for a whole task")
            self.app.db.insert("grants", {"id": new_id("g_"), "conversation_id": ap["conversation_id"], "tool": ap["tool"], "expires": time.time() + self.GRANT_SECONDS, "created": time.time()})
            self.app.audit.record(actor=by, tool=ap["tool"], level="confirm", decision="task-grant-created", args={"conversation": ap["conversation_id"], "minutes": self.GRANT_SECONDS // 60}, outcome="ok")
        ctx = ToolContext(self.app, agent=ap["agent"], actor="approved-by-user", task_id=ap["task_id"],
                          conversation_id=ap["conversation_id"], origin=ap["origin"] or {})
        if not approve or tool is None:
            self.app.audit.record(actor=by, tool=ap["tool"], level="confirm", decision="denied", args=ap["args"], outcome="not-executed")
            result = ToolResult(False, error="Denied by user")
        else:
            result = await self._run(tool, ap["args"], ctx, by, decision="approved")
        self.app.db.update("approvals", approval_id, status=("executed" if result.ok else "failed") if approve else "denied",
                           result=result.to_model(4000))
        ap = self.app.db.one("SELECT * FROM approvals WHERE id=?", (approval_id,))
        self.app.bus.emit("approval.decided", approval=ap)
        await self.app.on_approval_decided(ap, result)
        return ap

    async def _run(self, tool: Tool, args: dict, ctx: ToolContext, actor: str, *, decision: str) -> ToolResult:
        try:
            if inspect.iscoroutinefunction(tool.handler):
                data = await asyncio.wait_for(tool.handler(ctx, **args), tool.timeout)
            else:
                data = await asyncio.wait_for(asyncio.to_thread(tool.handler, ctx, **args), tool.timeout)
            result = ToolResult(True, data=redact_obj(data))
            outcome, detail = "ok", ""
            if tool.untrusted_output and ctx.conversation_id:
                self._untrusted.setdefault(ctx.conversation_id, []).append((time.time(), tool.name))
        except asyncio.TimeoutError:
            result, outcome, detail = ToolResult(False, error=f"{tool.name} timed out after {tool.timeout:.0f}s"), "timeout", ""
        except SecurityError as e:
            result, outcome, detail = ToolResult(False, error=f"Blocked by security policy: {e}"), "blocked", str(e)
        except Exception as e:
            result, outcome, detail = ToolResult(False, error=f"{type(e).__name__}: {e}"), "error", str(e)
        self.app.audit.record(actor=actor, tool=tool.name, level=tool.level.value, decision=decision, args=args, outcome=outcome, detail=detail)
        self.app.bus.emit("tool.executed", tool=tool.name, agent=ctx.agent, ok=result.ok, level=tool.level.value, decision=decision)
        return result
