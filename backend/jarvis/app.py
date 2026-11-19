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
from .listen import VoiceService
from .knowledge import KnowledgeBase, Memory, make_embedder
from .llm.base import FORCE_PROVIDER, LLMProvider, LLMRouter
from .llm.offline import Env, OfflineProvider, fast_talk, match_rules, with_context
from .security import AuditLog, PathGuard
from .tools import browser, coding, desktop, files, images, personal, research, system
from .tools.registry import ToolExecutor, ToolRegistry, ToolResult
from .translate import NLLB, Translator, language_of
from .voice import build_voice

AFFIRM_ALL = re.compile(r"^\s*(hey\s+)?(jarvis[,\s]+)?(approve all|yes to all|approve (them|these|everything)( all)?|approve for (this|the) (whole )?task|always allow( this)?( for (this|the) task)?)[\s.!]*$", re.I)
MAX_CONTINUATIONS = 20
AFFIRM = re.compile(r"^\s*(hey\s+)?((jarvis|ஜார்விஸ்)[,\s]+)?(yes|yep|approve[d]?|go ahead|do it|confirm(ed)?|proceed|yes,? (do it|please|go ahead)"
                    r"|சரி|ஆமா|ஆமாம்|சரி,?\s*(செய்|பண்ணு)\S*|செய்|பண்ணு|அனுமதிக்கிறேன்)[\s.!]*$", re.I)  # Tamil: OK / yes / do it
DENY = re.compile(r"^\s*(hey\s+)?((jarvis|ஜார்விஸ்)[,\s]+)?(no|nope|deny|denied|cancel( it| that)?|don'?t|stop|abort"
                  r"|வேண்டாம்|இல்லை|ரத்து\s*(செய்|பண்ணு)?\S*)[\s.!]*$", re.I)  # Tamil: don't / no / cancel
HISTORY_CHAR_BUDGET = 16_000
LOCAL_HISTORY_CHAR_BUDGET = 5_000  # local models usually run with a small context window


class Jarvis:
    def __init__(self, settings: Settings | None = None, providers: list[LLMProvider] | None = None):
        self.settings = settings or load_settings()
        self.model_warm: dict = {}  # last preload of the local model (see _keep_model_warm)
        self.translator = Translator(self.settings.translator_dir or self.settings.data_dir / "models" / "nllb-200-distilled-600M-ct2-int8")
        self.db = Database(self.settings.db_path)
        self.bus = EventBus()
        self.audit = AuditLog(self.db)
        self.guard = PathGuard(self.settings.allowed_roots, self.settings.data_dir, self.settings.blocked_paths)
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
        self.voice = VoiceService(self)  # offline wake word + speech recognition; started by the desktop app
        self.selftest = None  # the running/last self-test (selftest.py)
        self._chat_tasks: dict[str, asyncio.Task] = {}
        self._background: set[asyncio.Task] = set()
        self._continuations: dict[str, int] = {}
        self._continuing: set[str] = set()  # conversations with an automatic continuation running
        self._decisions: dict[str, int] = {}  # per conversation: approval decisions so far (numbered)
        self._continued: dict[str, int] = {}  # per conversation: the decisions the latest continuation already covers
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
            compat = OpenAICompatProvider(s.openai_base_url, s.openai_model, get_secret("OPENAI_API_KEY"), stream=s.openai_stream, keep_alive=s.openai_keep_alive, max_tokens=s.openai_max_tokens)
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
        self._warm_task = asyncio.create_task(self._keep_model_warm(), name="jarvis-model-warm")
        if self.settings.listen_on_start:
            self.voice.start(asyncio.get_running_loop())

    WARM_EVERY = 45 * 60  # seconds; shorter than the 2 h keep-alive, so the model stays loaded while JARVIS runs

    async def _keep_model_warm(self) -> None:
        """Load the local model at start-up with its fixed prompt already processed, and refresh that now and then, so a
        request after a quiet hour is as fast as one a minute later. Failures (Ollama not running, not enough memory)
        are reported once and retried quietly; requests still work, they are just slower."""
        reported = False
        while True:
            delay = self.WARM_EVERY
            try:
                t0 = time.time()
                usage = await agents_mod.warm_local_model(self)
                if usage is None:
                    return  # no local model to warm
                self.model_warm = {"at": time.time(), "seconds": round(time.time() - t0, 1), "prompt_tokens": usage.get("prompt_tokens"),
                                   "cached_tokens": (usage.get("prompt_tokens_details") or {}).get("cached_tokens")}
                reported = False
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 - warming is best effort
                self.model_warm = {"at": time.time(), "error": str(e)[:300]}
                if not reported:
                    note = self.db.notify("warning", "Local model could not be preloaded", f"{e}. The first reply may be slow.")
                    self.bus.emit("notification", notification=note)
                    reported = True
                delay = 10 * 60
            await asyncio.sleep(delay)

    async def stop(self) -> None:
        task = getattr(self, "_warm_task", None)
        if task:
            task.cancel()
        await asyncio.to_thread(self.voice.stop)
        primary = self.llm.primary
        if getattr(primary, "keep_alive", "") and hasattr(primary, "unload"):
            await primary.unload()  # give the model's memory back when JARVIS quits
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
            if used > (LOCAL_HISTORY_CHAR_BUDGET if self.llm.current().name == "openai_compat" else HISTORY_CHAR_BUDGET) and kept:
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
        # One answer at a time per conversation: a new message from the user replaces the answer still being worked out
        # (the new turn sees the earlier message in its history). "Create a tab opening google.com" then "in my account"
        # once ran side by side and gave two contradicting answers (2026-11-20).
        running = self._chat_tasks.get(conversation_id)
        if actor == "chat" and running and not running.done() and running is not asyncio.current_task():
            running.cancel()
            await asyncio.wait([running], timeout=10)
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
                return self._finish(conversation_id, f"There are {len(pending)} actions waiting. Please approve or deny each one on screen so I act on the right one.", {"provider": "policy"})

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
        via = self.translation_language(text) if pin is None and actor == "chat" else None
        try:
            if via:
                result, via_english = await self._run_via_english(text, via, conversation_id=conversation_id, history=history,
                                                                  context_note="\n\n".join(notes), actor=actor, on_text=on_text)
            else:
                via_english = None
                result = await self.agents.run(self.root_agent(pinned_offline=pin is not None), text, conversation_id=conversation_id, history=history,
                                               context_note="\n\n".join(notes), actor=actor, on_text=on_text)
        except asyncio.CancelledError:
            partial = "".join(streamed).strip()
            self._finish(conversation_id, (partial + "\n\n" if partial else "") + "_(interrupted)_", {"interrupted": True}, state="idle")
            raise
        finally:
            if pin is not None:
                FORCE_PROVIDER.reset(pin)
            if self._chat_tasks.get(conversation_id) is asyncio.current_task():  # an interrupted turn ending late must not
                self._chat_tasks.pop(conversation_id, None)                      # forget the newer turn (it could not be cancelled)
            self.bus.active_task = None
        meta = {"task_id": result.task_id, "provider": result.provider, "tools": result.tools_used, "pending_approvals": result.pending_approvals,
                "errors": result.errors, "agents": sorted({t["agent"] for t in result.tools_used} | {"orchestrator"})}
        if via_english:
            meta["via_english"] = via_english
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

    def translation_language(self, text: str) -> str | None:
        """The language to route through English for this request, or None. Only for a local model (hosted models
        write Tamil well), only when the translation model is installed, and only for a request in an Indian script."""
        if not self.settings.translate_via_english or self.llm.current().name != "openai_compat":
            return None
        lang = language_of(text)
        return lang if lang in NLLB and self.translator.available() else None

    TO_ENGLISH = "Translate the user's request below into English. Reply with only the English translation, nothing else.\n\n"

    async def _read_in_english(self, text: str, lang: str) -> tuple[str, str]:
        """(English version of the request, problem). Asks the chat model first, with the flat agent's fixed prompt and
        tools so the cached prefix is reused (~5-8 s); falls back to the translation model (~1 s)."""
        provider = self.llm.current()
        try:
            tools = [t for t in self.tools.all() if t.name != "delegate"]
            resp = await provider.complete(agents_mod.flat_system(self), [{"role": "user", "content": with_context(self.TO_ENGLISH + text)}],
                                           [agents_mod.lean_spec(t) for t in agents_mod.flat_core(tools)], None, {"agent": "translate"})
            english = resp.text.strip().strip('"“”').strip()
            if english and not resp.tool_calls and language_of(english) is None:
                return english, ""
        except Exception:  # noqa: BLE001 - fall back to the translation model below
            pass
        try:
            english = (await asyncio.to_thread(self.translator.translate, text, lang, "en")).strip()
            return english, "" if english else "empty translation"
        except Exception as e:  # noqa: BLE001 - without a translation the model gets the original words
            return "", f"translation to English failed: {e}"

    async def _run_via_english(self, text: str, lang: str, *, conversation_id, history, context_note, actor, on_text):
        """Translate the request to English, let the model answer (and use tools) in English, translate the answer
        back. The English answer is not streamed: only the translated reply is shown and spoken.
        Measured on the owner's laptop: the chat model (Qwen3 4B) reads Tamil requests correctly (10/10) but writes poor
        Tamil; the translation model writes good Tamil but misread 2 of 8 requests. So the chat model reads, the
        translation model writes."""
        english, problem = await self._read_in_english(text, lang)
        if not english:
            result = await self.agents.run(self.root_agent(), text, conversation_id=conversation_id, history=history,
                                           context_note=context_note, actor=actor, on_text=on_text)
            return result, {"language": lang, "error": problem}
        result = await self.agents.run(self.root_agent(), english, conversation_id=conversation_id, history=history,
                                       context_note=context_note, actor=actor, on_text=None)
        english_reply = result.text
        try:
            reply = await asyncio.to_thread(self.translator.translate, english_reply, "en", lang)
        except Exception as e:  # noqa: BLE001 - better an English answer than none
            reply, english_reply = english_reply, english_reply + f"\n\n[translation back failed: {e}]"
        result.text = reply
        if reply:
            on_text(reply)
        return result, {"language": lang, "request": english, "reply": english_reply}

    def fast_path_applies(self, text: str) -> bool:
        """True when a model is configured but this request is a simple command the rule engine can do instantly."""
        if self.llm.primary.name == "offline" or not self.db.get_setting("fast_path", True) or len(text.split()) > 14:
            return False
        hit = match_rules(text, self.offline.env)
        if hit:
            return hit[1][0] not in self.NOT_FAST
        return fast_talk(text) is not None  # the time, the date, hello, thanks: no reason to wait for a model

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
            seq = self._decisions[conv] = self._decisions.get(conv, 0) + 1
            task = asyncio.create_task(self._continue_after_approval(conv, seq))
            self._background.add(task)
            task.add_done_callback(self._background.discard)

    async def _continue_after_approval(self, conversation_id: str, seq: int = 0) -> None:
        """Pick the task back up once its approvals are decided, so a multi-step job does not die at the
        first confirmation. Only for real models - the rule engine's requests are single-step."""
        await asyncio.sleep(0.05)
        running = self._chat_tasks.get(conversation_id)
        if running and not running.done():
            await asyncio.wait([running])
        if self.db.one("SELECT id FROM approvals WHERE status='pending' AND conversation_id=?", (conversation_id,)):
            return  # wait until every outstanding request is decided, then continue once
        # Several decisions made together (two clicks, "approve all") each schedule this; only one may continue, or the
        # model runs twice and repeats actions (found by the self-test, 2026-11-19): skip when a continuation is running,
        # or one started after this decision was made. No await between these checks and the bookkeeping below.
        if conversation_id in self._continuing or self._continued.get(conversation_id, 0) >= seq > 0:
            return
        self._continuing.add(conversation_id)
        self._continued[conversation_id] = self._decisions.get(conversation_id, 0)
        try:
            await self._continue_once(conversation_id)
        finally:
            self._continuing.discard(conversation_id)

    async def _continue_once(self, conversation_id: str) -> None:
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
