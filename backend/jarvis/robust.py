"""Making the agent loop survive weak models.

Small local models (the realistic case on a CPU-only laptop) fail in predictable ways: they write a
tool call as text instead of using the tool-call channel, misspell tool names, send "5" for 5, invent
arguments, and repeat the same call forever. Everything here is deterministic repair applied between
the model and the tool executor - the executor's own schema validation and permission checks still run
afterwards, so repair can never widen what a model is allowed to do."""
from __future__ import annotations

import difflib
import json
import re
from typing import Any

import numpy as np

from .llm.base import LLMResponse, ToolCall

_FENCE = re.compile(r"```(?:json|tool_call|tool|python)?\s*(\{.*?\})\s*```", re.S)
_TAG = re.compile(r"<(?:tool_call|function_call|tool)>\s*(\{.*?\})\s*</(?:tool_call|function_call|tool)>", re.S)
_CALL = re.compile(r"\b([a-z_][a-z0-9_]{2,40})\s*\(\s*(\{.*?\})\s*\)", re.S)
NAME_KEYS = ("name", "tool", "tool_name", "function", "action")
ARG_KEYS = ("arguments", "args", "parameters", "input", "tool_input", "params")


def _balanced_objects(text: str) -> list[str]:
    """Top-level {...} spans, respecting strings - for JSON a model dropped into prose."""
    out, depth, start, in_str, esc = [], 0, -1, False, False
    for i, ch in enumerate(text):
        if in_str:
            esc = (ch == "\\") and not esc
            if ch == '"' and not esc:
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}" and depth:
            depth -= 1
            if depth == 0:
                out.append(text[start:i + 1])
    return out


def resolve_name(name: str, allowed: set[str]) -> str | None:
    if name in allowed:
        return name
    cleaned = re.sub(r"^(functions?|tools?|default_api)[.:/]", "", str(name).strip()).replace("-", "_").replace(" ", "_").lower()
    if cleaned in allowed:
        return cleaned
    close = difflib.get_close_matches(cleaned, sorted(allowed), n=1, cutoff=0.82)
    return close[0] if close else None


def _as_call(obj: Any, allowed: set[str]) -> tuple[str, dict] | None:
    if not isinstance(obj, dict):
        return None
    if isinstance(obj.get("function"), dict):  # {"function": {"name":..., "arguments":...}}
        obj = obj["function"]
    name = next((obj[k] for k in NAME_KEYS if isinstance(obj.get(k), str)), None)
    if not name:
        return None
    resolved = resolve_name(name, allowed)
    if not resolved:
        return None
    args = next((obj[k] for k in ARG_KEYS if k in obj), {})
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except ValueError:
            return None
    return (resolved, args) if isinstance(args, dict) else None


def extract_text_calls(text: str, allowed: set[str]) -> tuple[list[tuple[str, dict]], str]:
    """Find tool calls a model wrote into its text. Returns (calls, text with those calls removed)."""
    calls, cleaned = [], text
    for pattern in (_TAG, _FENCE):
        for m in pattern.finditer(text):
            try:
                call = _as_call(json.loads(m.group(1)), allowed)
            except ValueError:
                call = None
            if call:
                calls.append(call)
                cleaned = cleaned.replace(m.group(0), "")
    if not calls:
        for m in _CALL.finditer(text):  # tool_name({...})
            name = resolve_name(m.group(1), allowed)
            if name:
                try:
                    args = json.loads(m.group(2))
                except ValueError:
                    continue
                if isinstance(args, dict):
                    calls.append((name, args))
                    cleaned = cleaned.replace(m.group(0), "")
    if not calls:
        for span in _balanced_objects(text):
            try:
                call = _as_call(json.loads(span), allowed)
            except ValueError:
                continue
            if call:
                calls.append(call)
                cleaned = cleaned.replace(span, "")
    return calls, cleaned.strip()


def coerce_args(args: Any, schema: dict) -> dict:
    """Fix the argument mistakes small models make, guided by the tool's schema."""
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except ValueError:
            args = {}
    if not isinstance(args, dict):
        return {}
    props: dict = schema.get("properties", {})
    out = {}
    for key, value in args.items():
        if key not in props:
            alias = difflib.get_close_matches(key, list(props), n=1, cutoff=0.8)
            if not alias or alias[0] in args:
                continue  # an invented argument: drop it rather than fail the whole call
            key = alias[0]
        kind = props[key].get("type")
        try:
            if value is None:
                continue
            if kind == "integer" and not isinstance(value, bool) and not isinstance(value, int):
                value = int(float(str(value).strip()))
            elif kind == "number" and not isinstance(value, (int, float)):
                value = float(str(value).strip())
            elif kind == "boolean" and not isinstance(value, bool):
                value = str(value).strip().lower() in ("true", "yes", "1", "on")
            elif kind == "string" and not isinstance(value, str):
                value = json.dumps(value) if isinstance(value, (dict, list)) else str(value)
            elif kind == "array" and not isinstance(value, list):
                if isinstance(value, str) and value.strip().startswith("["):
                    try:
                        value = json.loads(value)
                    except ValueError:
                        value = [value]
                else:
                    value = [v.strip() for v in value.split(",")] if isinstance(value, str) and "," in value else [value]
            elif kind == "object" and isinstance(value, str):
                value = json.loads(value)
        except (ValueError, TypeError):
            pass  # leave it: the executor's validation will report it to the model
        out[key] = value
    return out


def repair_response(resp: LLMResponse, tools: dict[str, dict]) -> tuple[LLMResponse, list[str]]:
    """`tools` maps tool name -> JSON schema for the tools this agent may use. Returns the repaired
    response and a list of what was fixed (for the task trace)."""
    allowed, fixes = set(tools), []
    calls: list[ToolCall] = []
    for call in resp.tool_calls:
        name = resolve_name(call.name, allowed)
        if name and name != call.name:
            fixes.append(f"tool name '{call.name}' -> '{name}'")
        final = name or call.name
        args = coerce_args(call.args, tools[final]) if final in tools else (call.args if isinstance(call.args, dict) else {})
        if args != call.args:
            fixes.append(f"arguments of {final} normalised")
        calls.append(ToolCall(call.id, final, args))
    text = resp.text
    if not calls and text and ("{" in text):
        found, cleaned = extract_text_calls(text, allowed)
        for i, (name, args) in enumerate(found[:4]):
            calls.append(ToolCall(f"textcall_{abs(hash((name, json.dumps(args, sort_keys=True, default=str)))) % 10**8}_{i}", name, coerce_args(args, tools[name])))
        if found:
            fixes.append(f"{len(found)} tool call(s) recovered from text")
            text = cleaned
    if not fixes:
        return resp, []
    # raw provider blocks no longer match what we will execute, so drop them
    return LLMResponse(text=text, tool_calls=calls, stop_reason=resp.stop_reason, raw=None, provider=resp.provider, model=resp.model, usage=resp.usage), fixes


class StreamFilter:
    """Sits between the model's token stream and the user. As soon as the text starts to look like a tool
    call written as prose (a code fence, a <tool_call> tag, a JSON object), it stops forwarding and holds
    the rest back. If that text turns out to be a real tool call it is discarded; otherwise it is flushed.
    Without this the user would see - and the voice would read aloud - raw JSON."""

    MARKERS = ("```", "<tool_call", "<function_call", "<tool>", '{"name"', '{"tool"', '{"function"', "{\n")

    def __init__(self, forward):
        self.forward, self.sent, self.buffer, self.holding = forward, 0, "", False

    def feed(self, piece: str) -> None:
        self.buffer += piece
        if self.holding:
            return
        cut = min((i for i in (self.buffer.find(m, self.sent) for m in self.MARKERS) if i >= 0), default=-1)
        if cut >= 0:
            self.holding = True
            end = cut
        else:  # keep a short tail back in case a marker is split across two tokens
            end = max(self.sent, len(self.buffer) - 12)
        if end > self.sent:
            self.forward(self.buffer[self.sent:end])
            self.sent = end

    def finish(self, final_text: str, had_tool_calls: bool) -> None:
        """Called with the response's final (possibly repaired) text."""
        if self.holding and had_tool_calls:
            rest = final_text[len(self.buffer[:self.sent].rstrip()):] if final_text.startswith(self.buffer[:self.sent].rstrip()) else ""
        else:
            rest = self.buffer[self.sent:]
        if rest.strip():
            self.forward(rest)
        self.sent, self.buffer, self.holding = 0, "", False


class LoopGuard:
    """Detects a model repeating the same call. First it is told; if it keeps going, the run stops."""

    def __init__(self, warn_at: int = 3, stop_at: int = 5):
        self.counts: dict[str, int] = {}
        self.warn_at, self.stop_at = warn_at, stop_at

    def check(self, call: ToolCall) -> str:
        """'' = fine, 'warn' = return an error result instead of running, 'stop' = abort the run."""
        key = call.name + json.dumps(call.args, sort_keys=True, default=str)
        self.counts[key] = n = self.counts.get(key, 0) + 1
        return "stop" if n >= self.stop_at else "warn" if n >= self.warn_at else ""


# Everyday words people use for a capability that its formal description does not contain. The default
# embedder is lexical, so without these a paraphrase ("is my laptop struggling?") would miss the right tool.
# With a neural embedder (JARVIS_EMBEDDER=openai:<model>) these matter much less.
TOOL_HINTS = {
    "system_stats": "computer laptop pc machine slow fast laggy lagging struggling freezing hot fan health speed busy load usage ram",
    "list_processes": "apps programs running hogging eating using memory cpu task manager",
    "disk_usage": "space storage full drive free room big large folders",
    "search_files": "find locate where look for file document photo spreadsheet pdf lost missing",
    "read_file": "open show contents text inside",
    "organize_folder_plan": "tidy clean up sort messy downloads desktop",
    "screen_read": "see look at screen display window error dialog message showing visible",
    "click_text": "press tap button link menu select choose",
    # not "write": "write me a poem" put this tool first and a 3B model then typed into whatever window had focus
    "type_text": "type typing enter input keyboard fill",
    "system_control": "volume louder quieter mute sound brightness dim lock music pause play next song",
    "clipboard_read": "copied paste clipboard",
    "browser_open": "website web page site url visit go to online",
    "web_search": "search internet google look up online latest news research",
    "calendar_events": "meetings appointments schedule agenda today tomorrow busy free week",
    "calendar_add": "book schedule meeting appointment event remind",
    "email_inbox": "mail inbox messages unread new emails",
    "email_send": "send write reply mail message someone",
    "add_dev_task": "remind reminder todo task remember to later",
    "list_dev_tasks": "todo tasks reminders pending unfinished outstanding",
    "kb_search": "my documents notes papers knowledge according to",
    "repo_overview": "project codebase repository code explain architecture",
    "run_tests": "tests test suite passing failing",
    "generate_image": "draw picture image illustration art photo create",
    "restore_from_trash": "undo undelete recover bring back mistake",
    "daily_briefing": "brief briefing morning status overview what is on",
    "daily_summary": "accomplished done today summary productivity recap",
    "create_workflow": "automate every schedule recurring routine when",
    "remember": "remember note keep in mind my preference",
    "open_application": "open launch start app program application",
    "focus_window": "switch to window app bring front show go to alt tab",
    "wait_for_text": "wait until appears loaded finished ready",
    "save_as_workflow": "do that again every repeat routine save automate",
}


def select_tools(task: str, tools: list, embedder, limit: int, always: set[str]) -> list:
    """Show a small model only the tools most relevant to the request (plus `always`). Fewer tool
    definitions means a shorter prompt and far fewer wrong-tool mistakes. The agent's full tool set
    stays *permitted* - this only narrows what is advertised."""
    if limit <= 0 or len(tools) <= limit:
        return tools
    from .knowledge import tokenize

    docs = [re.sub(r"[_/()=,]", " ", f"{t.name}. {t.name}. {t.description} {TOOL_HINTS.get(t.name, '')}") for t in tools]  # split volume_up -> "volume up"
    vecs = embedder.embed([task] + docs)
    words = set(tokenize(task))
    overlap = np.array([len(words & set(tokenize(d))) for d in docs], dtype=np.float32)
    scores = vecs[1:] @ vecs[0] + 0.12 * np.minimum(overlap, 4)  # shared vocabulary is strong evidence for a tool
    ranked = [tools[i] for i in np.argsort(-scores)]
    keep = [t for t in tools if t.name in always]
    for t in ranked:
        if len(keep) >= limit:
            break
        if t not in keep:
            keep.append(t)
    return [t for t in tools if t in keep]  # preserve declaration order (stable prompt)


def compact_history(messages: list[dict], keep_recent: int = 2, limit: int = 600) -> None:
    """Shrink old tool results in place so a long task does not overflow a small context window."""
    tool_idx = [i for i, m in enumerate(messages) if m["role"] == "tool"]
    for i in tool_idx[:-keep_recent] if keep_recent else tool_idx:
        for r in messages[i]["results"]:
            if len(r["content"]) > limit:
                r["content"] = r["content"][:limit] + f" ... [older result shortened, {len(r['content']) - limit} chars removed]"
