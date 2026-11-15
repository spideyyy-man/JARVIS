"""Offline provider: a deterministic intent engine that implements the same interface as a real
model. It exists so JARVIS (a) works with no API key and (b) degrades gracefully when a hosted
model is unreachable. It is pattern matching, not reasoning: it handles the request shapes below
and says so honestly when a request is outside them."""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ..knowledge import tokenize
from .base import LLMResponse, OnText, ToolCall

NAMED_DIRS = {"downloads": "Downloads", "documents": "Documents", "desktop": "Desktop", "pictures": "Pictures", "music": "Music", "videos": "Videos"}
TYPE_EXT = {"pdf": [".pdf"], "spreadsheet": [".xlsx", ".xls", ".csv"], "csv": [".csv"], "image": [".png", ".jpg", ".jpeg", ".gif", ".webp"],
            "photo": [".jpg", ".jpeg", ".png", ".heic"], "markdown": [".md"], "python": [".py"], "presentation": [".pptx", ".ppt"],
            "word": [".docx", ".doc"], "docx": [".docx"], "json": [".json"], "log": [".log"], "text": [".txt"], "typescript": [".ts", ".tsx"]}
FILLER = set("find locate search look where is are the a an my me for file files document documents named called about that which with in on of to "
             "please jarvis can you could would folder directory from any all some show get i need want recent recently latest last this week today "
             "yesterday month modified changed containing contains mentions mention script scripts notes note".split()) | set(TYPE_EXT)
_PATH = re.compile(r"""(?:["'`])((?:~|/)[^"'`]+)(?:["'`])|((?:~|/)[^\s,;"'`]*[^\s,;"'`.?!:])""")
_QUOTED = re.compile(r"""["'`]([^"'`]{2,})["'`]""")


@dataclass
class Env:
    home: Path
    default_project: Callable[[], str]
    workflows: Callable[[], list[dict]]


@dataclass
class Rule:
    agent: str
    pattern: re.Pattern
    build: Callable[[str, re.Match, Env], tuple[str, dict] | None]
    whole: bool = False  # must see the whole utterance (do not split into clauses)


def find_path(text: str, env: Env, allow_project: bool = True) -> str | None:
    m = _PATH.search(text)
    if m:
        return m.group(1) or m.group(2)
    low = text.lower()
    for word, folder in NAMED_DIRS.items():
        if re.search(rf"\b{word}\b", low):
            return str(env.home / folder)
    if re.search(r"\bhome (folder|directory|dir)\b", low):
        return str(env.home)
    if allow_project and re.search(r"\b(this|current|my|the)\s+(project|repo|repository|codebase|code base)\b", low):
        return env.default_project() or None
    return None


def _clean(text: str, *drop: str) -> str:
    text = re.sub(r"^\s*(hey\s+)?jarvis[,:\s]+", "", text, flags=re.I)
    for d in drop:
        text = re.sub(d, " ", text, flags=re.I)
    return re.sub(r"\s+", " ", text).strip(" ?.!,")


# ------------------------------------------------------------------ rule builders
def _search_files(text: str, m: re.Match, env: Env):
    low = text.lower()
    args: dict = {}
    path = find_path(text, env)
    if path:
        args["root"] = path
    exts = sorted({e for word, es in TYPE_EXT.items() if re.search(rf"\b{word}s?\b", low) for e in es})
    if exts:
        args["extensions"] = exts
    for pat, days in ((r"\btoday\b", 1), (r"\byesterday\b", 2), (r"\b(this|last|past) week\b|\brecent(ly)?\b", 7), (r"\b(this|last|past) month\b", 31)):
        if re.search(pat, low):
            args["modified_within_days"] = days
            break
    c = re.search(r"\b(?:containing|contains|that mentions?|mentioning|with the text)\s+[\"'`]?([^\"'`]+?)[\"'`]?\s*(?:$|\bin\b|\bunder\b)", text, re.I)
    if c:
        args["contains_text"] = c.group(1).strip()
        low = low.replace(c.group(0).lower(), " ")
    low = _PATH.sub(" ", low)
    kws = [w for w in re.findall(r"[a-z0-9_\-]{2,}", low) if w not in FILLER and not w.isdigit()]
    if kws:
        args["keywords"] = kws[:6]
    return "search_files", args


def _interval(text: str) -> dict | None:
    low = text.lower()
    m = re.search(r"every\s+(\d+)?\s*(second|minute|hour|day)s?", low)
    if m:
        return {"type": "interval", "seconds": int(m.group(1) or 1) * {"second": 1, "minute": 60, "hour": 3600, "day": 86400}[m.group(2)]}
    m = re.search(r"(?:every day|daily|each day|every morning|every evening)(?:\s+at\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?)?", low)
    if m:
        hour = int(m.group(1)) if m.group(1) else (9 if "evening" not in low else 18)
        if m.group(3) == "pm" and hour < 12:
            hour += 12
        return {"type": "cron", "expr": f"{int(m.group(2) or 0)} {hour} * * *"}
    m = re.search(r"\bin\s+(\d+)\s*(second|minute|hour)s?\b", low)
    if m:
        return {"type": "once", "at": time.time() + int(m.group(1)) * {"second": 1, "minute": 60, "hour": 3600}[m.group(2)]}
    return None


def _workflow(text: str, m: re.Match, env: Env):
    schedule = _interval(text)
    if not schedule:
        return None
    action = _clean(text, r"every\s+(\d+\s*)?(second|minute|hour|day)s?", r"(every day|daily|each day|every morning|every evening)(\s+at\s+[\d:]+\s*(am|pm)?)?",
                    r"\bin\s+\d+\s*(second|minute|hour)s?\b", r"\b(schedule|automate|set up|create)( a| an)?( task| workflow| automation| job)?( to| that)?\b",
                    r"\b(and|then)?\s*(notify|alert|tell|warn) me.*$")
    step = next((r.build(action, mm, env) for r in RULES if not r.whole and r.agent != "orchestrator" and (mm := r.pattern.search(action))), None)
    steps: list[dict] = []
    if step and step[0] not in ("create_workflow",):
        steps.append({"id": "s1", "tool": step[0], "args": step[1], "retries": 1})
    th = re.search(r"\b(cpu|memory|ram|disk)\b.{0,30}?\b(above|over|exceeds?|greater than|more than)\s+(\d+)", text, re.I)
    if re.search(r"\b(notify|alert|tell|warn|remind) me\b", text, re.I):
        note: dict = {"id": "notify", "notify": f"Scheduled check: {action or 'reminder'}"}
        if th and steps and steps[0]["tool"] == "system_stats":
            key = {"ram": "memory"}.get(th.group(1).lower(), th.group(1).lower())
            note = {"id": "notify", "level": "warning", "notify": f"{key} usage is {{{{s1.{key}.percent}}}}% (threshold {th.group(3)}%)",
                    "when": {"ref": f"s1.{key}.percent", "op": ">", "value": float(th.group(3))}}
        steps.append(note)
    if not steps:
        return None
    return "create_workflow", {"name": (action or "Scheduled task")[:60].capitalize(), "schedule": schedule, "steps": steps}


def _run_workflow(text: str, m: re.Match, env: Env):
    name = m.group(1).strip(" .\"'").lower()
    for wf in env.workflows():
        if name in wf["name"].lower() or wf["id"] == name:
            return "run_workflow", {"id": wf["id"]}
    return "list_workflows", {}


def R(agent: str, pattern: str, build, whole: bool = False) -> Rule:
    return Rule(agent, re.compile(pattern, re.I), build, whole)


def _p(tool: str, key: str = "path", **extra):
    """Builder for tools that take one path argument."""
    def build(text, m, env):
        path = find_path(text, env)
        return (tool, {**({key: path} if path else {}), **extra})
    return build


RULES: list[Rule] = [
    # automation first: "every 10 minutes check X" must not be captured by X's own rule
    R("automation", r"\b(every\s+(\d+\s*)?(second|minute|hour|day)s?|every (day|morning|evening)|daily|schedule\b.*\bin \d+)", _workflow, whole=True),
    R("automation", r"\brun (?:the )?(?:workflow|automation)\s+(.+)$", _run_workflow, whole=True),
    R("automation", r"\b(list|show|what).*(workflows?|automations?|scheduled tasks?)\b", lambda t, m, e: ("list_workflows", {})),
    # orchestrator-level
    R("orchestrator", r"\b(?:generate|create|make|draw|paint|render)\b\s+(?:me\s+)?(?:an?\s+|the\s+)?(?:image|picture|photo|illustration|drawing|logo|wallpaper|icon)\s+(?:of|for|showing|with)?\s*(.+)$|^\s*(?:jarvis[,\s]+)?(?:draw|paint)\s+(?:me\s+)?(.+)$",
      lambda t, m, e: ("generate_image", {"prompt": (m.group(1) or m.group(2)).strip(" .")}), whole=True),
    R("orchestrator", r"\b(what (did|have) i (do|done|accomplish)|accomplished|daily summary|summar\w+ (of )?(my|the) day|productivity)\b", lambda t, m, e: ("daily_summary", {})),
    R("orchestrator", r"\bremember (?:that|this:?)\s+(.+)$", lambda t, m, e: ("remember", {"content": m.group(1).strip(), "kind": "preference" if re.search(r"\b(prefer|like|always|never)\b", t, re.I) else "fact"})),
    R("orchestrator", r"\bwhat do you (know|remember) about\s+(.+)$", lambda t, m, e: ("recall", {"query": m.group(2)})),
    R("orchestrator", r"\b(remind me (about|of)|unfinished|open tasks?|pending tasks?|my tasks|to-?do list|what.*\btasks\b)", lambda t, m, e: ("list_dev_tasks", {})),
    R("orchestrator", r"\b(?:remind me to|add (?:a )?(?:task|todo|reminder)(?: to)?:?|todo:)\s+(.+)$", lambda t, m, e: ("add_dev_task", {"title": m.group(1).strip(" .")})),
    # knowledge
    R("knowledge", r"\b(index|ingest|import)\b.*(~|/)|\badd\b.*(~|/).*\b(knowledge|index)", lambda t, m, e: ("kb_ingest", {"path": find_path(t, e, False), **({"space": s.group(1)} if (s := re.search(r"\b(?:into|to|in)\s+(?:the\s+)?[\"']?([\w\-]+)[\"']?\s+space", t, re.I)) else {})}) if find_path(t, e, False) else None),
    R("knowledge", r"\b(my|the|indexed) (docs|documents?|notes|knowledge ?base|papers|files|documentation)\b.*\b(say|explain|about|describe|what|how|why)\b|\b(according to|based on|using|from) (my|the) (indexed |own )?(docs|documents?|notes|knowledge ?base|documentation)\b",
      lambda t, m, e: ("kb_search", {"query": _clean(t, r"\b(according to|based on|using|from|in) (my|the) (indexed |own )?(docs|documents?|notes|knowledge ?base|documentation)\b", r"\bindexed\b", r"^(please )?(explain|describe|tell me)( about)?\b")})),
    R("knowledge", r"\bwhat (documents|spaces|knowledge)\b|\blist\b.*\b(knowledge|indexed)", lambda t, m, e: ("kb_list", {})),
    R("knowledge", r"\b(search|find)\b.*\b(previous|past|earlier|old)?\s*conversations?\b(?:\s+(?:for|about))?\s*(.*)$|\bdid we (talk|discuss)\w*( about)?\s+(.+)$", lambda t, m, e: ("conversation_search", {"query": (m.group(3) or m.group(6) or t).strip()})),
    R("knowledge", r"\bsave (?:a |this )?note:?\s+(.+)$", lambda t, m, e: ("save_note", {"title": m.group(1)[:50], "text": m.group(1)})),
    # monitoring
    R("monitoring", r"\b(performance|cpu|ram\b|memory usage|system (health|status|load)|how is my (computer|machine|system|pc|laptop)|running slow)", lambda t, m, e: ("system_stats", {})),
    R("monitoring", r"\bprocess(es)?\b|\bwhat('s| is) running\b", lambda t, m, e: ("list_processes", {"sort_by": "memory" if re.search(r"memory|ram", t, re.I) else "cpu"})),
    R("monitoring", r"\b(disk|storage|free space)\b", _p("disk_usage")),
    # research
    R("research", r"github\.com/([\w.\-]+/[\w.\-]+)|\b(?:evaluate|assess|check out)\b.*\brepo(?:sitory)?\s+([\w.\-]+/[\w.\-]+)", lambda t, m, e: ("github_repo_info", {"repo": m.group(1) or m.group(2)})),
    R("research", r"\b(summari[sz]e|fetch|read)\b.*(https?://\S+)", lambda t, m, e: ("fetch_url", {"url": m.group(2).rstrip(".,)")})),
    R("research", r"\b(research|look up|search the web|search online|web search|google|latest (developments|news|version)|what'?s new in|compare\b.*\b(vs\.?|versus|with|and|to)\b)",
      lambda t, m, e: ("web_search", {"query": _clean(t, r"^(please )?(research|look up|search the web for|search online for|web search for|google)\b", r"\b(and )?(cite|with) (your )?sources\b")})),
    # coding
    R("coding", r"\b(plan|roadmap)\b.*\b(project|repo|repository|codebase)\b|\b(project|repo|codebase)\b.*\b(plan|roadmap)\b", _p("repo_overview")),
    R("coding", r"\b(run|execute)\b.*\btests?\b|\btest suite\b", _p("run_tests")),
    R("coding", r"\bgit status\b|\buncommitted\b|\bwhat (has )?changed\b", _p("git_status")),
    R("coding", r"\b(git )?diff\b", _p("git_diff")),
    R("coding", r"\b(commits?|git log|commit history)\b", _p("git_log")),
    R("coding", r"\b(search|grep|find)\b.*\b(code|function|class|usages?|symbol)\b", lambda t, m, e: ("search_code", {"pattern": re.escape((q.group(1) if (q := _QUOTED.search(t)) else _clean(t).split()[-1])), **({"path": p} if (p := find_path(t, e)) else {})})),
    R("coding", r"\b(inspect|analy[sz]e|explain|understand|overview|review|look at|summari[sz]e|describe)\b.*\b(repo|repository|codebase|code base|project)\b", _p("repo_overview")),
    # computer
    R("computer", r"\b(development|dev) environment\b|\btoolchains?\b", lambda t, m, e: ("dev_environment", {})),
    R("computer", r"\binstalled (apps?|applications?|software|programs?)\b", lambda t, m, e: ("list_installed_apps", {})),
    R("computer", r"\b(organi[sz]e|tidy|clean up|sort)\b.*\b(folder|directory|downloads|desktop|documents|files)\b|\b(organi[sz]e|tidy)\b.*(~|/)", lambda t, m, e: ("organize_folder_plan", {"path": find_path(t, e, False)}) if find_path(t, e, False) else None),
    R("computer", r"\b(delete|remove|trash)\b.*?((?:~|/)\S+)", lambda t, m, e: ("delete_path", {"path": m.group(2).rstrip(".,")})),
    R("computer", r"\b(move|rename)\b\s+((?:~|/)\S+)\s+(?:to|into)\s+((?:~|/)\S+)", lambda t, m, e: ("move_path", {"source": m.group(2), "destination": m.group(3).rstrip(".,")})),
    R("computer", r"\bcopy\b\s+((?:~|/)\S+)\s+(?:to|into)\s+((?:~|/)\S+)", lambda t, m, e: ("copy_path", {"source": m.group(1), "destination": m.group(2).rstrip(".,")})),
    R("computer", r"\b(create|make)\b.*\b(folder|directory)\b.*?((?:~|/)\S+)", lambda t, m, e: ("create_directory", {"path": m.group(3).rstrip(".,")})),
    R("computer", r"\b(find|locate|search for|look for|where (is|are))\b", _search_files),
    R("computer", r"\b(list|show|what'?s in)\b.*\b(folder|directory|files)\b|\b(list|show)\b.*(~|/)", _p("list_dir")),
    R("computer", r"\b(read|show|print|cat)\b.*?((?:~|/)\S+\.\w+)", lambda t, m, e: ("read_file", {"path": m.group(2)})),
    R("computer", r"^\s*(?:hey\s+)?(?:jarvis[,\s]+)?(?:please\s+)?(?:open|launch|start)\s+(?:the\s+|my\s+)?(.+?)\s*(?:app|application)?[.!]?$", lambda t, m, e: ("open_application", {"name": m.group(1)})),
]

SMALL_TALK = [
    (r"^\s*(hi|hello|hey|good (morning|afternoon|evening))\b", lambda: "Hello. All systems are online. What do you need?"),
    (r"\b(who|what) are you\b|\byour name\b", lambda: "I'm JARVIS, your personal assistant. I can inspect this computer, work with your projects and documents, research topics and run automations."),
    (r"\bwhat (time|day|date)\b", lambda: time.strftime("It's %H:%M on %A, %d %B %Y.")),
    (r"\b(thanks|thank you)\b", lambda: "You're welcome."),
    (r"\bwhat can you do\b|\bhelp\b", lambda: CAPABILITIES),
]
CAPABILITIES = ("Here's what I can do right now:\n"
                "- **Computer**: performance, processes, disk usage, find/list/read files, organize folders (with your approval)\n"
                "- **Code**: inspect a repository, git status/log/diff, search code, run tests, build a development plan\n"
                "- **Knowledge**: index PDF/DOCX/TXT/Markdown and answer from them with citations\n"
                "- **Research**: web search with tracked sources, GitHub repository evaluation\n"
                "- **Automation**: scheduled and conditional workflows, reminders, a daily summary")
OFFLINE_NOTE = ("I'm running on the built-in offline engine (no language model is configured), so I can only handle requests I can map to a "
                "concrete capability. Set ANTHROPIC_API_KEY, or point JARVIS at a local model, for open-ended conversation and reasoning.")


def match_rules(text: str, env: Env, agent: str | None = None, allow_whole: bool = True) -> tuple[Rule, tuple[str, dict]] | None:
    for rule in RULES:
        if agent and rule.agent != agent:
            continue
        if rule.whole and not allow_whole:
            continue
        m = rule.pattern.search(text)
        if m:
            built = rule.build(text, m, env)
            if built:
                return rule, built
    return None


class OfflineProvider:
    name = "offline"
    model = "rule-engine"

    def __init__(self, env: Env):
        self.env = env
        self._n = 0

    def _id(self) -> str:
        self._n += 1
        return f"off_{self._n}"

    async def complete(self, system, messages, tools, on_text: OnText | None = None, meta=None) -> LLMResponse:
        agent = (meta or {}).get("agent", "orchestrator")
        available = {t["name"] for t in tools}
        turn_start = max(i for i, m in enumerate(messages) if m["role"] == "user")
        question = messages[turn_start]["content"]
        if messages[-1]["role"] == "tool":
            resp = self._after_tools(agent, question, messages[turn_start:], available)
        else:
            resp = self._decide(agent, question, available)
        resp.provider, resp.model = self.name, self.model
        if resp.text and on_text:
            for piece in re.findall(r"\S+\s*", resp.text):
                on_text(piece)
        return resp

    # -- first step of a turn ---------------------------------------------------
    def _decide(self, agent: str, text: str, available: set[str]) -> LLMResponse:
        self._current = text
        if agent != "orchestrator":
            hit = match_rules(text, self.env, agent)
            if hit and hit[1][0] in available:
                return LLMResponse(tool_calls=[ToolCall(self._id(), *hit[1])])
            if agent == "knowledge" and "kb_search" in available:  # default for the knowledge agent: look it up
                return LLMResponse(tool_calls=[ToolCall(self._id(), "kb_search", {"query": _clean(text)})])
            return LLMResponse(text=f"The {agent} agent could not map this request to one of its tools in offline mode.")

        whole = match_rules(text, self.env)
        if whole and whole[0].whole:
            return self._route([whole])
        # multi-intent: "do X and then Y" -> one delegation per clause, when each clause is recognisable
        clauses = [c for c in re.split(r"\s*(?:,?\s+and then\s+|,?\s+then\s+|;\s*|\s+and\s+)", _clean(text)) if c.strip()]
        if len(clauses) > 1:
            hits = [match_rules(c, self.env, allow_whole=False) for c in clauses]
            if all(hits) and len({(h[0].agent, h[1][0]) for h in hits}) > 1:
                return self._route(hits, clauses)
        if whole:
            return self._route([whole])
        for pattern, reply in SMALL_TALK:
            if re.search(pattern, text, re.I):
                return LLMResponse(text=reply())
        if re.search(r"\bplan\b|\broadmap\b", text, re.I) and "create_plan" in available:
            goal = _clean(text, r"^(please )?(create|make|write|draft|build|give me)( me)?( a| an)?", r"\b(multi-step |development |implementation |project )*plan\b( for| to)?")
            return LLMResponse(tool_calls=[ToolCall(self._id(), "create_plan", {"title": f"Plan: {goal or 'project'}", "steps": plan_steps(goal or "the project", None)})])
        if re.search(r"\?\s*$|^\s*(what|how|why|explain|describe|tell me)\b", text, re.I) and "delegate" in available:
            return LLMResponse(tool_calls=[ToolCall(self._id(), "delegate", {"agent": "knowledge", "task": text})])
        return LLMResponse(text=f"I couldn't map that to something I can do offline.\n\n{CAPABILITIES}\n\n{OFFLINE_NOTE}")

    def _route(self, hits: list, clauses: list[str] | None = None) -> LLMResponse:
        calls = []
        for i, (rule, (tool, args)) in enumerate(hits):
            if rule.agent == "orchestrator":
                calls.append(ToolCall(self._id(), tool, args))
            else:
                calls.append(ToolCall(self._id(), "delegate", {"agent": rule.agent, "task": clauses[i] if clauses else self._current}))
        return LLMResponse(tool_calls=calls)

    # -- after tool results --------------------------------------------------------
    def _after_tools(self, agent: str, question: str, turn: list[dict], available: set[str]) -> LLMResponse:
        import json

        called = [c for m in turn if m["role"] == "assistant" for c in m.get("tool_calls", [])]
        called_names = [c.name for c in called]
        results = {r["id"]: r for m in turn if m["role"] == "tool" for r in m["results"]}
        last_results = turn[-1]["results"]

        def data(r):
            try:
                return json.loads(r["content"])
            except (ValueError, TypeError):
                return r["content"]

        # chained follow-ups (each fires at most once per turn)
        for r in last_results:
            d = data(r)
            if r.get("is_error") or not isinstance(d, dict):
                continue
            if r["name"] == "organize_folder_plan" and d.get("files") and "organize_folder" not in called_names and "organize_folder" in available:
                return LLMResponse(tool_calls=[ToolCall(self._id(), "organize_folder", {"path": d["path"]})])
            if r["name"] == "repo_overview" and re.search(r"\bplan\b|\broadmap\b", question, re.I) and "create_plan" not in called_names and "create_plan" in available:
                goal = _clean(question, r"^.*?\b(plan|roadmap)\b( for| to)?", r"\b(this|my|the|current) (project|repo|repository|codebase)\b")
                return LLMResponse(tool_calls=[ToolCall(self._id(), "create_plan", {"title": f"Development plan: {Path(d['path']).name}", "steps": plan_steps(goal or "the next milestone", d)})])
            if r["name"] == "web_search" and d.get("results") and "fetch_url" not in called_names and "fetch_url" in available:
                return LLMResponse(tool_calls=[ToolCall(self._id(), "fetch_url", {"url": x["url"], "max_chars": 3000}) for x in d["results"][:2]])

        parts = []
        for call in called:
            r = results.get(call.id)
            if r is None:
                continue
            d = data(r)
            if r.get("is_error"):
                parts.append(render_error(call.name, d))
            elif isinstance(d, dict) and d.get("status") == "pending_approval":
                parts.append(f"I've prepared this action, but it changes your system, so it's waiting for your approval:\n\n> {d.get('preview', call.name).splitlines()[0]}\n\n"
                             f"Approve or deny it in the Command Center, or just say \"approve\" (request `{d['approval_id']}`).")
            elif call.name == "repo_overview" and "create_plan" in called_names:
                continue  # the plan already incorporates the overview
            else:
                renderer = RENDERERS.get(call.name)
                text = renderer(d, question, turn) if renderer else f"`{call.name}` returned: {str(d)[:600]}"
                if text:
                    parts.append(text)
        return LLMResponse(text="\n\n".join(parts) or "Done.")


def plan_steps(goal: str, repo: dict | None) -> list[dict]:
    steps = []
    if repo:
        git = repo.get("git") or {}
        langs = ", ".join(l["language"] for l in repo.get("languages", [])[:3]) or "unknown stack"
        steps.append({"title": "Baseline the repository", "detail": f"Stack: {langs}. Top-level layout: {', '.join(repo.get('top_level', [])[:8])}."})
        if git.get("uncommitted_changes"):
            steps.append({"title": "Stabilise the working tree", "detail": f"{git['uncommitted_changes']} uncommitted change(s) on {git.get('branch', 'the current branch')}: review, then commit or stash before new work."})
        steps.append({"title": "Establish a test baseline", "detail": f"Run `{repo['test_command']}` and record failures." if repo.get("test_command") else "No test command detected: add a minimal test harness first so later changes are verifiable."})
        if repo.get("todo_markers"):
            steps.append({"title": "Triage known debt", "detail": f"{repo['todo_markers']} TODO/FIXME marker(s) in the code: convert the real ones into tracked tasks."})
        if not repo.get("readme_excerpt"):
            steps.append({"title": "Document the project", "detail": "No README found: write setup, run and test instructions."})
    else:
        steps.append({"title": "Clarify scope and success criteria", "detail": f"Write down what done means for {goal}, the constraints, and what is explicitly out of scope."})
        steps.append({"title": "Survey the current state", "detail": "Inventory existing code, data and dependencies that the work touches."})
    steps += [
        {"title": f"Design: {goal}", "detail": "Break the goal into components and interfaces; identify the riskiest assumption and plan to test it first."},
        {"title": "Implement in vertical slices", "detail": "Deliver the smallest end-to-end path first, then widen. Keep each slice behind a passing test."},
        {"title": "Verify", "detail": "Run the full test suite, exercise failure paths, and review the diff before merging."},
        {"title": "Review and document", "detail": "Update docs and changelog; capture follow-ups as tracked tasks."},
    ]
    return steps


# ------------------------------------------------------------------ renderers
def render_error(tool: str, d) -> str:
    err = d.get("error") if isinstance(d, dict) else str(d)
    if tool in ("web_search", "fetch_url", "github_repo_info"):
        return f"I could not complete the web lookup, so I have **no** web findings to report and won't guess. Reason: {err}"
    return f"I couldn't complete `{tool}`: {err}"


def _r_stats(d, q, t):
    c, m, k = d["cpu"], d["memory"], d["disk"]
    top = ", ".join(f"{p['name']} ({p['cpu_percent']}%)" for p in d.get("top_cpu", [])[:3])
    topm = ", ".join(f"{p['name']} ({p['memory_percent']}%)" for p in d.get("top_memory", [])[:3])
    lines = [f"**System performance** - {d.get('platform', '')}, up {d.get('uptime_hours', '?')} h.",
             f"- CPU: **{c['percent']}%** across {c['cores']} cores" + (f" (load {', '.join(map(str, c['load_avg']))})" if c.get("load_avg") else ""),
             f"- Memory: **{m['percent']}%** used ({m['used_gb']} of {m['total_gb']} GB, {m['available_gb']} GB available)",
             f"- Disk: **{k['percent']}%** used, {k['free_gb']} GB free of {k['total_gb']} GB",
             f"- Busiest by CPU: {top or 'n/a'}", f"- Largest by memory: {topm or 'n/a'}"]
    h = d.get("history") or {}
    if h.get("samples", 0) > 5:
        lines.append(f"- Last {h['window_minutes']} min: CPU avg {h['cpu_avg']}% (peak {h['cpu_max']}%), memory avg {h['memory_avg']}%")
    return "\n".join(lines) + "\n\n" + " ".join(d.get("assessment", []))


def _r_search(d, q, t):
    if not d["results"]:
        return f"I searched {d['scanned']} files under `{d['root']}` and found nothing matching." + (" The search hit its time limit, so it may be incomplete." if d.get("timed_out") else "")
    lines = [f"Found {d['matched']} match(es) under `{d['root']}` ({d['scanned']} files scanned). Best matches:"]
    for r in d["results"][:10]:
        extra = f" - line {r['match']['line']}: \"{r['match']['text'][:80]}\"" if r.get("match") else ""
        lines.append(f"- `{r['path']}` ({r['size'] / 1024:.0f} KB, modified {r['modified']}){extra}")
    return "\n".join(lines)


def _r_repo(d, q, t):
    git = d.get("git")
    langs = ", ".join(f"{l['language']} ({l['lines']} lines in {l['files']} files)" for l in d.get("languages", [])[:4]) or "no recognised source files"
    lines = [f"**Repository `{Path(d['path']).name}`** at `{d['path']}`", f"- Languages: {langs}"]
    if git and not git.get("error"):
        lines.append(f"- Git: branch `{git['branch']}`, {git['uncommitted_changes']} uncommitted change(s)")
        if git.get("recent_commits"):
            lines.append("- Recent commits:\n" + "\n".join(f"  - {c}" for c in git["recent_commits"][:5]))
    else:
        lines.append("- Not a git repository" if not git else f"- Git error: {git['error']}")
    lines.append(f"- Layout: {', '.join(d.get('top_level', [])[:14])}")
    if d.get("entry_points"):
        lines.append(f"- Entry points: {', '.join(d['entry_points'][:6])}")
    lines.append(f"- Tests: `{d['test_command']}`" if d.get("test_command") else "- Tests: no test command detected")
    if d.get("manifests"):
        lines.append(f"- Manifests: {', '.join(d['manifests'])}")
    lines.append(f"- {d.get('todo_markers', 0)} TODO/FIXME marker(s)")
    if d.get("readme_excerpt"):
        first = next((l.strip("# ").strip() for l in d["readme_excerpt"].splitlines() if l.strip()), "")
        lines.append(f"- README: {first[:160]}")
    return "\n".join(lines)


def _r_kb(d, q, t):
    hits = d.get("results", [])
    if not hits:
        return ("Nothing in your indexed documents matches that question, and without a language model I won't answer from general knowledge. "
                "Index relevant material first (Knowledge Explorer), or configure a model.")
    qtok = set(tokenize(d["query"]))
    scored = []
    for h in hits[:4]:
        for sent in re.split(r"(?<=[.!?])\s+|\n+", h["text"]):
            sent = sent.strip(" #*-")
            overlap = len(qtok & set(tokenize(sent)))
            if overlap and 30 < len(sent) < 420:
                scored.append((overlap / (1 + len(sent) / 400), sent, h["cite"]))
    scored.sort(key=lambda s: s[0], reverse=True)
    picked, seen = [], set()
    for _s, sent, cite in scored:
        if sent not in seen:
            picked.append(f"{sent} {cite}")
            seen.add(sent)
        if len(picked) == 4:
            break
    body = " ".join(picked) if picked else f"{hits[0]['text'][:400]} {hits[0]['cite']}"
    sources = "\n".join(f"{h['cite']} {h['title']}" + (f" ({h['locator']})" if h["locator"] else "") + f" - `{h['source']}`" for h in hits[:4])
    return f"From your indexed documents:\n\n{body}\n\n**Sources**\n{sources}\n\n_Extractive answer (offline engine): these are the most relevant passages, quoted rather than synthesised._"


def _r_web(d, q, turn):
    import json

    pages = []
    for m in turn:
        if m["role"] == "tool":
            for r in m["results"]:
                if r["name"] == "fetch_url" and not r.get("is_error"):
                    try:
                        pages.append(json.loads(r["content"]))
                    except ValueError:
                        pass
    lines = [f"**Research: {d['query']}** (searched via {d['engine']}, {len(d['results'])} results)", ""]
    for i, r in enumerate(d["results"][:6], 1):
        lines.append(f"{i}. **{r['title']}** - {r['snippet'][:220]} [{i}]")
    if pages:
        lines.append("\n**From the top pages**")
        for p in pages:
            excerpt = " ".join(p["content"].split())[:500]
            lines.append(f"- {p['title']}: {excerpt}... ({p['url']})")
    lines.append("\n**Sources**")
    lines += [f"[{i}] {r['title']} - {r['url']}" for i, r in enumerate(d["results"][:6], 1)]
    lines.append("\n_Offline engine: this lists and quotes what was retrieved; it is not a synthesised analysis._")
    return "\n".join(lines)


def _r_plan(d, q, t):
    return f"**{d['title']}**\n" + "\n".join(f"{i}. **{s['title']}** - {s.get('detail', '')}" for i, s in enumerate(d["steps"], 1)) + "\n\nThe plan is saved with this task; tell me which step to start on."


def _r_tasks(d, q, t):
    if not d["tasks"]:
        return "You have no tracked tasks. Say \"remind me to ...\" to add one."
    lines = [f"You have {d['open']} unfinished task(s):"]
    for r in d["tasks"]:
        if r["status"] != "done":
            lines.append(f"- {r['title']}" + (f" [{r['project']}]" if r["project"] else "") + (" - **overdue**" if r.get("overdue") else "") + f" (`{r['id']}`)")
    return "\n".join(lines)


def _r_daily(d, q, t):
    acts = ", ".join(f"{a['tool']} x{a['n']}" for a in d["actions_executed"][:8]) or "none"
    lines = [f"**Summary for {d['date']}**", f"- {d['requests']} request(s) across {d['conversations']} conversation(s)",
             f"- Actions executed: {acts}"]
    for key, label in (("approved_changes", "Approved changes"), ("tasks_completed", "Tasks completed"), ("commits_today", "Commits today"), ("tasks_open", "Still open")):
        if d.get(key):
            lines.append(f"- {label}: " + "; ".join(str(x) for x in d[key][:6]))
    if d.get("documents_indexed"):
        lines.append(f"- Documents indexed: {d['documents_indexed']}")
    if d.get("workflow_runs"):
        lines.append("- Workflow runs: " + ", ".join(f"{r['n']} {r['status']}" for r in d["workflow_runs"]))
    return "\n".join(lines)


def _r_procs(d, q, t):
    return f"{d['total']} processes running. Top entries:\n" + "\n".join(f"- {p['name']} (pid {p['pid']}): CPU {p['cpu_percent']}%, memory {p['memory_percent']}%" for p in d["processes"][:10])


def _r_disk(d, q, t):
    lines = ["**Disks**"] + [f"- `{p['mount']}`: {p['percent']}% used, {p['free_gb']} GB free of {p['total_gb']} GB" for p in d["partitions"][:8]]
    if d.get("largest_in"):
        lines.append(f"\nLargest items in `{d['largest_in']['path']}`:")
        lines += [f"- {e['name']}{'/' if e['is_dir'] else ''}: {e['size_mb']} MB" for e in d["largest_in"]["entries"][:10]]
    return "\n".join(lines)


def _r_devenv(d, q, t):
    lines = ["**Development environment**"] + [f"- {k}: {v}" for k, v in d["toolchains"].items()]
    if d["editors"]:
        lines.append(f"- Editors: {', '.join(d['editors'])}")
    lines.append(f"\n{len(d['projects'])} project(s) under `{d['projects_root']}`:")
    lines += [f"- `{p['path']}` ({', '.join(p['stack']) or 'unknown stack'}{', git' if p['git'] else ''})" for p in d["projects"][:12]]
    return "\n".join(lines)


def _r_tests(d, q, t):
    if d.get("timed_out"):
        return f"The test run (`{d['command']}`) timed out."
    head = f"Tests **{'passed' if d['passed'] else 'failed'}** (`{d['command']}`, exit {d['exit_code']}, {d['duration_s']} s)."
    fails = "\n".join(f"- {f[0]}: {f[1]}" for f in d.get("failures", []))
    return head + (f"\n\nFailures:\n{fails}" if fails else "") + f"\n\n```\n{d['output'][-1200:]}\n```"


def _r_workflows(d, q, t):
    if not d["workflows"]:
        return "No workflows are defined yet."
    return "**Workflows**\n" + "\n".join(f"- {w['name']} (`{w['id']}`): {w['schedule']}, {'enabled' if w['enabled'] else 'disabled'}" for w in d["workflows"])


RENDERERS: dict[str, Callable] = {
    "generate_image": lambda d, q, t: f"Here is the image ({d['width']}x{d['height']}, {d['provider']}):\n\n![{d['prompt'][:80]}]({d['url']})\n\nSaved to `{d['file']}`.",
    "delegate": lambda d, q, t: d.get("result", ""),
    "system_stats": _r_stats, "search_files": _r_search, "recent_files": _r_search, "repo_overview": _r_repo, "kb_search": _r_kb,
    "web_search": _r_web, "fetch_url": lambda d, q, t: (f"**{d['title']}** ({d['url']})\n\n{' '.join(d['content'].split())[:900]}..." if "web_search" not in [c.name for m in t if m["role"] == "assistant" for c in m.get("tool_calls", [])] else ""),
    "create_plan": _r_plan, "list_dev_tasks": _r_tasks, "daily_summary": _r_daily, "list_processes": _r_procs, "disk_usage": _r_disk,
    "dev_environment": _r_devenv, "run_tests": _r_tests, "list_workflows": _r_workflows,
    "organize_folder_plan": lambda d, q, t: (f"`{d['path']}` has no loose files to organize." if not d.get("files") else ""),
    "organize_folder": lambda d, q, t: f"Organized `{d['path']}`: moved {d['moved']} file(s) into category folders.",
    "add_dev_task": lambda d, q, t: f"Noted. I'll track: \"{d['title']}\" (`{d['id']}`).",
    "remember": lambda d, q, t: f"I'll remember that: \"{d['content']}\".",
    "recall": lambda d, q, t: ("Here's what I have:\n" + "\n".join(f"- {m['content']}" for m in d["memories"])) if d["memories"] else "I don't have anything stored about that.",
    "kb_ingest": lambda d, q, t: (f"Indexed {d.get('indexed')} document(s) into the '{d.get('space')}' space ({d.get('new_or_updated')} new or updated)." if "indexed" in d else f"\"{d['title']}\" is {d['status']}" + (f" ({d['chunks']} passages, space '{d['space']}')." if d.get("chunks") else ".")),
    "kb_list": lambda d, q, t: "**Knowledge spaces**\n" + ("\n".join(f"- {s['space']}: {s['documents']} document(s), {s['chunks']} passages" for s in d["spaces"]) or "Nothing indexed yet."),
    "git_status": lambda d, q, t: f"```\n{d['status']}\n```",
    "git_diff": lambda d, q, t: f"```diff\n{d['diff'][:3000] or 'No changes.'}\n```",
    "git_log": lambda d, q, t: "**Recent commits**\n" + "\n".join(f"- `{c['hash']}` {c['date'][:10]} {c['author']}: {c['subject']}" for c in d["commits"]),
    "search_code": lambda d, q, t: ("\n".join(f"- `{m['file']}:{m['line']}` {m['text']}" for m in d["matches"][:20]) or "No matches."),
    "list_dir": lambda d, q, t: f"`{d['path']}` contains {d['count']} item(s):\n" + "\n".join(f"- {e['name']}{'/' if e['is_dir'] else ''}" for e in d["entries"][:40]),
    "read_file": lambda d, q, t: f"`{d['path']}`:\n```\n{d.get('content', '(binary file)')[:2500]}\n```",
    "list_installed_apps": lambda d, q, t: f"{len(d['desktop_applications'])} desktop application(s): " + ", ".join(a["name"] for a in d["desktop_applications"][:25]) + "\n\nDeveloper tools: " + ", ".join(c["name"] for c in d["developer_tools_on_path"]),
    "github_repo_info": lambda d, q, t: f"**{d['repo']}** - {d['description']}\n- Stars {d['stars']}, forks {d['forks']}, open issues {d['open_issues']}\n- Language {d['language']}, license {d['license']}\n- Last push {d['last_push']}, latest release {d['latest_release']}, archived: {d['archived']}\n\nSource: {d['url']}",
    "run_workflow": lambda d, q, t: f"Workflow run `{d['run_id']}` finished with status **{d['status']}** ({len(d['log'])} step(s) logged).",
    "create_workflow": lambda d, q, t: f"Workflow \"{d['name']}\" created (`{d['id']}`).",
    "conversation_search": lambda d, q, t: ("\n".join(f"- {m['title']}: {m['snippet']}" for m in d["matches"]) or "No earlier conversation matches that."),
    "open_application": lambda d, q, t: f"Launched {' '.join(d['launched'])}.",
    "save_note": lambda d, q, t: f"Saved the note \"{d['title']}\" to the '{d['space']}' space.",
}
