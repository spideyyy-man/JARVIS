"""Running projects (workspace.py): which projects have AI agents working on them - for the icon's hover and the
running_projects tool. Process data is faked here; a live scan on the owner's laptop found Claude Code in EDIT2, Jarvis
and linkedin, Antigravity in forge-ai and Gemini CLI in the home folder (2026-11-27)."""
from pathlib import Path

import pytest

from conftest import run
from jarvis.llm.offline import Env, match_rules
from jarvis.workspace import Agent, WorkspaceWatcher, classify, describe, pick_folder, project_for

DESKTOP_CLAUDE = r"C:\Program Files\WindowsApps\Claude_6.28899.6.0_x64__pzs8sxrjxfjjc\app\Claude.exe"
SESSION_CLAUDE = r"C:\Users\GOD\AppData\Roaming\Claude\claude-code\2.1.330\9424831ad51c\claude.exe"


@pytest.mark.parametrize("name,exe,cmd,kind", [
    ("claude.exe", SESSION_CLAUDE, f"{SESSION_CLAUDE} --output-format stream-json --verbose", "Claude Code"),  # a desktop-app session
    ("claude.exe", r"C:\Users\GOD\.local\bin\claude.exe", "claude", "Claude Code"),  # the terminal CLI
    ("claude.exe", DESKTOP_CLAUDE, f"{DESKTOP_CLAUDE} --type=renderer --user-data-dir=x", None),  # the app window
    ("claude.exe", DESKTOP_CLAUDE, DESKTOP_CLAUDE, None),
    ("codex.exe", r"C:\Program Files\WindowsApps\OpenAI.Codex\app\resources\codex.exe", "codex.exe sandbox -c x", "Codex"),
    ("codex.exe", r"C:\Program Files\WindowsApps\OpenAI.Codex\app\resources\codex.exe", "codex.exe -c a app-server --analytics", None),
    ("node.exe", r"C:\node\node.exe", r"node C:\x\node_modules\@google\gemini-cli\dist\index.js", "Gemini CLI"),
    ("node.exe", r"C:\node\node.exe", r"node C:\x\node_modules\@anthropic-ai\claude-code\cli.js", "Claude Code"),
    ("node.exe", r"C:\node\node.exe", r"node C:\proj\node_modules\.bin\vite.js --port 5173", None),  # a dev server is not an AI
    ("language_server.exe", r"C:\Users\GOD\AppData\Local\Programs\Antigravity\resources\language_server.exe", "", "Antigravity"),
    ("python.exe", r"C:\Python313\python.exe", "python -m aider --model x", "Aider"),
    ("python.exe", r"C:\Python313\python.exe", "python -m jarvis.desktop_app", None),
])
def test_agents_are_recognised_and_app_windows_are_not(name, exe, cmd, kind):
    assert classify(name, exe, cmd) == kind


def test_the_folder_comes_from_where_the_agent_works():
    # a Claude CLI started in the department folder whose shell works inside EDIT2 is counted for EDIT2
    assert pick_folder(r"D:\spideyy\Automation and Agents", [r"D:\spideyy\Automation and Agents\EDIT2\apps\web\src", r"C:\WINDOWS\system32"]) \
        == r"D:\spideyy\Automation and Agents\EDIT2\apps\web\src"
    # an agent server whose own folder says nothing: its child's project folder
    assert pick_folder(r"C:\Users\GOD\AppData\Local\Programs\Antigravity", [r"D:\code\forge-ai\services\local-agent", r"C:\Users\GOD\.gemini\config\plugins\x"]) \
        == r"D:\code\forge-ai\services\local-agent"
    assert pick_folder(r"C:\WINDOWS\system32", []) == ""


def test_projects_are_named_by_department_repository_or_task():
    roots, home = [r"C:\Users\GOD", r"D:\spideyy"], r"C:\Users\GOD"
    assert project_for(r"D:\spideyy\Automation and Agents\EDIT2\apps\web\src", roots, home)[:2] == ("EDIT2", "Automation and Agents")
    assert project_for(r"D:\spideyy\Marketing Content\linkedin", roots, home)[:2] == ("linkedin", "Marketing Content")
    assert project_for(r"C:\Users\GOD", roots, home)[:2] == ("Home folder", "")
    assert project_for(r"C:\Users\GOD\Documents\Codex\2026-11-25\fix-login-page", roots, home)[:2] == ("fix-login-page", "Codex task")
    repo = lambda f: r"D:\HAAROON_FILES\VIBE-CODING\forge-ai" if "forge-ai" in f else ""  # noqa: E731
    assert project_for(r"D:\HAAROON_FILES\VIBE-CODING\forge-ai\services\local-agent", roots, home, repo=repo)[:2] == ("forge-ai", "VIBE-CODING")
    assert project_for("", roots, home)[0] == "No folder"


def _watcher(agents, private=lambda p: False, extra=lambda: []):
    w = WorkspaceWatcher(roots=[r"C:\Users\GOD", r"D:\spideyy"], home=r"C:\Users\GOD", is_private=private, extra=extra)
    w._latest = {"agents": agents, "at": 1.0}
    return w


def test_the_snapshot_counts_projects_and_working_ais():
    snap = _watcher([Agent("Claude Code", 1, r"D:\spideyy\Automation and Agents\Jarvis\backend", True),
                     Agent("Codex", 2, r"D:\spideyy\Automation and Agents\Jarvis", False),
                     Agent("Claude Code", 3, r"D:\spideyy\Automation and Agents\EDIT2", True),
                     Agent("Gemini CLI", 4, r"C:\Users\GOD", False)]).snapshot()
    assert (snap["projects_count"], snap["agents"], snap["working"]) == (3, 4, 2)
    jarvis = next(g for g in snap["projects"] if g["name"] == "Jarvis")
    assert jarvis["total"] == 2 and jarvis["working"] == 1 and {a["kind"] for a in jarvis["agents"]} == {"Claude Code", "Codex"}
    assert snap["projects"][-1]["name"] == "Home folder"  # idle projects sort last
    text = describe(snap)
    assert text.startswith("4 AI agents on 3 projects, 2 working right now.") and "- Home folder: Gemini CLI, idle" in text


def test_blocked_folders_are_never_named_and_jarvis_counts_its_own_agents():
    snap = _watcher([Agent("Claude Code", 1, r"D:\spideyy\.account\secret-project", True)], private=lambda p: ".account" in p,
                    extra=lambda: [{"name": "JARVIS", "kind": "JARVIS assistant"}]).snapshot()
    names = [g["name"] for g in snap["projects"]]
    assert "private folder" in names and not any("secret" in n or ".account" in n for n in names)
    assert "JARVIS" in names and snap["agents"] == 2
    assert describe(_watcher([]).snapshot()) == "No AI agents are running on your projects right now."


@pytest.mark.parametrize("phrase", ["what projects are running", "which projects are my agents working on", "how many AIs are working",
                                    "what are my agents doing", "is claude working", "show running projects"])
def test_asking_about_projects_routes_to_the_tool(phrase):
    hit = match_rules(phrase, Env(Path("/home/u"), lambda: "", lambda: []))
    assert hit and hit[1][0] == "running_projects", phrase


@pytest.mark.parametrize("phrase,tool", [("what's running", "list_processes"), ("run the tests", "run_tests"), ("open notepad", "open_application")])
def test_other_requests_are_not_taken(phrase, tool):
    hit = match_rules(phrase, Env(Path("/home/u"), lambda: "", lambda: []))
    assert hit and hit[1][0] == tool


def test_the_tool_answers_instantly_from_the_scan(jarvis):
    jarvis.workspace._latest = {"agents": [Agent("Claude Code", 1, r"D:\spideyy\Automation and Agents\EDIT2", True)], "at": 1.0}
    assert "running_projects" not in jarvis.NOT_FAST  # with a model configured it still answers on the fast path
    r = run(jarvis.chat("what projects are running"))
    assert r["provider"] == "offline" and "EDIT2" in r["text"] and "1 working" in r["text"]


def test_the_icon_gets_the_projects(jarvis):
    from fastapi.testclient import TestClient

    from jarvis.api import create_app

    jarvis.workspace._latest = {"agents": [Agent("Codex", 9, r"D:\spideyy\Automation and Agents\Jarvis", False)], "at": 1.0}
    h = {"Authorization": f"Bearer {jarvis.settings.api_token()}"}
    with TestClient(create_app(jarvis)) as c:
        assert jarvis.workspace._thread is None  # only the desktop app scans the real machine (settings.watch_agents)
        proj = c.get("/api/overlay", headers=h).json()["projects"]
        assert proj["projects"][0]["name"] == "Jarvis" and proj["working"] == 0
        assert c.get("/api/projects", headers=h).json()["agents"] == 1


def test_the_hover_panel_lists_projects_with_a_dot_per_ai():
    tk = pytest.importorskip("tkinter")
    from jarvis.overlay import PROJECT_ROWS, Overlay

    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("no display")
    root.withdraw()
    o = Overlay.__new__(Overlay)  # only the panel - no icon window, no polling
    o.root, o._texts, o.pinned, o.history_open, o.data = root, {}, False, False, None
    try:
        o._build_panel()
        snap = _watcher([Agent("Claude Code", 1, r"D:\spideyy\Automation and Agents\Jarvis", True),
                         Agent("Claude Code", 2, r"D:\spideyy\Automation and Agents\Jarvis", False),
                         Agent("Codex", 3, r"D:\spideyy\Automation and Agents\EDIT2", False)]
                        + [Agent("Gemini CLI", 10 + i, rf"D:\spideyy\Research\p{i}", False) for i in range(4)]).snapshot()
        o._render_projects(snap)
        assert o.proj_count.cget("text") == "1 of 7 working"
        row, name, kinds, pips = o.proj_rows[0]
        assert name.cget("text") == "Jarvis" and kinds.cget("text") == "2 Claude Code"
        assert len(pips.find_all()) == 2  # one dot per AI
        assert o.proj_more.cget("text") == f"{snap['projects_count'] - PROJECT_ROWS} more projects"
        o._render_projects(_watcher([]).snapshot())
        assert o.proj_count.cget("text") == "No AI agents running" and not o.proj_rows[0][0].winfo_manager()
    finally:
        root.destroy()


def test_the_new_icon_is_solid_and_its_bezel_carries_the_state():
    """The puck is opaque everywhere inside its edge - ticks drawn straight onto it used to punch see-through holes
    that showed a light desktop through them - and only the lit ticks change with the state."""
    import math

    from jarvis.orb import COLORS, ORB_R, SIZE, TICK_IN, TICK_OUT, TICKS, OrbState, render, tick_levels

    for state in ("waiting", "thinking", "speaking", "offline"):
        img = render(OrbState(state=state, t=0.8, color=COLORS[state], timer=0.5, approval=state == "thinking"))
        c = SIZE / 2
        for i in range(TICKS):  # every tick position, lit or not, is solid
            a = -math.pi / 2 + 2 * math.pi * i / TICKS
            r = (TICK_IN + TICK_OUT) / 2
            assert img.getpixel((int(c + math.cos(a) * r), int(c + math.sin(a) * r)))[3] == 255, (state, i)
        assert img.getpixel((int(c), int(c - ORB_R + 3)))[3] == 255
    wait, _ = tick_levels("waiting", 1.0)
    assert sum(v > 0.6 for v in wait) <= 4 and min(wait) > 0.1  # at rest: one travelling tick and a short tail
    off, _ = tick_levels("offline", 1.0)
    assert max(off) < 0.1  # offline: no light at all
    speak, extra = tick_levels("speaking", 1.0)
    assert max(extra) > 0.5  # speaking: the ticks reach inward like an equaliser
