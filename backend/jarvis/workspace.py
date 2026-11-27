"""Which projects have AI agents working on them right now (the floating icon's hover, the `running_projects` tool).

An agent is a running coding-agent process - Claude Code (terminal CLI or a desktop-app session), Codex task runners,
Gemini CLI, Antigravity's agent server, Aider, OpenCode, Cursor agent, Goose, Copilot CLI, Qwen Code. App windows and
helper services (the Claude / ChatGPT / Copilot desktop windows, Codex's app-server, MCP servers) are not agents.

Its project is the deepest real folder that the agent or its child processes (its shell commands) work in, mapped to
`<projects root>/<department>/<project>` - the owner keeps projects in D:\\spideyy\\<department>\\<project>. A Claude CLI
started in "Automation and Agents" whose shell works in "...\\EDIT2\\apps\\web" is counted for EDIT2.
"Working" means its process tree used CPU since the previous scan; otherwise it is open but idle.

Privacy: command lines are read only to recognise an agent and are never kept or returned (they can hold tokens - the
JARVIS window's own command line does). Folders the user blocked (JARVIS_BLOCKED_PATHS) are shown as "private folder".
"""
from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path, PureWindowsPath
from typing import Callable

# (kind, process-name pattern, pattern the exe path + command line must match or None, exclude pattern or None,
#  merge agents of this kind that work in the same folder - Codex runs several sandbox processes per task)
AGENT_KINDS: list[tuple[str, str, str | None, str | None, bool]] = [
    ("Claude Code", r"^claude(\.exe)?$", r"claude-code|(^|[\\/ ])claude(\.exe)?\s*$|\bclaude(\.exe)?\s+(-|--)", r"WindowsApps[\\/]Claude_|--type=", False),
    ("Claude Code", r"^node(\.exe)?$", r"@anthropic-ai[\\/]claude-code", None, False),
    ("Codex", r"^codex(\.exe)?$", r"\b(sandbox|exec|resume)\b|codex(\.exe)?\s*$", r"app-server|--__codex-windows|windows-sandbox|code-mode-host", True),
    ("Codex", r"^node(\.exe)?$", r"@openai[\\/]codex", None, True),
    ("Gemini CLI", r"^node(\.exe)?$", r"@google[\\/]gemini-cli", None, False),
    ("Gemini CLI", r"^gemini(\.exe)?$", None, None, False),
    ("Antigravity", r"^language_server", r"antigravity", None, True),
    ("Aider", r"^(aider|pythonw?)(\.exe)?$", r"\baider\b", None, False),
    ("OpenCode", r"^opencode(\.exe)?$", None, None, False),
    ("Cursor agent", r"^cursor-agent(\.exe)?$", None, None, False),
    ("Goose", r"^goose(\.exe)?$", None, None, False),
    ("Copilot CLI", r"^node(\.exe)?$", r"@github[\\/]copilot", None, False),
    ("Qwen Code", r"^node(\.exe)?$", r"@qwen-code[\\/]", None, False),
]
_KINDS = [(k, re.compile(n, re.I), re.compile(m, re.I) if m else None, re.compile(x, re.I) if x else None, merge)
          for k, n, m, x, merge in AGENT_KINDS]
CANDIDATE_NAMES = re.compile(r"^(claude|codex|node|gemini|language_server.*|aider|pythonw?|opencode|cursor-agent|goose)(\.exe)?$", re.I)
MERGE_BY_FOLDER = {k for k, *_rest, merge in AGENT_KINDS if merge}

# Folders that say nothing about the project: program files, app data, temp, the tools' own config folders.
NOISE = re.compile(r"[\\/](windows|program files( \(x86\))?|programdata|windowsapps|appdata|temp|tmp|\.codex|\.gemini[\\/](config|antigravity[\\/]builtin)"
                   r"|\.claude|\.cache|\.npm|\.vscode|node_modules|site-packages)([\\/]|$)", re.I)
BUSY_CPU_SECONDS = 0.12  # CPU used by the agent's process tree between two scans (~10 s) that counts as working
SHELLS = re.compile(r"^(bash|sh|zsh|pwsh|powershell|cmd|git)(\.exe)?$", re.I)
MAX_CHILD_FOLDERS = 8


def process_table() -> dict[int, tuple[str, int]]:
    """{pid: (exe name, parent pid)} for every process. On Windows one Toolhelp snapshot (milliseconds); psutil's
    process_iter took 48 s for 561 processes on the owner's busy laptop."""
    import os

    if os.name == "nt":
        try:
            return _toolhelp_table()
        except OSError:
            pass
    import psutil

    return {p.info["pid"]: (p.info["name"] or "", p.info["ppid"] or 0) for p in psutil.process_iter(["pid", "name", "ppid"])}


def _toolhelp_table() -> dict[int, tuple[str, int]]:
    import ctypes
    from ctypes import wintypes

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                    ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                    ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", ctypes.c_long), ("dwFlags", wintypes.DWORD),
                    ("szExeFile", ctypes.c_wchar * 260)]

    k32 = ctypes.windll.kernel32
    k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    snap = k32.CreateToolhelp32Snapshot(0x00000002, 0)  # TH32CS_SNAPPROCESS
    if snap in (None, wintypes.HANDLE(-1).value):
        raise OSError("CreateToolhelp32Snapshot failed")
    out: dict[int, tuple[str, int]] = {}
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        ok = k32.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            out[entry.th32ProcessID] = (entry.szExeFile, entry.th32ParentProcessID)
            ok = k32.Process32NextW(snap, ctypes.byref(entry))
    finally:
        k32.CloseHandle(snap)
    if not out:
        raise OSError("empty process snapshot")
    return out


_REPO_ROOTS: dict[str, str] = {}


def repo_root(folder: str) -> str:
    """The nearest enclosing folder with a .git (a repository), or "" - cached."""
    key = folder.lower()
    if key not in _REPO_ROOTS:
        found = ""
        p = Path(folder)
        for cand in [p, *p.parents][:10]:
            try:
                if (cand / ".git").exists():
                    found = str(cand)
                    break
            except OSError:
                break
        _REPO_ROOTS[key] = found
    return _REPO_ROOTS[key]


def classify(name: str, exe: str, cmdline: str) -> str | None:
    """The agent kind for one process, or None."""
    text = f"{exe} {cmdline}"
    for kind, name_re, must, exclude, _merge in _KINDS:
        if not name_re.search(name or ""):
            continue
        if exclude and exclude.search(text):
            continue
        if must and not must.search(text):
            continue
        return kind
    return None


def _norm(path: str) -> str:
    return str(PureWindowsPath(path)) if re.match(r"^[A-Za-z]:", path or "") else (path or "")


def interesting(path: str) -> bool:
    return bool(path) and path != "?" and not NOISE.search(_norm(path) + "\\")


def pick_folder(agent_cwd: str, child_cwds: list[str]) -> str:
    """The folder that tells what the agent works on: the deepest interesting child folder inside the agent's own folder
    (its shell `cd`s into the project), else the agent's folder, else the deepest interesting child folder."""
    own = _norm(agent_cwd)
    kids = [_norm(c) for c in child_cwds if interesting(c)]
    if interesting(own):
        inside = [c for c in kids if c.lower().startswith(own.lower().rstrip("\\") + "\\")]
        return max(inside, key=len) if inside else own
    return max(kids, key=len) if kids else ""


def project_for(folder: str, roots: list[str], home: str = "", repo: Callable[[str], str] = lambda _f: "") -> tuple[str, str, str]:
    """(project name, group, key) for a folder. Under a projects root: <department>/<project>; Codex task folders
    (Documents\\Codex\\<date>\\<task>) by the task; anywhere else by the enclosing git repository (`repo`), else the
    folder itself."""
    if not folder:
        return "No folder", "", "-"
    f = PureWindowsPath(_norm(folder))
    home_p = PureWindowsPath(_norm(home)) if home else None
    for root in sorted((PureWindowsPath(_norm(r)) for r in roots if r), key=lambda p: -len(p.parts)):
        if home_p is not None and root == home_p:
            continue  # the home folder is handled below: its subfolders are not departments
        try:
            rel = f.relative_to(root)
        except ValueError:
            continue
        parts = rel.parts
        if not parts:
            return root.name or str(root), "", str(root).lower()
        if len(parts) == 1:
            return parts[0], "", str(root / parts[0]).lower()
        return parts[1], parts[0], str(root / parts[0] / parts[1]).lower()
    if home_p is not None:
        try:
            parts = f.relative_to(home_p).parts
            if not parts:
                return "Home folder", "", str(home_p).lower()
            if len(parts) >= 4 and parts[0].lower() == "documents" and parts[1].lower() == "codex":
                return parts[3], "Codex task", str(home_p.joinpath(*parts[:4])).lower()
        except ValueError:
            pass
    root = repo(str(f))
    if root:
        r = PureWindowsPath(_norm(root))
        return r.name, r.parent.name, str(r).lower()
    return f.name or str(f), f.parent.name, str(f).lower()


@dataclass
class Agent:
    kind: str
    pid: int
    folder: str
    busy: bool = False


@dataclass
class WorkspaceWatcher:
    """Scans processes every `every` seconds in a background thread; `snapshot()` returns the latest grouping."""
    roots: list[str]
    home: str = ""
    every: float = 10.0
    is_private: Callable[[str], bool] = lambda _p: False
    extra: Callable[[], list[dict]] = lambda: []  # JARVIS's own agents (app.agents.active), added per snapshot
    _cache: dict = field(default_factory=dict)  # (pid, create_time) -> kind or None
    _cpu: dict = field(default_factory=dict)  # pid -> cpu seconds at the previous scan
    _latest: dict = field(default_factory=dict)
    _stop: threading.Event = field(default_factory=threading.Event)
    _thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, daemon=True, name="jarvis-workspace")
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.scan()
            except Exception:  # noqa: BLE001 - the hover simply shows the previous result
                pass
            self._stop.wait(self.every)

    def scan(self) -> list[Agent]:
        import psutil

        procs = process_table()
        children: dict[int, list[int]] = {}
        for pid, (_n, ppid) in procs.items():
            children.setdefault(ppid, []).append(pid)

        kinds: dict[int, str] = {}
        seen = set()
        for pid, (name, _ppid) in procs.items():
            if not CANDIDATE_NAMES.match(name):
                continue
            try:
                proc = psutil.Process(pid)
                key = (pid, proc.create_time())
                seen.add(key)
                if key not in self._cache:
                    try:
                        exe, cmd = proc.exe(), " ".join(proc.cmdline())
                    except psutil.Error:
                        exe, cmd = "", ""
                    self._cache[key] = classify(name, exe, cmd)  # the command line itself is not kept
                if self._cache[key]:
                    kinds[pid] = self._cache[key]
            except psutil.Error:
                continue
        self._cache = {k: v for k, v in self._cache.items() if k in seen}

        def descendants(pid: int, depth: int = 0) -> list[int]:
            if depth > 6:
                return []
            out = []
            for c in children.get(pid, []):
                out.append(c)
                out.extend(descendants(c, depth + 1))
            return out

        def cwd(pid: int) -> str:
            try:
                return psutil.Process(pid).cwd()
            except (psutil.Error, OSError):
                return ""

        def cpu(pid: int) -> float:
            try:
                t = psutil.Process(pid).cpu_times()
                return t.user + t.system
            except psutil.Error:
                return 0.0

        agents: list[Agent] = []
        new_cpu: dict[int, float] = {}
        for pid, kind in kinds.items():
            # a helper process of an agent already counted (Gemini's node wrapper starts a second node) is not another agent
            anc, hops = procs.get(pid, ("", 0))[1], 0
            nested = False
            while anc and hops < 8:
                if kinds.get(anc) == kind:
                    nested = True
                    break
                anc, hops = procs.get(anc, ("", 0))[1], hops + 1
            if nested:
                continue
            tree = [pid] + descendants(pid)
            # the agent's shells say where it works; reading every child's folder (MCP servers...) cost ~0.7 s per scan
            kids = sorted(tree[1:], key=lambda c: 0 if SHELLS.match(procs.get(c, ("", 0))[0]) else 1)
            folder = pick_folder(cwd(pid), [cwd(c) for c in kids[:MAX_CHILD_FOLDERS]])
            used = 0.0
            for t in tree[:60]:
                now = cpu(t)
                new_cpu[t] = now
                if t in self._cpu:
                    used += max(0.0, now - self._cpu[t])
            agents.append(Agent(kind, pid, folder, busy=used >= BUSY_CPU_SECONDS))
        self._cpu = new_cpu
        merged: dict[tuple, Agent] = {}
        out = []
        for a in agents:
            if a.kind in MERGE_BY_FOLDER and a.folder:
                k = (a.kind, a.folder.lower())
                if k in merged:
                    merged[k].busy = merged[k].busy or a.busy
                    continue
                merged[k] = a
            out.append(a)
        self._latest = {"agents": out, "at": time.time()}
        return out

    def snapshot(self) -> dict:
        """{"projects": [{name, group, agents: [{kind, busy}], working, total}], "projects_count", "agents", "working", "at"}"""
        agents = list(self._latest.get("agents", []))
        groups: dict[str, dict] = {}
        for a in agents:
            name, group, key = (("private folder", "", "private") if a.folder and self.is_private(a.folder)
                                else project_for(a.folder, self.roots, self.home, repo=repo_root))
            g = groups.setdefault(key, {"name": name, "group": group, "agents": []})
            g["agents"].append({"kind": a.kind, "busy": a.busy})
        for extra in self.extra():
            g = groups.setdefault("jarvis:" + extra.get("name", "JARVIS"), {"name": extra.get("name", "JARVIS"), "group": "this assistant", "agents": []})
            g["agents"].append({"kind": extra.get("kind", "JARVIS agent"), "busy": True})
        projects = []
        for g in groups.values():
            g["total"] = len(g["agents"])
            g["working"] = sum(x["busy"] for x in g["agents"])
            projects.append(g)
        projects.sort(key=lambda g: (-g["working"], -g["total"], g["name"].lower()))
        return {"projects": projects, "projects_count": len(projects), "agents": sum(g["total"] for g in projects),
                "working": sum(g["working"] for g in projects), "at": self._latest.get("at")}


def describe(snap: dict) -> str:
    """One spoken-friendly paragraph for the running_projects tool."""
    if not snap.get("projects"):
        return "No AI agents are running on your projects right now."
    lines = [f"{snap['agents']} AI agent{'s' if snap['agents'] != 1 else ''} on {snap['projects_count']} project"
             f"{'s' if snap['projects_count'] != 1 else ''}, {snap['working']} working right now."]
    for g in snap["projects"]:
        kinds: dict[str, int] = {}
        for a in g["agents"]:
            kinds[a["kind"]] = kinds.get(a["kind"], 0) + 1
        who = ", ".join(f"{n} {k}" if n > 1 else k for k, n in kinds.items())
        where = f"{g['name']} ({g['group']})" if g["group"] else g["name"]
        lines.append(f"- {where}: {who}" + (f", {g['working']} working" if g["working"] else ", idle"))
    return "\n".join(lines)


def default_roots(allowed_roots: list[Path]) -> tuple[list[str], str]:
    home = str(Path.home())
    return [str(r) for r in allowed_roots], home
