"""HTTP + WebSocket API. Bound to localhost; every /api and /ws request needs the per-install token
(sent as an HttpOnly SameSite=Strict cookie after /api/auth, or as a Bearer header)."""
from __future__ import annotations

import asyncio
import hmac
import json
import re
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .agents import AGENTS
from .app import Jarvis
from .automation import daily_summary
from .knowledge import search_conversations
from .security import Level, SecurityError
from .tools import coding, everyday, files, system
from .tools.registry import ToolContext

COOKIE = "jarvis_token"
# User-switchable behaviours (all default on): what each does is described in the Settings screen.
BEHAVIOR = ("fast_path", "auto_continue", "auto_memory")
FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"


class ChatIn(BaseModel):
    text: str
    conversation_id: str | None = None
    wait: bool = False
    source: str = "text"


class DecideIn(BaseModel):
    approve: bool
    scope: str = "once"  # "task": also approve this tool for the rest of this conversation's task (30 min)


class SettingsIn(BaseModel):
    trusted_tools: list[str] | None = None
    default_project: str | None = None
    voice: dict | None = None
    capabilities: dict | None = None
    behavior: dict | None = None
    proactive: dict | None = None


class IngestIn(BaseModel):
    path: str
    space: str = "general"


class SearchIn(BaseModel):
    query: str
    space: str | None = None
    limit: int = 6


class WorkflowIn(BaseModel):
    name: str
    description: str = ""
    schedule: dict
    steps: list[dict]


class DevTaskIn(BaseModel):
    title: str | None = None
    project: str = ""
    notes: str = ""
    status: str | None = None
    due_in_days: float = 0


class MemoryIn(BaseModel):
    content: str
    kind: str = "fact"


def create_app(jarvis: Jarvis | None = None) -> FastAPI:
    j = jarvis or Jarvis()
    token = j.settings.api_token()
    allowed_hosts = {"localhost", "127.0.0.1", "[::1]", "testserver", j.settings.host}
    background: set[asyncio.Task] = set()

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        await j.start()
        yield
        await j.stop()

    app = FastAPI(title="JARVIS", version="0.1.0", lifespan=lifespan)
    app.state.jarvis = j
    ctx = lambda: ToolContext(j, agent="user", actor="ui")  # noqa: E731

    def authorized(headers, cookies) -> bool:
        supplied = cookies.get(COOKIE) or headers.get("authorization", "").removeprefix("Bearer ").strip() or headers.get("x-jarvis-token", "")
        return bool(supplied) and hmac.compare_digest(supplied, token)

    @app.middleware("http")
    async def guard(request: Request, call_next):
        host = request.headers.get("host", "").rsplit(":", 1)[0] if not request.headers.get("host", "").startswith("[") else request.headers.get("host", "").split("]")[0] + "]"
        if host not in allowed_hosts:  # DNS-rebinding defence
            return JSONResponse({"detail": "Unrecognised Host header"}, status_code=421)
        path = request.url.path
        if path.startswith("/api/") and path not in ("/api/auth", "/api/health") and not authorized(request.headers, request.cookies):
            return JSONResponse({"detail": "Missing or invalid JARVIS token. Open the link printed when JARVIS started."}, status_code=401)
        try:
            return await call_next(request)
        except SecurityError as e:
            return JSONResponse({"detail": f"Blocked by security policy: {e}"}, status_code=403)
        except (KeyError, FileNotFoundError) as e:
            return JSONResponse({"detail": f"Not found: {e}"}, status_code=404)
        except (ValueError, FileExistsError, NotADirectoryError) as e:
            return JSONResponse({"detail": str(e)}, status_code=400)

    # -- auth / status -----------------------------------------------------------
    @app.get("/api/health")
    async def health():
        return {"ok": True}

    @app.get("/api/auth")
    async def auth(token: str = "", next: str = "/"):
        if not hmac.compare_digest(token, j.settings.api_token()):
            raise HTTPException(401, "Invalid token")
        resp = RedirectResponse(next if next.startswith("/") and not next.startswith("//") else "/", status_code=303)
        resp.set_cookie(COOKIE, token, httponly=True, samesite="strict", max_age=86400 * 365)
        return resp

    def status_payload() -> dict:
        return {"state": j.bus.assistant_state, "active_task": j.bus.active_task, "llm": j.provider_info(),
                "running_agents": list(j.agents.active.values()),
                "pending_approvals": j.db.one("SELECT COUNT(*) AS n FROM approvals WHERE status='pending'")["n"],
                "voice": {"stt": j.settings.stt_provider, "tts": j.settings.tts_provider, "wake_word": j.settings.wake_word, **j.db.get_setting("voice", {})},
                "model_warm": j.model_warm, "version": "0.1.0", "time": time.time()}

    @app.get("/api/status")
    async def status():
        return status_payload()

    @app.post("/api/ui/listen")
    async def ui_listen():
        """The hotkey or tray: listen for one request now (no wake word). The offline listener takes it when it runs,
        otherwise the open JARVIS window's browser recogniser does."""
        if j.voice.listen_now():
            return {"ok": True, "local": True}
        j.bus.emit("ui.listen")
        return {"ok": True, "windows": len(j.bus._subscribers)}

    @app.post("/api/voice/listen")
    async def voice_listen():
        ok = j.voice.listen_now()
        return {"ok": ok, **({} if ok else {"reason": j.voice.available() or "the offline listener is not running"})}

    @app.get("/api/overlay")
    async def overlay():
        """Everything the floating icon shows on hover, in one cheap call (it polls about twice a second)."""
        pending = j.db.query("SELECT id, tool, preview, conversation_id, created FROM approvals WHERE status='pending' ORDER BY created LIMIT 5")
        last = None
        if j.selftest is None:
            path = j.settings.data_dir / "selftest" / "last.json"
            if path.is_file():
                try:
                    last = json.loads(path.read_text(encoding="utf-8"))
                except ValueError:
                    last = None
        reply = j.db.one("SELECT content, conversation_id, created FROM messages WHERE role='assistant' ORDER BY created DESC LIMIT 1")
        request = j.db.one("SELECT content, created FROM messages WHERE role='user' ORDER BY created DESC LIMIT 1")
        # The last 5 exchanges (each request with the first reply after it), newest first, for the icon's history list.
        asks = j.db.query("SELECT content, conversation_id, created FROM messages WHERE role='user' ORDER BY created DESC LIMIT 5")
        history = []
        for a in asks:
            r = j.db.one("SELECT content FROM messages WHERE role='assistant' AND conversation_id=? AND created>=? ORDER BY created LIMIT 1",
                         (a["conversation_id"], a["created"]))
            history.append({"request": a["content"][:200], "reply": (r or {}).get("content", "")[:300], "at": a["created"]})
        return {"state": j.bus.assistant_state, "detail": j.bus.state_detail, "task": j.bus.active_task, "voice": j.voice.status(),
                "last": {"request": (request or {}).get("content", "")[:300], "reply": (reply or {}).get("content", "")[:700],
                         "reply_at": (reply or {}).get("created"), "conversation_id": (reply or {}).get("conversation_id")},
                "history": history,
                "timers": [{k: t[k] for k in ("id", "kind", "label", "due")} | {"seconds": t.get("seconds")} for t in everyday.timers(j)],
                "approvals": [{**a, "preview": (a["preview"] or "").strip()[:300]} for a in pending],
                "activity": list(j.bus.activity)[-14:], "selftest": j.selftest.summary() if j.selftest else last,
                "capabilities": j.capabilities(), "model": j.llm.current().name, "windows": len(j.bus._subscribers)}

    @app.get("/api/timers")
    async def timers_list():
        return {"timers": everyday.timers(j), "now": time.time()}

    @app.delete("/api/timers/{tid}")
    async def timers_cancel(tid: str):
        keep = [t for t in everyday.timers(j) if t["id"] != tid]
        j.db.set_setting("timers", keep)
        j.bus.emit("timers.changed", timers=keep)
        return {"ok": True, "timers": keep}

    @app.post("/api/selftest")
    async def selftest_start():
        from .selftest import SelfTest

        if j.selftest and j.selftest.state["running"]:
            raise HTTPException(409, "A self-test is already running")
        j.selftest = SelfTest(j)
        task = asyncio.create_task(j.selftest.run())
        background.add(task)
        task.add_done_callback(lambda t: (background.discard(t), t.cancelled() or t.exception()))
        return j.selftest.summary()

    @app.get("/api/selftest")
    async def selftest_status():
        return j.selftest.summary() if j.selftest else {"running": False, "steps": []}

    # -- chat ------------------------------------------------------------------------
    @app.post("/api/chat")
    async def chat(body: ChatIn):
        if body.wait:
            return await j.chat(body.text, body.conversation_id, source=body.source)
        from .db import new_id

        conversation_id = body.conversation_id or new_id("c_")
        task = asyncio.create_task(j.chat(body.text, conversation_id, source=body.source))
        background.add(task)
        task.add_done_callback(lambda t: (background.discard(t), t.cancelled() or t.exception()))
        return {"conversation_id": conversation_id, "streaming": True}

    @app.post("/api/chat/{conversation_id}/cancel")
    async def cancel(conversation_id: str):
        return {"cancelled": j.cancel(conversation_id)}

    @app.get("/api/conversations")
    async def conversations(limit: int = 50):
        return j.db.query("SELECT c.*, (SELECT COUNT(*) FROM messages m WHERE m.conversation_id=c.id) AS messages FROM conversations c ORDER BY updated DESC LIMIT ?", (limit,))

    @app.get("/api/conversations/search")
    async def conv_search(q: str):
        return search_conversations(j.db, q, 20)

    @app.get("/api/conversations/{cid}")
    async def conversation(cid: str):
        conv = j.db.one("SELECT * FROM conversations WHERE id=?", (cid,))
        if not conv:
            raise HTTPException(404, "No such conversation")
        return {**conv, "messages": j.db.query("SELECT * FROM messages WHERE conversation_id=? ORDER BY created", (cid,)),
                "tasks": j.db.query("SELECT id, agent, title, status, plan, evaluation, parent_id, created FROM tasks WHERE conversation_id=? ORDER BY created", (cid,))}

    @app.delete("/api/conversations/{cid}")
    async def delete_conversation(cid: str):
        j.db.execute("DELETE FROM messages_fts WHERE conversation_id=?", (cid,))
        j.db.execute("DELETE FROM messages WHERE conversation_id=?", (cid,))
        j.db.execute("DELETE FROM conversations WHERE id=?", (cid,))
        return {"deleted": cid}

    # -- agents / tasks ----------------------------------------------------------------
    @app.get("/api/agents")
    async def agents():
        out = []
        flat = j.root_agent() == "assistant"
        for a in AGENTS.values():
            if a.name == "assistant" and not flat:
                continue  # the single flat agent only exists when a local model is driving
            stats = j.db.one("SELECT COUNT(*) AS total, SUM(status='done') AS done, SUM(status='failed') AS failed, MAX(updated) AS last FROM tasks WHERE agent=?", (a.name,))
            active = [t for t in j.agents.active.values() if t["agent"] == a.name]
            tools = [{"name": t.name, "level": t.level.value, "trusted": t.name in j.executor.trusted_tools()} for t in (j.tools.subset(a.tools) if a.name != "assistant" else [x for x in j.tools.all() if x.name != "delegate"])]
            out.append({"name": a.name, "title": a.title, "responsibility": a.responsibility, "status": "running" if active else "idle", "active": active,
                        "tools": tools, "stats": stats, "recent_tasks": j.db.query("SELECT id, title, status, created, updated FROM tasks WHERE agent=? ORDER BY created DESC LIMIT 5", (a.name,))})
        return out

    @app.get("/api/tasks")
    async def tasks(limit: int = 40):
        return j.db.query("SELECT * FROM tasks ORDER BY created DESC LIMIT ?", (limit,))

    @app.get("/api/tasks/{tid}")
    async def task(tid: str):
        t = j.db.one("SELECT * FROM tasks WHERE id=?", (tid,))
        if not t:
            raise HTTPException(404, "No such task")
        return {**t, "messages": j.db.query("SELECT * FROM agent_messages WHERE task_id=? OR task_id IN (SELECT id FROM tasks WHERE parent_id=?) ORDER BY created", (tid, tid)),
                "subtasks": j.db.query("SELECT * FROM tasks WHERE parent_id=? ORDER BY created", (tid,))}

    @app.get("/api/agent-messages")
    async def agent_messages(limit: int = 60):
        return j.db.query("SELECT * FROM agent_messages ORDER BY created DESC LIMIT ?", (limit,))

    # -- computer ------------------------------------------------------------------------
    @app.get("/api/system/stats")
    async def sys_stats():
        return await asyncio.to_thread(system.system_stats, ctx())

    @app.get("/api/system/history")
    async def sys_history():
        return {"points": list(j.monitor.history)[-300:], "summary": j.monitor.summary()}

    @app.get("/api/system/processes")
    async def sys_processes(sort_by: str = "cpu", limit: int = 25, q: str = ""):
        return await asyncio.to_thread(system.list_processes, ctx(), sort_by, limit, q)

    @app.get("/api/system/disks")
    async def sys_disks(path: str = ""):
        return await asyncio.to_thread(system.disk_usage, ctx(), path)

    @app.get("/api/system/apps")
    async def sys_apps(q: str = ""):
        return await asyncio.to_thread(system.list_installed_apps, ctx(), q)

    @app.get("/api/system/dev-environment")
    async def sys_dev(root: str = ""):
        return await asyncio.to_thread(system.dev_environment, ctx(), root)

    @app.get("/api/files/list")
    async def files_list(path: str = "", hidden: bool = False):
        return await asyncio.to_thread(files.list_dir, ctx(), path, hidden)

    @app.get("/api/files/read")
    async def files_read(path: str):
        return await asyncio.to_thread(files.read_file, ctx(), path)

    @app.get("/api/files/search")
    async def files_search(q: str = "", root: str = "", ext: str = "", days: int = 0, text: str = ""):
        return await asyncio.to_thread(files.search_files, ctx(), root, q.split(), [e for e in ext.split(",") if e], text, days)

    @app.get("/api/files/activity")
    async def files_activity(limit: int = 20):
        names = ("write_file", "replace_in_file", "move_path", "copy_path", "delete_path", "create_directory", "organize_folder", "kb_ingest", "read_file")
        return j.db.query(f"SELECT ts, actor, tool, decision, args, outcome FROM audit_log WHERE tool IN ({','.join('?' * len(names))}) ORDER BY id DESC LIMIT ?", (*names, limit))

    # -- approvals / audit / settings ----------------------------------------------------
    @app.get("/api/approvals")
    async def approvals(status: str = "pending", limit: int = 50):
        if status == "all":
            return j.db.query("SELECT * FROM approvals ORDER BY created DESC LIMIT ?", (limit,))
        return j.db.query("SELECT * FROM approvals WHERE status=? ORDER BY created DESC LIMIT ?", (status, limit))

    @app.post("/api/approvals/{aid}/decide")
    async def decide(aid: str, body: DecideIn):
        if body.scope not in ("once", "task"):
            raise HTTPException(400, "scope must be 'once' or 'task'")
        return await j.executor.decide(aid, body.approve, body.scope)

    @app.get("/api/grants")
    async def grants():
        return [{**g, "title": (j.db.one("SELECT title FROM conversations WHERE id=?", (g["conversation_id"],)) or {}).get("title", "")} for g in j.executor.grants()]

    @app.delete("/api/grants")
    async def grants_revoke(conversation_id: str | None = None):
        j.executor.revoke_grants(conversation_id)
        j.audit.record(actor="user", tool="settings.grants", level="confirm", decision="user-action", args={"revoked": conversation_id or "all"}, outcome="ok")
        return {"ok": True}

    @app.get("/api/audit")
    async def audit(limit: int = 100, tool: str | None = None):
        return j.audit.recent(min(limit, 500), tool)

    @app.get("/api/calendar")
    async def calendar(days: int = 1, start_in_days: int = 0):
        from .tools import personal

        return await asyncio.to_thread(personal.calendar_events, ctx(), days, start_in_days)

    @app.get("/api/doctor")
    async def doctor(online: bool = False):
        from .doctor import diagnose

        return await diagnose(j, online=online)

    @app.get("/api/tools")
    async def tools():
        trusted = j.executor.trusted_tools()
        return [{"name": t.name, "description": t.description, "level": t.level.value, "trust_eligible": t.trust_eligible, "trusted": t.name in trusted,
                 "agents": [a.name for a in AGENTS.values() if t.name in a.tools]} for t in j.tools.all()]

    def settings_payload() -> dict:
        return {"trusted_tools": j.executor.trusted_tools(), "default_project": j.db.get_setting("default_project", ""),
                "voice": {"wake_word": j.settings.wake_word, "continuous": False, "voice_name": "", "rate": 1.0, "speak_replies": True, "wake_on_start": True, "language": "auto", "engine": "local", **j.db.get_setting("voice", {})},
                "voice_local": j.voice.status(),
                "capabilities": {name: {"enabled": on, "description": j.CAPABILITIES[name]} for name, on in j.capabilities().items()},
                "proactive": {"briefing_time": "", **j.db.get_setting("proactive", {})},
                "behavior": {k: bool(j.db.get_setting(k, True)) for k in BEHAVIOR},
                "agent_mode": {"configured": j.settings.agent_mode, "active": "flat" if j.root_agent() == "assistant" else "team"},
                "allowed_roots": [str(r) for r in j.settings.allowed_roots], "data_dir": str(j.settings.data_dir),
                "llm": j.provider_info(), "embedder": j.embedder.name, "search_provider": j.settings.search_provider,
                "speech": {"stt": j.settings.stt_provider, "tts": j.settings.tts_provider}, "image_provider": j.settings.image_provider,
                "levels": {"read": sum(t.level is Level.READ for t in j.tools.all()), "confirm": sum(t.level is Level.CONFIRM for t in j.tools.all()),
                           "trust_eligible": [t.name for t in j.tools.all() if t.trust_eligible]}}

    @app.get("/api/settings")
    async def get_settings():
        return settings_payload()

    @app.put("/api/settings")
    async def put_settings(body: SettingsIn):
        if body.trusted_tools is not None:
            j.executor.set_trusted_tools(body.trusted_tools)
        if body.default_project is not None:
            if body.default_project:
                j.guard.resolve(body.default_project)
            j.db.set_setting("default_project", body.default_project)
        if body.capabilities is not None:
            unknown = set(body.capabilities) - set(j.CAPABILITIES)
            if unknown:
                raise ValueError(f"Unknown capability: {', '.join(sorted(unknown))}")
            new = {**j.capabilities(), **{k: bool(v) for k, v in body.capabilities.items()}}
            j.db.set_setting("capabilities", new)
            j.audit.record(actor="user", tool="settings.capabilities", level="confirm", decision="user-action", args=new, outcome="ok")
        if body.behavior is not None:
            unknown = set(body.behavior) - set(BEHAVIOR)
            if unknown:
                raise ValueError(f"Unknown behaviour setting: {', '.join(sorted(unknown))}")
            for k, v in body.behavior.items():
                j.db.set_setting(k, bool(v))
        if body.proactive is not None:
            at = str(body.proactive.get("briefing_time", "")).strip()
            if at and not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", at):
                raise ValueError("briefing_time must be HH:MM (24-hour) or empty")
            j.db.set_setting("proactive", {"briefing_time": at})
        if body.voice is not None:
            allowed = {"wake_word", "continuous", "voice_name", "rate", "speak_replies", "pitch", "wake_on_start", "language", "engine", "keep_samples", "local_tts"}
            if body.voice.get("local_tts") not in (None, "piper", "kokoro", "windows"):
                raise ValueError("local_tts must be 'piper', 'kokoro' or 'windows'")
            if body.voice.get("engine") not in (None, "local", "browser"):
                raise ValueError("engine must be 'local' (offline listener on this computer) or 'browser' (the browser's speech service)")
            lang = body.voice.get("language")
            if lang is not None and not (lang in ("", "auto") or (isinstance(lang, str) and re.fullmatch(r"[a-z]{2,3}(-[A-Z]{2})?", lang))):
                raise ValueError("language must be 'auto' (English + Tamil, follows the user), a speech language code like 'ta-IN', or empty for the browser default")
            j.db.set_setting("voice", {**j.db.get_setting("voice", {}), **{k: v for k, v in body.voice.items() if k in allowed}})
            if "local_tts" in body.voice:
                j.voice.set_engine()
            if "keep_samples" in body.voice and j.voice.listener:
                j.voice.listener.keep_samples = bool(body.voice["keep_samples"])
            if "engine" in body.voice and j.settings.listen_on_start:  # switch listening over now, not at the next start
                if j.voice.enabled():
                    j.voice.start(asyncio.get_running_loop())
                else:
                    await asyncio.to_thread(j.voice.stop)
                j.bus.emit("voice.engine", engine=body.voice["engine"], status=j.voice.status())
        return settings_payload()

    # -- knowledge / memory ----------------------------------------------------------------
    @app.get("/api/knowledge/spaces")
    async def kb_spaces():
        return j.kb.spaces()

    @app.get("/api/knowledge/documents")
    async def kb_documents(space: str | None = None):
        return j.kb.documents(space)

    @app.post("/api/knowledge/ingest")
    async def kb_ingest(body: IngestIn):
        p = j.guard.resolve(body.path)
        res = await asyncio.to_thread(lambda: j.kb.ingest_directory(p, body.space, j.guard) if p.is_dir() else j.kb.ingest_file(p, body.space))
        j.audit.record(actor="ui/user", tool="kb_ingest", level="read", decision="user-action", args=body.model_dump(), outcome="ok")
        return res

    @app.post("/api/knowledge/upload")
    async def kb_upload(file: UploadFile = File(...), space: str = Form("general")):
        name = re.sub(r"[^\w.\- ]", "_", Path(file.filename or "upload").name)
        dest = j.settings.data_dir / "uploads" / name
        dest.parent.mkdir(exist_ok=True)
        data = await file.read()
        if len(data) > 50_000_000:
            raise HTTPException(413, "File larger than 50 MB")
        dest.write_bytes(data)
        return await asyncio.to_thread(j.kb.ingest_file, dest, space)

    @app.post("/api/knowledge/search")
    async def kb_search(body: SearchIn):
        return {"query": body.query, "results": await asyncio.to_thread(j.kb.search, body.query, body.space, body.limit)}

    @app.delete("/api/knowledge/documents/{doc_id}")
    async def kb_delete(doc_id: str):
        j.kb.delete_document(doc_id)
        return {"deleted": doc_id}

    @app.get("/api/memory")
    async def memory_list():
        return j.memory.all()

    @app.post("/api/memory")
    async def memory_add(body: MemoryIn):
        return j.memory.remember(body.content, body.kind)

    @app.delete("/api/memory/{mid}")
    async def memory_delete(mid: str):
        j.memory.forget(mid)
        return {"deleted": mid}

    # -- research -------------------------------------------------------------------------
    @app.get("/api/research/reports")
    async def reports():
        return [{"name": p.name, "size": p.stat().st_size, "modified": p.stat().st_mtime} for p in sorted(j.settings.reports_dir.glob("*.md"), reverse=True)]

    @app.get("/api/research/reports/{name}")
    async def report(name: str):
        p = (j.settings.reports_dir / Path(name).name)
        if not p.is_file():
            raise HTTPException(404, "No such report")
        return {"name": p.name, "markdown": p.read_text()}

    @app.get("/api/research/sources")
    async def sources(task_id: str | None = None, limit: int = 60):
        if task_id:
            return j.db.query("SELECT * FROM sources WHERE task_id=? ORDER BY fetched", (task_id,))
        return j.db.query("SELECT * FROM sources ORDER BY fetched DESC LIMIT ?", (limit,))

    @app.get("/api/research/tasks")
    async def research_tasks(limit: int = 20):
        return j.db.query("SELECT id, title, status, result, evaluation, created FROM tasks WHERE agent='research' ORDER BY created DESC LIMIT ?", (limit,))

    # -- images (generated by the local image model) -----------------------------------------
    @app.get("/api/images")
    async def images_list():
        return [{"name": p.name, "url": f"/api/images/{p.name}", "modified": p.stat().st_mtime} for p in sorted(j.settings.images_dir.iterdir(), reverse=True) if p.is_file()][:200]

    @app.get("/api/images/{name}")
    async def image_get(name: str):
        p = j.settings.images_dir / Path(name).name
        if not p.is_file():
            raise HTTPException(404, "No such image")
        return FileResponse(p)

    # -- coding ------------------------------------------------------------------------------
    @app.get("/api/coding/overview")
    async def coding_overview(path: str = ""):
        return await asyncio.to_thread(coding.repo_overview, ctx(), path or j.db.get_setting("default_project", ""))

    @app.get("/api/coding/diff")
    async def coding_diff(path: str = ""):
        return await asyncio.to_thread(coding.git_diff, ctx(), path or j.db.get_setting("default_project", ""))

    @app.get("/api/dev-tasks")
    async def dev_tasks():
        return coding.list_dev_tasks(ctx())

    @app.post("/api/dev-tasks")
    async def dev_task_add(body: DevTaskIn):
        if not body.title:
            raise HTTPException(400, "title is required")
        return coding.add_dev_task(ctx(), body.title, body.project, body.notes, body.due_in_days)

    @app.patch("/api/dev-tasks/{tid}")
    async def dev_task_update(tid: str, body: DevTaskIn):
        return coding.update_dev_task(ctx(), tid, body.status or "", body.notes)

    @app.delete("/api/dev-tasks/{tid}")
    async def dev_task_delete(tid: str):
        j.db.execute("DELETE FROM dev_tasks WHERE id=?", (tid,))
        return {"deleted": tid}

    # -- automation ----------------------------------------------------------------------------
    @app.get("/api/workflows")
    async def workflows():
        return j.automation.list()

    @app.post("/api/workflows")
    async def workflow_create(body: WorkflowIn):
        wf = j.automation.create(body.name, body.schedule, body.steps, body.description)
        j.audit.record(actor="ui/user", tool="create_workflow", level="confirm", decision="user-action", args=body.model_dump(), outcome="ok")
        return wf

    @app.post("/api/workflows/{wid}/run")
    async def workflow_run(wid: str):
        return await j.automation.run(wid, trigger="manual")

    @app.patch("/api/workflows/{wid}")
    async def workflow_toggle(wid: str, enabled: bool):
        j.automation.set_enabled(wid, enabled)
        return j.automation._get(wid)

    @app.delete("/api/workflows/{wid}")
    async def workflow_delete(wid: str):
        j.automation.delete(wid)
        return {"deleted": wid}

    @app.get("/api/workflow-runs")
    async def workflow_runs(workflow_id: str | None = None, limit: int = 30):
        return j.automation.runs(workflow_id, limit)

    # -- notifications / dashboard -------------------------------------------------------------
    @app.get("/api/notifications")
    async def notifications(limit: int = 30):
        return j.db.query("SELECT * FROM notifications ORDER BY created DESC LIMIT ?", (limit,))

    @app.post("/api/notifications/read")
    async def notifications_read():
        j.db.execute("UPDATE notifications SET read=1")
        return {"ok": True}

    @app.get("/api/summary/daily")
    async def summary_daily():
        return await asyncio.to_thread(daily_summary, ctx())

    @app.get("/api/dashboard")
    async def dashboard():
        project = j.db.get_setting("default_project", "")
        return {
            "status": status_payload(),
            "metrics": system.snapshot(),
            "metrics_history": list(j.monitor.history)[-120:],
            "recent_conversations": j.db.query("SELECT id, title, updated FROM conversations ORDER BY updated DESC LIMIT 6"),
            "running_agents": list(j.agents.active.values()),
            "recent_tasks": j.db.query("SELECT id, agent, title, status, updated FROM tasks WHERE parent_id IS NULL ORDER BY created DESC LIMIT 6"),
            "workflows": [{k: w[k] for k in ("id", "name", "schedule", "enabled", "next_run", "last_run")} for w in j.automation.list()[:8]],
            "pending_approvals": j.db.query("SELECT * FROM approvals WHERE status='pending' ORDER BY created DESC LIMIT 10"),
            "file_activity": await files_activity(8),
            "projects": {"default": project, "dev_tasks_open": j.db.one("SELECT COUNT(*) AS n FROM dev_tasks WHERE status!='done'")["n"]},
            "daily_summary": await asyncio.to_thread(daily_summary, ctx()),
            "notifications": j.db.query("SELECT * FROM notifications ORDER BY created DESC LIMIT 8"),
        }

    # -- voice (server-side providers; the browser handles voice by default) -------------------
    @app.get("/api/voice/config")
    async def voice_config():
        return {"stt": {"provider": j.settings.stt_provider, "server_side": j.stt is not None}, "tts": {"provider": j.settings.tts_provider, "server_side": j.tts is not None},
                "local": j.voice.status(),
                "wake_word": j.db.get_setting("voice", {}).get("wake_word", j.settings.wake_word), "settings": j.db.get_setting("voice", {})}

    @app.post("/api/voice/transcribe")
    async def transcribe(file: UploadFile = File(...), language: str | None = Form(None)):
        if j.stt is None:
            raise HTTPException(501, "No server-side speech-to-text provider is configured (set JARVIS_STT_PROVIDER=openai_compat and JARVIS_VOICE_BASE_URL). The browser's speech recognition is used instead.")
        return {"text": await j.stt.transcribe(await file.read(), file.filename or "audio.webm", language)}

    @app.post("/api/voice/speak")
    async def speak(body: dict[str, Any]):
        if j.tts is None:
            raise HTTPException(501, "No server-side text-to-speech provider is configured. The browser's speech synthesis is used instead.")
        audio, ctype = await j.tts.synthesize(str(body.get("text", ""))[:4000], body.get("voice"))
        return Response(audio, media_type=ctype)

    # -- realtime ------------------------------------------------------------------------------------
    @app.websocket("/ws")
    async def ws(sock: WebSocket):
        if sock.headers.get("host", "").rsplit(":", 1)[0] not in allowed_hosts or not (authorized(sock.headers, sock.cookies) or hmac.compare_digest(sock.query_params.get("token", ""), token)):
            await sock.close(code=4401)
            return
        await sock.accept()
        queue = j.bus.subscribe()
        await sock.send_json({"type": "hello", "status": status_payload()})

        async def pump():
            while True:
                await sock.send_json(await queue.get())

        sender = asyncio.create_task(pump())
        try:
            while True:
                msg = await sock.receive_json()
                if msg.get("type") == "state" and msg.get("state") in ("listening", "speaking", "idle"):
                    j.bus.set_state(msg["state"])  # the voice UI reports mic/speaker activity
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            sender.cancel()
            j.bus.unsubscribe(queue)

    # -- frontend -------------------------------------------------------------------------------------
    if FRONTEND_DIST.is_dir():
        app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

        @app.get("/{path:path}")
        async def spa(path: str):
            target = (FRONTEND_DIST / path).resolve()
            if path and FRONTEND_DIST in target.parents and target.is_file():
                return FileResponse(target)
            return FileResponse(FRONTEND_DIST / "index.html")

    return app
