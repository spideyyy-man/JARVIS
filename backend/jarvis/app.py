"""The Jarvis application object: wires services together and owns the conversation loop."""
from __future__ import annotations

import asyncio
import re
import time
from pathlib import Path

from . import agents as agents_mod
from . import automation as automation_mod
from .agents import AgentRuntime
from .automation import AutomationEngine, Monitor, Proactive
from .config import Settings, get_secret, load_settings
from .db import Database, new_id
from .events import EventBus
from .knowledge import KnowledgeBase, Memory, make_embedder
from .llm.base import FORCE_PROVIDER, LLMProvider, LLMRouter
from .llm.offline import Env, OfflineProvider, match_rules
from .security import AuditLog, PathGuard
from .tools import browser, coding, desktop, files, images, personal, research, system
from .tools.registry import ToolExecutor, ToolRegistry, ToolResult
from .voice import build_voice

AFFIRM_ALL = re.compile(r"^\s*(hey\s+)?(jarvis[,\s]+)?(approve all|yes to all|approve (them|these|everything)( all)?|approve for (this|the) (whole )?task|always allow( this)?( for (this|the) task)?)[\s.!]*$", re.I)
MAX_CONTINUATIONS = 20
AFFIRM = re.compile(r"^\s*(hey\s+)?(jarvis[,\s]+)?(yes|yep|approve[d]?|go ahead|do it|confirm(ed)?|proceed|yes,? (do it|please|go ahead))[\s.!]*$", re.I)
DENY = re.compile(r"^\s*(hey\s+)?(jarvis[,\s]+)?(no|nope|deny|denied|cancel( it| that)?|don'?t|stop|abort)[\s.!]*$", re.I)
HISTORY_CHAR_BUDGET = 16_000


class Jarvis:
    def __init__(self, settings: Settings | None = None, providers: list[LLMProvider] | None = None):
        self.settings = settings or load_settings()
        self.db = Database(self.settings.db_path)
        self.bus = EventBus()
        self.audit = AuditLog(self.db)
        self.guard = PathGuard(self.settings.allowed_roots, self.settings.data_dir)
        self.embedder = make_embedder(self.settings)
        self.kb = KnowledgeBase(self.db, self.embedder)
        self.memory = Memory(self.db, self.embedder)
        self.http_transport = None  # injectable for tests
        self.mail_factory = None  # injectable for tests: (kind, config) -> IMAP/SMTP connection
        self.capture_screen = desktop.capture_screen  # injectable for tests
        self.browser = browser.BrowserSession(self)
        self.proactive = Proactive(self)
        self.monitor = Monitor(self)
        self.automation = AutomationEngine(self)
        self.agents = AgentRuntime(self)
        self.tools = ToolRegistry()
        self.executor = ToolExecutor(self)
        system.register(self.tools)
        files.register(self.tools, self)
        coding.register(self.tools, self)
        research.register(self.tools)
        images.register(self.tools)
        desktop.register(self.tools, self)
        personal.register(self.tools, self)
        browser.register(self.tools, self)
        automation_mod.register(self.tools, self)
        agents_mod.register(self.tools, self)
        self.offline = OfflineProvider(Env(home=self.settings.allowed_roots[0], default_project=lambda: self.db.get_setting("default_project", ""),
                                           workflows=self.automation.list))
        self.llm = LLMRouter(providers if providers is not None else self._build_providers(), on_event=self._llm_event, timeout=self.settings.llm_timeout)
        self.stt, self.tts = build_voice(self.settings)
        self._chat_tasks: dict[str, asyncio.Task] = {}
        self._background: set[asyncio.Task] = set()
        self._continuations: dict[str, int] = {}
        for name in ("delete_path", "run_command", "git_commit"):  # always ask, every time
            self.tools.get(name).grantable = False
        for name in ("fetch_url", "web_search", "github_repo_info", "browser_open", "browser_read", "browser_click", "browser_fill", "screen_read", "clipboard_read"):
            self.tools.get(name).untrusted_output = True

    # -- providers ---------------------------------------------------------------
    def _build_providers(self) -> list[LLMProvider]:
        from .llm.providers import AnthropicProvider, OpenAICompatProvider

        s, chain = self.settings, []
        want = s.llm_provider
        has_anthropic = bool(get_secret("ANTHROPIC_API_KEY") or get_secret("ANTHROPIC_AUTH_TOKEN"))
        if want == "anthropic" or (want == "auto" and has_anthropic):
            chain.append(AnthropicProvider(s.anthropic_model, s.anthropic_effort, get_secret("ANTHROPIC_API_KEY")))
        if s.openai_base_url and s.openai_model and want in ("auto", "openai_compat", "anthropic"):
            compat = OpenAICompatProvider(s.openai_base_url, s.openai_model, get_secret("OPENAI_API_KEY"), stream=s.openai_stream)
            chain.insert(0, compat) if want == "openai_compat" else chain.append(compat)
        chain.append(self.offline)  # always last: graceful degradation
        return chain

    def _llm_event(self, type_: str, **data) -> None:
        self.bus.emit(type_, **data)
        if not data.get("retrying"):
            note = self.db.notify("warning", f"Language model '{data['provider']}' failed", f"{data['error']}" + (f" Falling back to '{data['next']}'." if data.get("next") else ""))
            self.bus.emit("notification", notification=note)

    CAPABILITIES = {"screen_access": "Read the screen (JARVIS sees whatever is visible, including anything sensitive on screen)",
                    "desktop_control": "Move the mouse and type (each action still asks for approval)",
                    "browser_control": "Drive JARVIS's own browser window (clicks and form input still ask for approval)"}

    def capabilities(self) -> dict:
        """High-impact abilities, all off by default. Only the user (Settings / API) can change them - no tool can."""
        stored = self.db.get_setting("capabilities", {})
        return {name: bool(stored.get(name, False)) for name in self.CAPABILITIES}

    def provider_info(self) -> dict:
        return {"chain": [{"name": p.name, "model": p.model} for p in self.llm.providers], "last_used": self.llm.last_used,
                "degraded": self.llm.primary.name == "offline", "recent_failures": self.llm.failures[-5:]}

    # -- lifecycle -----------------------------------------------------------------
    async def start(self) -> None:
        self.monitor.start()
        self.proactive.start()
        self.automation.start()

    async def stop(self) -> None:
        await self.monitor.stop()
        await self.proactive.stop()
        await self.browser.close()
        await self.automation.stop()
        self.db.close()

    # -- conversations -------------------------------------------------------------
    def _add_message(self, conversation_id: str, role: str, content: str, meta: dict | None = None) -> dict:
        msg = {"id": new_id("msg_"), "conversation_id": conversation_id, "role": role, "content": content, "meta": meta or {}, "created": time.time()}
        self.db.insert("messages", msg)
        self.db.execute("INSERT INTO messages_fts(content, message_id, conversation_id) VALUES (?,?,?)", (content, msg["id"], conversation_id))
        self.db.update("conversations", conversation_id, updated=msg["created"])
        return msg

    def _history(self, conversation_id: str, exclude_id: str) -> tuple[list[dict], str]:
        """Short-term memory: the most recent turns verbatim within a budget; older turns as a summary."""
        rows = [r for r in self.db.query("SELECT * FROM messages WHERE conversation_id=? ORDER BY created", (conversation_id,)) if r["id"] != exclude_id]
        kept, used = [], 0
        for r in reversed(rows):
            used += len(r["content"])
            if used > HISTORY_CHAR_BUDGET and kept:
                break
            kept.append(r)
        kept.reverse()
        older = rows[: len(rows) - len(kept)]
        summary = ""
        if older:
            asks = [r["content"].strip().splitlines()[0][:140] for r in older if r["role"] == "user"]
            summary = "Earlier in this conversation the user asked about: " + " | ".join(asks[-12:])
        history: list[dict] = []
        for r in kept:
            role = "assistant" if r["role"] == "assistant" else "user"
            content = r["content"] if r["role"] != "event" else f"[system event] {r['content']}"
            if history and history[-1]["role"] == role:
                history[-1]["content"] += "\n\n" + content
            else:
                history.append({"role": role, "content": content})
        while history and history[0]["role"] != "user":
            history.pop(0)
        return history, summary

    async def chat(self, text: str, conversation_id: str | None = None, actor: str = "chat", source: str = "text") -> dict:
        text = text.strip()
        if not text:
            raise ValueError("Empty message")
        now = time.time()
        if conversation_id is None or not self.db.one("SELECT id FROM conversations WHERE id=?", (conversation_id,)):
            conversation_id = conversation_id or new_id("c_")
            self.db.insert("conversations", {"id": conversation_id, "title": text[:70], "summary": "", "created": now, "updated": now})
        if actor == "continuation":  # an internal nudge, not something the user said: keep it out of the transcript
            user_msg = {"id": "", "conversation_id": conversation_id, "role": "event", "content": "", "meta": {}, "created": now}
            self.bus.emit("chat.resume", conversation_id=conversation_id)
        else:
            user_msg = self._add_message(conversation_id, "user", text, {"source": source, "actor": actor})
            self.bus.emit("chat.start", conversation_id=conversation_id, message=user_msg)

        # Spoken/typed approval. Deliberately handled here, on the user's own words, and NOT exposed
        # as a tool - a model must never be able to approve its own pending action.
        if actor == "chat":
            self._continuations.pop(conversation_id, None)  # a real message from the user resets the auto-continue budget
        if actor == "chat" and AFFIRM_ALL.match(text):
            pending = self.db.query("SELECT * FROM approvals WHERE status='pending' AND conversation_id=? ORDER BY created", (conversation_id,))
            if pending:
                done, refused = [], []
                for ap in pending:
                    try:
                        done.append((await self.executor.decide(ap["id"], True, scope="task"))["tool"])
                    except Exception:
                        refused.append(ap["tool"])
                reply = (f"Approved {len(done)} action(s). I won't ask again for {', '.join(sorted(set(done)))} during this task (30 minutes)." if done else "") + \
                        (f" {', '.join(sorted(set(refused)))} must be approved one at a time - still waiting on that." if refused else "")
                return self._finish(conversation_id, reply.strip(), {"provider": "policy"})
        if actor == "chat" and (AFFIRM.match(text) or DENY.match(text)):
            pending = self.db.query("SELECT * FROM approvals WHERE status='pending' AND conversation_id=? ORDER BY created", (conversation_id,))
            if len(pending) == 1:
                ap = await self.executor.decide(pending[0]["id"], bool(AFFIRM.match(text)))
                reply = {"executed": "Approved and done.", "failed": f"Approved, but it failed: {ap['result']}", "denied": "Understood - cancelled. Nothing was changed."}[ap["status"]]
                return self._finish(conversation_id, reply, {"approval": ap["id"], "provider": "policy"})
            if len(pending) > 1:
                return self._finish(conversation_id, f"There are {len(pending)} actions waiting. Please approve or deny each one in the Command Center so I act on the right one.", {"provider": "policy"})

        history, summary = self._history(conversation_id, user_msg["id"])
        notes = []
        if summary:
            notes.append(summary)
        memories = self.memory.recall(text, k=4)
        if memories:
            notes.append("Long-term memory relevant to this request:\n" + "\n".join(f"- {m['content']}" for m in memories))
        project = self.db.get_setting("default_project", "")
        if project:
            notes.append(f"The user's current project directory is {project}.")

        streamed: list[str] = []

        def on_text(piece: str) -> None:
            streamed.append(piece)
            self.bus.emit("chat.delta", conversation_id=conversation_id, text=piece)

        self.bus.set_state("thinking")
        self.bus.active_task = {"conversation_id": conversation_id, "title": text[:120], "started": now}
        self._chat_tasks[conversation_id] = asyncio.current_task()  # type: ignore[assignment]
        # Fast path: a slow local model should not be made to think about "turn the volume up".
        pin = FORCE_PROVIDER.set("offline") if actor == "chat" and self.fast_path_applies(text) else None
        try:
            result = await self.agents.run(self.root_agent(pinned_offline=pin is not None), text, conversation_id=conversation_id, history=history,
                                           context_note="\n\n".join(notes), actor=actor, on_text=on_text)
        except asyncio.CancelledError:
            partial = "".join(streamed).strip()
            self._finish(conversation_id, (partial + "\n\n" if partial else "") + "_(interrupted)_", {"interrupted": True}, state="idle")
            raise
        finally:
            if pin is not None:
                FORCE_PROVIDER.reset(pin)
            self._chat_tasks.pop(conversation_id, None)
            self.bus.active_task = None
        meta = {"task_id": result.task_id, "provider": result.provider, "tools": result.tools_used, "pending_approvals": result.pending_approvals,
                "errors": result.errors, "agents": sorted({t["agent"] for t in result.tools_used} | {"orchestrator"})}
        failed = bool(result.errors) and not result.tools_used
        if actor == "chat":
            learned = self.auto_memory(text)
            if learned:
                meta["learned"] = learned
        return self._finish(conversation_id, result.text, meta, state="error" if failed else "idle")

    def _finish(self, conversation_id: str, text: str, meta: dict, state: str = "idle") -> dict:
        msg = self._add_message(conversation_id, "assistant", text, meta)
        self.bus.emit("chat.done", conversation_id=conversation_id, message=msg)
        self.bus.set_state(state)
        return {"conversation_id": conversation_id, "message_id": msg["id"], "text": text, **meta}

    _FACT_PATTERNS = [re.compile(p, re.I) for p in (
        r"\bmy name is\s+[A-Z][\w\-]+", r"\bcall me\s+\w+", r"\bI(?:'m| am) (?:a|an)\s+\w+", r"\bI (?:work|am working) (?:at|on|for|as|in)\s+\w+",
        r"\bI (?:really |usually |always |never |mostly )?(?:prefer|like|love|hate|dislike|use|avoid|want you to)\s+\w+", r"\bI (?:always|never|usually)\s+\w+",
        r"\bmy (?:\w+ ){0,2}(?:is|are)\s+\w+", r"\bI live in\s+\w+", r"\bdon'?t (?:ever )?(?:call|use|send|show)\b")]

    def auto_memory(self, text: str) -> list[str]:
        """Learn durable facts the user states in passing ("I prefer dark mode", "my manager is Priya").
        Pattern-based and conservative: statements only, never questions or commands, and no duplicates."""
        if not self.db.get_setting("auto_memory", True):
            return []
        saved = []
        for sentence in re.split(r"(?<=[.!?])\s+|\n+", text):
            s = sentence.strip()
            if not (12 <= len(s) <= 220) or s.endswith("?") or re.match(r"^(hey\s+)?(jarvis[,\s]+)?(remember|remind|find|open|search|show|what|how|why|can|could|please|create|make|delete|run)\b", s, re.I):
                continue
            if not any(p.search(s) for p in self._FACT_PATTERNS) or re.search(r"\bmy (computer|screen|clipboard|downloads?|documents?|desktop|project|repo|code|files?|folder|inbox|email|calendar)\b", s, re.I):
                continue
            s = re.sub(r"^(by the way|btw|also|oh|and|fyi|just so you know)[,:\s]+", "", s, flags=re.I)
            existing = self.memory.recall(s, k=1, min_sim=0.6)
            if existing:
                continue
            self.memory.remember(f"The user said: \"{s.rstrip('.!')}\"", "auto")
            saved.append(s)
        return saved

    # Requests the rule engine recognises but a model answers better (they need synthesis, not a lookup).
    NOT_FAST = {"search_files", "kb_search", "web_search", "fetch_url", "repo_overview", "conversation_search", "create_plan", "recall", "kb_ingest"}

    def root_agent(self, pinned_offline: bool = False) -> str:
        """team: orchestrator + specialists (least privilege, 4+ model calls per action).
        flat: one agent with a shortlist drawn from every tool (2 model calls per action). 'auto' picks flat for
        local OpenAI-compatible models, where each call is slow, and team otherwise. The rule engine always uses team."""
        if pinned_offline or self.llm.current().name == "offline":
            return "orchestrator"
        mode = self.settings.agent_mode
        if mode == "auto":
            mode = "flat" if self.llm.current().name == "openai_compat" else "team"
        return "assistant" if mode == "flat" else "orchestrator"

    def fast_path_applies(self, text: str) -> bool:
        """True when a model is configured but this request is a simple command the rule engine can do instantly."""
        if self.llm.primary.name == "offline" or not self.db.get_setting("fast_path", True) or len(text.split()) > 14:
            return False
        hit = match_rules(text, self.offline.env)
        return bool(hit) and hit[1][0] not in self.NOT_FAST

    def cancel(self, conversation_id: str) -> bool:
        """Barge-in: the user interrupted. Stops the turn at its next await point."""
        task = self._chat_tasks.get(conversation_id)
        if task and not task.done():
            task.cancel()
            return True
        return False

    async def on_approval_decided(self, approval: dict, result: ToolResult) -> None:
        if approval.get("conversation_id") and self.db.one("SELECT id FROM conversations WHERE id=?", (approval["conversation_id"],)):
            verb = {"executed": "was approved and executed", "failed": "was approved but failed", "denied": "was denied by the user and not executed"}.get(approval["status"], approval["status"])
            msg = self._add_message(approval["conversation_id"], "event", f"Action `{approval['tool']}` {verb}. {approval['preview'].splitlines()[0]} Result: {result.to_model(600)}",
                                    {"approval": approval["id"], "status": approval["status"]})
            self.bus.emit("chat.event", conversation_id=approval["conversation_id"], message=msg)
        await self.automation.on_approval(approval, result)
        conv = approval.get("conversation_id")
        if conv and self.db.get_setting("auto_continue", True):
            task = asyncio.create_task(self._continue_after_approval(conv))
            self._background.add(task)
            task.add_done_callback(self._background.discard)

    async def _continue_after_approval(self, conversation_id: str) -> None:
        """Pick the task back up once its approvals are decided, so a multi-step job does not die at the
        first confirmation. Only for real models - the rule engine's requests are single-step."""
        await asyncio.sleep(0.05)
        running = self._chat_tasks.get(conversation_id)
        if running and not running.done():
            await asyncio.wait([running])
        if self.db.one("SELECT id FROM approvals WHERE status='pending' AND conversation_id=?", (conversation_id,)):
            return  # wait until every outstanding request is decided, then continue once
        last = self.db.one("SELECT meta FROM messages WHERE conversation_id=? AND role='assistant' AND json_extract(meta,'$.task_id') IS NOT NULL ORDER BY created DESC LIMIT 1", (conversation_id,))
        if not last or last["meta"].get("provider") in (None, "offline", "policy"):
            return
        n = self._continuations.get(conversation_id, 0)
        if n >= MAX_CONTINUATIONS:
            self._finish(conversation_id, "I've paused here: this task has needed a lot of approvals in a row. Tell me to continue if you want me to keep going.", {"provider": "policy"})
            return
        self._continuations[conversation_id] = n + 1
        try:
            await self.chat("The approval decision(s) above are in. Continue the original task from where you stopped: use the results, do not repeat "
                            "actions that already ran, do not retry anything that was denied, and give the final answer when the task is complete.",
                            conversation_id, actor="continuation", source="event")
        except Exception as e:
            self.db.notify("error", "Could not continue the task", str(e))

    async def idle(self) -> None:
        """Wait for background continuations to finish (used by tests and graceful shutdown)."""
        while self._background:
            await asyncio.gather(*list(self._background), return_exceptions=True)
