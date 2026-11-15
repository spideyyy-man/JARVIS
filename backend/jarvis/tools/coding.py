"""Coding intelligence: repository inspection, git, code search, tests, dev-task tracking."""
from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

from ..db import new_id
from ..security import Level, SecurityError
from .files import SKIP_DIRS, walk
from .registry import Tool, ToolContext, ToolRegistry

LANG = {".py": "Python", ".ts": "TypeScript", ".tsx": "TypeScript", ".js": "JavaScript", ".jsx": "JavaScript", ".go": "Go", ".rs": "Rust",
        ".java": "Java", ".c": "C", ".cpp": "C++", ".h": "C/C++ header", ".rb": "Ruby", ".php": "PHP", ".cs": "C#", ".swift": "Swift",
        ".kt": "Kotlin", ".sh": "Shell", ".sql": "SQL", ".html": "HTML", ".css": "CSS", ".md": "Markdown"}
# Hardening: never let a repository's own config run programs on our behalf during read-only inspection.
GIT_SAFE = ["-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null", "-c", "diff.external=", "-c", "core.pager=cat"]
BLOCKED_EXECUTABLES = {"sudo", "su", "doas", "pkexec", "shutdown", "reboot", "mkfs", "dd", "systemctl", "chown", "passwd", "useradd", "visudo", "launchctl", "reg"}


def _git(repo: Path, *args: str, timeout: int = 20) -> str:
    out = subprocess.run(["git", "-C", str(repo), *GIT_SAFE, *args], capture_output=True, text=True, timeout=timeout,
                         env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0"})
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip() or f"git {args[0]} failed")
    return out.stdout.strip()


def _has_commits(repo: Path) -> bool:
    try:
        _git(repo, "rev-parse", "--verify", "HEAD")
        return True
    except RuntimeError:
        return False


def _check_command(cmd: list[str]) -> None:
    if not cmd:
        raise ValueError("Empty command")
    if Path(cmd[0]).name.lower() in BLOCKED_EXECUTABLES:
        raise SecurityError(f"'{Path(cmd[0]).name}' is an administrator/system command and is never executed by JARVIS")


def _repo(ctx: ToolContext, path: str) -> Path:
    p = ctx.app.guard.resolve(path or ".")
    if not p.is_dir():
        raise FileNotFoundError(f"Not a directory: {p}")
    return p


def _detect_in(repo: Path) -> list[str] | None:
    if (repo / "pyproject.toml").exists() or (repo / "pytest.ini").exists() or (repo / "tests").is_dir() or list(repo.glob("test_*.py")):
        # the project's own virtualenv if it has one, otherwise the interpreter JARVIS runs on
        venv = next((v for v in (repo / ".venv/bin/python", repo / "venv/bin/python", repo / ".venv/Scripts/python.exe") if v.exists()), None)
        return [str(venv) if venv else sys.executable, "-m", "pytest", "-q", "--no-header", "--maxfail=5"]
    if (repo / "package.json").exists():
        try:
            if "test" in json.loads((repo / "package.json").read_text()).get("scripts", {}):
                return ["npm", "test", "--silent"]
        except ValueError:
            pass
    if (repo / "Cargo.toml").exists():
        return ["cargo", "test"]
    if (repo / "go.mod").exists():
        return ["go", "test", "./..."]
    return None


def detect_test_command(repo: Path) -> tuple[list[str], Path] | None:
    """Returns (command, working directory). Looks one level down too, for monorepos (backend/, frontend/)."""
    for candidate in [repo, *sorted(d for d in repo.iterdir() if d.is_dir() and d.name not in SKIP_DIRS and not d.name.startswith("."))]:
        cmd = _detect_in(candidate)
        if cmd:
            return cmd, candidate
    return None


def _test_label(repo: Path) -> str | None:
    found = detect_test_command(repo)
    if not found:
        return None
    cmd, cwd = found
    shown = " ".join(["python -m pytest" if cmd[1:3] == ["-m", "pytest"] else cmd[0], *cmd[3 if cmd[1:3] == ["-m", "pytest"] else 1:]])
    return shown + (f" (in {cwd.relative_to(repo)}/)" if cwd != repo else "")


def repo_overview(ctx: ToolContext, path: str = "") -> dict:
    repo = _repo(ctx, path)
    out: dict = {"path": str(repo)}
    if (repo / ".git").exists():
        try:
            status = _git(repo, "status", "--porcelain=v1", "--branch").splitlines()
            out["git"] = {
                "branch": status[0].removeprefix("## ") if status else "",
                "uncommitted_changes": len(status) - 1,
                "changed_files": [l[3:] for l in status[1:21]],
                "recent_commits": _git(repo, "log", "-8", "--pretty=format:%h %ad %an: %s", "--date=short").splitlines() if _has_commits(repo) else [],
                "remotes": sorted(set(re.sub(r"//[^@/]+@", "//", l.split()[1]) for l in _git(repo, "remote", "-v").splitlines() if l)),
            }
        except Exception as e:
            out["git"] = {"error": str(e)}
    else:
        out["git"] = None
    loc, files, todos = Counter(), Counter(), 0
    deadline = time.time() + 15
    for p in walk(repo, ctx.app.guard, max_files=8000, deadline=deadline):
        lang = LANG.get(p.suffix.lower())
        if not lang or p.stat().st_size > 1_000_000:
            continue
        try:
            text = p.read_text(errors="ignore")
        except OSError:
            continue
        files[lang] += 1
        loc[lang] += text.count("\n") + 1
        todos += len(re.findall(r"\b(?:TODO|FIXME|HACK)\b", text))
    out["languages"] = [{"language": l, "files": files[l], "lines": n} for l, n in loc.most_common(8)]
    out["todo_markers"] = todos
    out["top_level"] = sorted(p.name + ("/" if p.is_dir() else "") for p in repo.iterdir() if p.name not in SKIP_DIRS and not p.name.startswith("."))[:60]
    manifests = {}
    for name in ("pyproject.toml", "requirements.txt", "package.json", "Cargo.toml", "go.mod", "Dockerfile", "docker-compose.yml", "Makefile"):
        f = repo / name
        if f.is_file():
            manifests[name] = f.read_text(errors="ignore")[:1500]
    out["manifests"] = manifests
    readme = next((f for f in repo.iterdir() if f.name.lower().startswith("readme")), None)
    out["readme_excerpt"] = readme.read_text(errors="ignore")[:1500] if readme else None
    out["test_command"] = _test_label(repo)
    out["entry_points"] = [str(p.relative_to(repo)) for p in walk(repo, ctx.app.guard, max_files=4000)
                           if p.name in ("main.py", "app.py", "__main__.py", "manage.py", "index.ts", "index.js", "main.ts", "main.tsx", "main.go", "main.rs", "server.py")][:15]
    return out


def git_status(ctx: ToolContext, path: str = "") -> dict:
    return {"status": _git(_repo(ctx, path), "status", "--short", "--branch")}


def git_log(ctx: ToolContext, path: str = "", limit: int = 15, since: str = "") -> dict:
    args = ["log", f"-{min(limit, 100)}", "--pretty=format:%h|%ad|%an|%s", "--date=iso-strict"]
    if since:
        args.append(f"--since={since}")
    rows = [dict(zip(("hash", "date", "author", "subject"), l.split("|", 3))) for l in _git(_repo(ctx, path), *args).splitlines()]
    return {"commits": rows}


def git_diff(ctx: ToolContext, path: str = "", staged: bool = False, file: str = "") -> dict:
    args = ["diff", "--no-ext-diff", "--no-textconv", "--stat", "--patch"] + (["--cached"] if staged else [])
    if file:
        args += ["--", file]
    return {"diff": _git(_repo(ctx, path), *args)[:20000]}


def search_code(ctx: ToolContext, pattern: str, path: str = "", file_glob: str = "", limit: int = 40) -> dict:
    repo = _repo(ctx, path)
    try:
        rx = re.compile(pattern, re.I)
    except re.error as e:
        raise ValueError(f"Invalid regex: {e}")
    hits, deadline = [], time.time() + 15
    for p in walk(repo, ctx.app.guard, max_files=20000, deadline=deadline):
        if file_glob and not p.match(file_glob):
            continue
        if p.stat().st_size > 1_000_000:
            continue
        try:
            for i, line in enumerate(p.read_text(errors="ignore").splitlines(), 1):
                if rx.search(line):
                    hits.append({"file": str(p.relative_to(repo)), "line": i, "text": line.strip()[:200]})
                    if len(hits) >= limit:
                        return {"matches": hits, "truncated": True}
        except OSError:
            continue
    return {"matches": hits, "truncated": False}


def _exec(cmd: list[str], cwd: Path, timeout: int) -> dict:
    _check_command(cmd)
    start = time.time()
    try:
        # no shell: no injection via metacharacters. No bytecode cache: an edit followed by a re-run within
        # the same second would otherwise execute the stale .pyc and report the old result.
        out = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, timeout=timeout, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
    except subprocess.TimeoutExpired as e:
        return {"command": " ".join(cmd), "timed_out": True, "output": ((e.stdout or b"").decode(errors="replace") if isinstance(e.stdout, bytes) else (e.stdout or ""))[-6000:]}
    except FileNotFoundError:
        raise RuntimeError(f"Executable not found: {cmd[0]}")
    text = (out.stdout + ("\n" + out.stderr if out.stderr else ""))
    return {"command": " ".join(cmd), "exit_code": out.returncode, "duration_s": round(time.time() - start, 2), "output": text[-8000:]}


def run_tests(ctx: ToolContext, path: str = "") -> dict:
    repo = _repo(ctx, path)
    found = detect_test_command(repo)
    if not found:
        raise RuntimeError("Could not detect a test command for this project")
    res = _exec(found[0], found[1], 300)
    res["passed"] = res.get("exit_code") == 0
    res["failures"] = re.findall(r"^(?:FAILED|ERROR) (\S+)(?: - (.*))?$", res["output"], re.M)[:20]
    return res


def run_command(ctx: ToolContext, command: str, path: str = "", timeout_s: int = 120) -> dict:
    return _exec(shlex.split(command), _repo(ctx, path), min(max(timeout_s, 1), 600))


def git_commit(ctx: ToolContext, message: str, path: str = "", add_all: bool = False) -> dict:
    repo = _repo(ctx, path)
    if add_all:
        _git(repo, "add", "-A")
    _git(repo, "commit", "-m", message)
    return {"committed": _git(repo, "log", "-1", "--pretty=format:%h %s")}


# -------------------------------------------------------------- dev task tracking
def add_dev_task(ctx: ToolContext, title: str, project: str = "", notes: str = "", due_in_days: float = 0) -> dict:
    now = time.time()
    row = {"id": new_id("dt_"), "project": project, "title": title, "status": "open", "notes": notes,
           "due": now + due_in_days * 86400 if due_in_days else None, "created": now, "updated": now}
    ctx.app.db.insert("dev_tasks", row)
    return row


def list_dev_tasks(ctx: ToolContext, status: str = "", project: str = "") -> dict:
    sql, params = "SELECT * FROM dev_tasks WHERE 1=1", []
    if status:
        sql, params = sql + " AND status=?", params + [status]
    if project:
        sql, params = sql + " AND project=?", params + [project]
    rows = ctx.app.db.query(sql + " ORDER BY status='done', COALESCE(due, 9e18), created", params)
    now = time.time()
    for r in rows:
        r["overdue"] = bool(r["due"] and r["due"] < now and r["status"] != "done")
    return {"tasks": rows, "open": sum(r["status"] != "done" for r in rows)}


def update_dev_task(ctx: ToolContext, id: str, status: str = "", notes: str = "") -> dict:
    if not ctx.app.db.one("SELECT id FROM dev_tasks WHERE id=?", (id,)):
        raise KeyError(f"No dev task {id}")
    fields = {"updated": time.time(), **({"status": status} if status else {}), **({"notes": notes} if notes else {})}
    ctx.app.db.update("dev_tasks", id, **fields)
    return ctx.app.db.one("SELECT * FROM dev_tasks WHERE id=?", (id,))


def register(reg: ToolRegistry, app) -> None:
    S, I, B = {"type": "string"}, {"type": "integer"}, {"type": "boolean"}
    P = {"type": "string", "description": "Project/repository directory"}
    reg.register(Tool("repo_overview", "Inspect a software project: git state, recent commits, languages, layout, manifests, README, test command, entry points. Start here for any codebase question.", {"path": P}, repo_overview, timeout=45))
    reg.register(Tool("git_status", "git status of a repository.", {"path": P}, git_status))
    reg.register(Tool("git_log", "Recent commits; `since` accepts git date syntax like '1 day ago'.", {"path": P, "limit": I, "since": S}, git_log))
    reg.register(Tool("git_diff", "Uncommitted (or staged) changes as a patch.", {"path": P, "staged": B, "file": S}, git_diff))
    reg.register(Tool("search_code", "Regex search across a project's source files.", {"pattern": S, "path": P, "file_glob": S, "limit": I}, search_code, required=["pattern"]))
    reg.register(Tool("list_dev_tasks", "List tracked development tasks.", {"status": {"type": "string", "enum": ["open", "in_progress", "done"]}, "project": S}, list_dev_tasks))
    reg.register(Tool("add_dev_task", "Track a development task/reminder in JARVIS's own task list.", {"title": S, "project": S, "notes": S, "due_in_days": {"type": "number"}}, add_dev_task, required=["title"]))
    reg.register(Tool("update_dev_task", "Update a tracked task's status or notes.", {"id": S, "status": {"type": "string", "enum": ["open", "in_progress", "done"]}, "notes": S}, update_dev_task, required=["id"]))

    C = Level.CONFIRM
    reg.register(Tool("run_tests", "Run the project's detected test suite and report failures.", {"path": P}, run_tests, level=C, trust_eligible=True, timeout=320,
                      preview=lambda a: f"Run tests in {app.guard.resolve(a.get('path') or '.')}: {_test_label(app.guard.resolve(a.get('path') or '.')) or '<no test command detected>'}"))
    reg.register(Tool("run_command", "Run a single program (no shell, no pipes) inside a project directory. Administrator commands are refused.", {"command": S, "path": P, "timeout_s": I}, run_command, required=["command"], level=C, timeout=620,
                      preview=lambda a: (_check_command(shlex.split(a["command"])), f"Run command in {app.guard.resolve(a.get('path') or '.')}:\n$ {a['command']}")[1]))
    reg.register(Tool("git_commit", "Create a git commit.", {"message": S, "path": P, "add_all": B}, git_commit, required=["message"], level=C,
                      preview=lambda a: f"git commit in {app.guard.resolve(a.get('path') or '.')}{' (after git add -A)' if a.get('add_all') else ''}: \"{a['message']}\""))
