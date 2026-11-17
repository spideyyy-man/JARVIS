"""Computer intelligence: performance, processes, disks, applications, dev environment."""
from __future__ import annotations

import os
import platform
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

import psutil

from ..security import Level, SecurityError
from .registry import Tool, ToolContext, ToolRegistry

GB = 1024 ** 3


def snapshot(interval: float = 0.0) -> dict:
    """Cheap metrics sample used by both the tool and the live dashboard."""
    vm, sw = psutil.virtual_memory(), psutil.swap_memory()
    disk = psutil.disk_usage(str(Path.home()))
    net = psutil.net_io_counters()
    try:
        load = [round(x, 2) for x in os.getloadavg()]
    except (OSError, AttributeError):
        load = []
    return {
        "ts": time.time(),
        "cpu": {"percent": psutil.cpu_percent(interval=interval or None), "cores": psutil.cpu_count(), "load_avg": load},
        "memory": {"percent": vm.percent, "used_gb": round(vm.used / GB, 2), "total_gb": round(vm.total / GB, 2), "available_gb": round(vm.available / GB, 2)},
        "swap": {"percent": sw.percent, "used_gb": round(sw.used / GB, 2)},
        "disk": {"percent": disk.percent, "free_gb": round(disk.free / GB, 1), "total_gb": round(disk.total / GB, 1)},
        "net": {"sent_mb": round(net.bytes_sent / 1e6, 1), "recv_mb": round(net.bytes_recv / 1e6, 1)} if net else {},
    }


def _top_processes(sort_by: str = "cpu", limit: int = 8, name_filter: str = "") -> list[dict]:
    """Two passes: the sort key for every process, then the costly fields (status, user, memory) only for
    the rows shown. On Windows each of those fields takes 2-3 s across a few hundred processes, which made
    one system_stats call take 15-20 s when they were read for everything."""
    nf = name_filter.lower()
    procs: list[tuple[psutil.Process, dict]] = []
    for p in psutil.process_iter(["pid", "name"]):
        try:
            info = dict(p.info)
            if info["pid"] == 0:  # Windows "System Idle Process": its CPU figure is idle time, not load
                continue
            if nf and nf not in (info["name"] or "").lower():
                if nf not in " ".join(p.as_dict(["cmdline"], ad_value=None)["cmdline"] or []).lower():
                    continue
            if sort_by == "cpu":
                p.cpu_percent(None)  # first sample; the second one below gives the percentage
            else:
                info["memory_percent"] = p.as_dict(["memory_percent"], ad_value=None)["memory_percent"]
            procs.append((p, info))
        except psutil.Error:
            continue
    if sort_by == "cpu":  # cpu_percent needs two samples to be meaningful
        time.sleep(0.25)
        for p, info in procs:
            try:
                info["cpu_percent"] = p.cpu_percent(None)
            except psutil.Error:
                info["cpu_percent"] = None
    key = "cpu_percent" if sort_by == "cpu" else "memory_percent"
    procs.sort(key=lambda pi: pi[1].get(key) or 0, reverse=True)
    rows = []
    for p, info in procs[:limit]:
        try:
            more = p.as_dict(["username", "status", "cmdline"] + ([] if "memory_percent" in info else ["memory_percent"]), ad_value=None)
        except psutil.Error:
            more = {"username": None, "status": "exited", "cmdline": None}
        info.update(more)
        rows.append({"pid": info["pid"], "name": info["name"], "user": info["username"], "cpu_percent": round(info.get("cpu_percent") or 0, 1),
                     "memory_percent": round(info.get("memory_percent") or 0, 2), "status": info["status"],
                     "command": " ".join(info["cmdline"] or [])[:160]})
    return rows


def system_stats(ctx: ToolContext) -> dict:
    snap = snapshot(interval=0.4)
    issues = []
    if snap["cpu"]["percent"] > 85:
        issues.append(f"CPU is heavily loaded ({snap['cpu']['percent']}%).")
    if snap["memory"]["percent"] > 85:
        issues.append(f"Memory pressure is high ({snap['memory']['percent']}% used).")
    if snap["swap"]["percent"] > 50:
        issues.append("Significant swap usage - the machine is short on RAM.")
    if snap["disk"]["percent"] > 90:
        issues.append(f"Disk is nearly full ({snap['disk']['free_gb']} GB free).")
    snap.update({
        "platform": f"{platform.system()} {platform.release()} ({platform.machine()})",
        "uptime_hours": round((time.time() - psutil.boot_time()) / 3600, 1),
        "top_cpu": _top_processes("cpu", 5),
        "top_memory": _top_processes("memory", 5),
        "assessment": issues or ["No performance problems detected."],
        "history": ctx.app.monitor.summary() if ctx.app.monitor else {},
    })
    return snap


def list_processes(ctx: ToolContext, sort_by: str = "cpu", limit: int = 15, name_filter: str = "") -> dict:
    return {"total": len(psutil.pids()), "processes": _top_processes(sort_by, min(limit, 100), name_filter)}


def disk_usage(ctx: ToolContext, path: str = "") -> dict:
    parts = []
    for p in psutil.disk_partitions(all=False):
        try:
            u = psutil.disk_usage(p.mountpoint)
            parts.append({"mount": p.mountpoint, "fs": p.fstype, "total_gb": round(u.total / GB, 1), "free_gb": round(u.free / GB, 1), "percent": u.percent})
        except OSError:
            continue
    out: dict = {"partitions": parts}
    if path:
        root = ctx.app.guard.resolve(path)
        sizes, deadline = [], time.time() + 15
        for child in root.iterdir():
            total = 0
            try:
                if child.is_symlink():
                    continue
                if child.is_file():
                    total = child.stat().st_size
                else:
                    for dirpath, _dirs, files in os.walk(child):
                        for f in files:
                            try:
                                total += os.lstat(os.path.join(dirpath, f)).st_size
                            except OSError:
                                pass
                        if time.time() > deadline:
                            break
            except OSError:
                continue
            sizes.append({"name": child.name, "is_dir": child.is_dir(), "size_mb": round(total / 1e6, 2)})
        sizes.sort(key=lambda s: s["size_mb"], reverse=True)
        out["largest_in"] = {"path": str(root), "entries": sizes[:20], "truncated": time.time() > deadline}
    return out


DEV_TOOLS = ["python3", "pip", "node", "npm", "pnpm", "yarn", "git", "docker", "go", "cargo", "rustc", "java", "gcc", "make", "code", "cursor", "nvim", "vim", "uv", "poetry", "pytest"]


def _desktop_apps() -> list[dict]:
    apps: list[dict] = []
    system = platform.system()
    if system == "Linux":
        for base in ["/usr/share/applications", str(Path.home() / ".local/share/applications"), "/var/lib/flatpak/exports/share/applications"]:
            for entry in Path(base).glob("*.desktop") if Path(base).is_dir() else []:
                name = exec_ = ""
                try:
                    for line in entry.read_text(errors="ignore").splitlines():
                        if line.startswith("Name=") and not name:
                            name = line[5:]
                        elif line.startswith("Exec=") and not exec_:
                            exec_ = line[5:]
                except OSError:
                    continue
                if name:
                    apps.append({"name": name, "exec": exec_, "source": str(entry)})
    elif system == "Darwin":
        for base in ["/Applications", str(Path.home() / "Applications")]:
            for entry in Path(base).glob("*.app") if Path(base).is_dir() else []:
                apps.append({"name": entry.stem, "exec": str(entry), "source": str(entry)})
    elif system == "Windows":
        for base in [os.environ.get("ProgramData", "") + r"\Microsoft\Windows\Start Menu\Programs", os.environ.get("APPDATA", "") + r"\Microsoft\Windows\Start Menu\Programs"]:
            for entry in Path(base).rglob("*.lnk") if Path(base).is_dir() else []:
                apps.append({"name": entry.stem, "exec": str(entry), "source": str(entry)})
    return apps


def list_installed_apps(ctx: ToolContext, name_filter: str = "") -> dict:
    apps = [a for a in _desktop_apps() if name_filter.lower() in a["name"].lower()]
    cli = [{"name": t, "path": shutil.which(t)} for t in DEV_TOOLS if shutil.which(t) and name_filter.lower() in t]
    return {"desktop_applications": sorted(apps, key=lambda a: a["name"].lower())[:200], "developer_tools_on_path": cli}


def _version(cmd: list[str]) -> str:
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        lines = [l for l in (out.stdout or out.stderr).strip().splitlines() if not l.startswith("Picked up")]
        return lines[0][:80]
    except Exception:
        return ""


def dev_environment(ctx: ToolContext, projects_root: str = "") -> dict:
    tools = {}
    for name, cmd in {"python": [sys.executable, "--version"], "node": ["node", "--version"], "npm": ["npm", "--version"], "git": ["git", "--version"],
                      "docker": ["docker", "--version"], "go": ["go", "version"], "rust": ["rustc", "--version"], "java": ["java", "-version"]}.items():
        if shutil.which(cmd[0]) or cmd[0] == sys.executable:
            tools[name] = _version(cmd)
    root = ctx.app.guard.resolve(projects_root or str(ctx.app.settings.allowed_roots[0]))
    markers = {"pyproject.toml": "python", "requirements.txt": "python", "package.json": "node", "Cargo.toml": "rust", "go.mod": "go", "pom.xml": "java"}
    projects = []
    for dirpath, dirs, files in os.walk(root):
        depth = len(Path(dirpath).relative_to(root).parts)
        dirs[:] = [d for d in dirs if not d.startswith(".") and d not in ("node_modules", "venv", "__pycache__", "dist", "build")] if depth < 3 else []
        kinds = sorted({markers[f] for f in files if f in markers})
        is_git = (Path(dirpath) / ".git").exists()
        if kinds or is_git:
            projects.append({"path": dirpath, "stack": kinds, "git": is_git})
            dirs[:] = []
        if len(projects) >= 40:
            break
    return {"toolchains": tools, "editors": [e for e in ("code", "cursor", "nvim", "vim", "idea", "pycharm") if shutil.which(e)],
            "shell": os.environ.get("SHELL", ""), "projects_root": str(root), "projects": projects}


def _resolve_app(name: str) -> list[str]:
    for app in _desktop_apps():
        if app["name"].lower() == name.lower() or name.lower() in app["name"].lower():
            if platform.system() == "Darwin":
                return ["open", app["exec"]]
            if platform.system() == "Windows":
                return ["cmd", "/c", "start", "", app["exec"]]
            # strip desktop-entry field codes (%U, %f...)
            return [tok for tok in shlex.split(app["exec"]) if not tok.startswith("%")]
    exe = shutil.which(name)
    if exe:
        return [exe]
    raise SecurityError(f"No installed application matches '{name}'")


def open_application(ctx: ToolContext, name: str, target: str = "") -> dict:
    cmd = _resolve_app(name)
    if target:
        cmd.append(str(ctx.app.guard.resolve(target)))
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    return {"launched": cmd, "pid": proc.pid}


def register(reg: ToolRegistry) -> None:
    reg.register(Tool("system_stats", "Current CPU, memory, swap, disk, load, uptime, the heaviest processes and a health assessment. Use for any question about computer performance.", {}, system_stats))
    reg.register(Tool("list_processes", "List running processes sorted by cpu or memory.", {
        "sort_by": {"type": "string", "enum": ["cpu", "memory"]}, "limit": {"type": "integer", "minimum": 1, "maximum": 100},
        "name_filter": {"type": "string", "description": "Only processes whose name/command contains this"}}, list_processes))
    reg.register(Tool("disk_usage", "Disk partitions and free space. With `path`, also the largest entries inside that folder.", {"path": {"type": "string"}}, disk_usage, timeout=40))
    reg.register(Tool("list_installed_apps", "Installed desktop applications and developer tools on PATH.", {"name_filter": {"type": "string"}}, list_installed_apps))
    reg.register(Tool("dev_environment", "Detect installed toolchains/editors and find software projects under a folder.", {"projects_root": {"type": "string"}}, dev_environment))
    reg.register(Tool("open_application", "Launch an installed application, optionally with a file/folder to open.", {
        "name": {"type": "string"}, "target": {"type": "string", "description": "File or folder to open with it"}}, open_application, required=["name"],
        level=Level.CONFIRM, trust_eligible=True,
        preview=lambda a: f"Launch application: {' '.join(_resolve_app(a['name']))} {a.get('target', '')}".strip()))
