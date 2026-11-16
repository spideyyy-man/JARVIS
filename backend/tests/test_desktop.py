"""Desktop control, browser automation, capabilities and proactive behaviour.
The GUI tests need a display and a Chromium binary; run the suite with `xvfb-run -a pytest` on a headless box."""
import glob
import http.server
import os
import threading
import time

import pytest
from fastapi.testclient import TestClient

from conftest import run
from jarvis.api import create_app
from jarvis.security import SecurityError
from jarvis.tools import desktop
from jarvis.tools.registry import ToolContext

CHROME = next(iter(glob.glob("/opt/pw-browsers/chromium-*/chrome-linux64/chrome")), os.environ.get("JARVIS_BROWSER_PATH", ""))
needs_gui = pytest.mark.skipif(not (os.environ.get("DISPLAY") and CHROME), reason="needs a display (xvfb-run) and a Chromium binary")
needs_chrome = pytest.mark.skipif(not CHROME, reason="needs a Chromium binary")


def enable(jarvis, *caps):
    jarvis.db.set_setting("capabilities", {c: True for c in caps})


def test_capabilities_are_off_by_default_and_block_before_queueing(jarvis, ctx):
    assert jarvis.capabilities() == {"screen_access": False, "desktop_control": False, "browser_control": False}
    res = run(jarvis.executor.call("screen_read", {}, ctx))
    assert not res.ok and "switched off" in res.error
    for name, args in [("click_text", {"text": "OK"}), ("type_text", {"text": "x"}), ("press_keys", {"keys": "enter"}), ("mouse_click", {"x": 1, "y": 1}),
                       ("browser_click", {"target": "Buy"}), ("browser_fill", {"text": "x", "field": "q"})]:
        res = run(jarvis.executor.call(name, args, ctx))
        assert not res.ok and not res.pending and "switched off" in res.error, name
    assert jarvis.db.one("SELECT COUNT(*) AS n FROM approvals")["n"] == 0


def test_input_and_browser_actions_can_never_be_pre_trusted(jarvis):
    for name in ("click_text", "mouse_click", "type_text", "press_keys", "browser_click", "browser_fill"):
        with pytest.raises(SecurityError):
            jarvis.executor.set_trusted_tools([name])
    assert not any("capabilit" in t.name for t in jarvis.tools.all())  # no tool can flip a capability


def test_only_the_user_can_switch_capabilities(jarvis):
    with TestClient(create_app(jarvis)) as c:
        h = {"Authorization": f"Bearer {jarvis.settings.api_token()}"}
        assert c.put("/api/settings", json={"capabilities": {"root_access": True}}, headers=h).status_code == 400
        body = c.put("/api/settings", json={"capabilities": {"screen_access": True}}, headers=h).json()
        assert body["capabilities"]["screen_access"]["enabled"] and not body["capabilities"]["desktop_control"]["enabled"]
        assert c.put("/api/settings", json={"proactive": {"briefing_time": "25:99"}}, headers=h).status_code == 400
        assert c.put("/api/settings", json={"proactive": {"briefing_time": "08:30"}}, headers=h).json()["proactive"]["briefing_time"] == "08:30"
        assert jarvis.audit.recent(tool="settings.capabilities")[0]["decision"] == "user-action"


def test_find_on_screen_prefers_exact_match_and_aims_at_the_label():
    lines = [{"text": "Save As...", "x": 10, "y": 10, "w": 100, "h": 20}, {"text": "Save", "x": 200, "y": 10, "w": 40, "h": 20},
             {"text": "File  Edit  View  Help", "x": 0, "y": 40, "w": 220, "h": 20}]
    hit = desktop.find_on_screen(lines, "save")
    assert (hit["text"], hit["cx"], hit["cy"], hit["matches"]) == ("Save", 220, 20, 2)
    assert desktop.find_on_screen(lines, "save", occurrence=2)["text"] == "Save As..."
    assert 120 < desktop.find_on_screen(lines, "View")["cx"] < 160  # inside the word, not the middle of the menu bar
    with pytest.raises(RuntimeError):
        desktop.find_on_screen(lines, "Delete")
    with pytest.raises(RuntimeError):
        desktop.find_on_screen(lines, "save", occurrence=3)


def test_windows_system_control_and_clipboard_plans(monkeypatch):
    monkeypatch.setattr(desktop, "SYSTEM", "Windows")
    assert desktop.system_control_plan("volume_up", 7) == [("key", 0xAF, 7)]
    assert desktop.system_control_plan("volume_mute") == [("key", 0xAD, 1)]
    assert "WmiSetBrightness(1,40)" in desktop.system_control_plan("set_brightness", 40)[0][1][-1]
    assert desktop.system_control_plan("lock_screen")[0][1] == ["rundll32.exe", "user32.dll,LockWorkStation"]
    read, write = desktop.clipboard_commands()
    assert read[-1] == "Get-Clipboard -Raw" and write[-1] == "$input | Set-Clipboard"
    with pytest.raises(ValueError):
        desktop.system_control_plan("format_disk")
    assert [str(k) for k in desktop.parse_keys("Ctrl + Shift + s")] == ["Key.ctrl", "Key.shift", "s"]
    with pytest.raises(ValueError):
        desktop.parse_keys("ctrl+banana")


def test_clipboard_round_trip_through_chat(jarvis, monkeypatch):
    store = {"text": "ERROR 0x80070005: access denied"}

    def fake_run(cmd, input_text=None, timeout=10):
        if input_text is not None:
            store["text"] = input_text
            return ""
        return store["text"]

    monkeypatch.setattr(desktop, "_run", fake_run)
    monkeypatch.setattr(desktop, "clipboard_commands", lambda: (["read"], ["write"]))
    assert "0x80070005" in run(jarvis.chat("what's on my clipboard", "c1"))["text"]
    r = run(jarvis.chat("copy meeting moved to 3pm to the clipboard", "c1"))
    assert r["pending_approvals"] and store["text"].startswith("ERROR")
    run(jarvis.chat("approve", "c1"))
    assert store["text"] == "meeting moved to 3pm"


def test_proactive_reminder_fires_once_and_briefing_once_per_day(jarvis):
    events = []
    q = jarvis.bus.subscribe()
    r = run(jarvis.chat("remind me in 20 minutes to call Sam"))
    assert "remind you at" in r["text"]
    assert jarvis.proactive.check() == []  # not due yet
    later = time.time() + 21 * 60
    assert jarvis.proactive.check(later) == ["Reminder: call Sam"]
    assert jarvis.proactive.check(later + 60) == []  # never repeats
    while not q.empty():
        events.append(q.get_nowait())
    announce = [e for e in events if e["type"] == "assistant.announce"]
    assert len(announce) == 1 and announce[0]["kind"] == "reminder"

    jarvis.db.set_setting("proactive", {"briefing_time": "00:00"})
    said = jarvis.proactive.check()
    assert len(said) == 1 and said[0].startswith("Good morning.") and "open task" in said[0] and "System: CPU" in said[0]
    assert jarvis.proactive.check() == []
    assert "open task" in run(jarvis.chat("brief me"))["text"]


def test_browser_refuses_local_files_and_its_own_control_panel(jarvis):
    for bad in ("file:///etc/passwd", "javascript:alert(1)", f"http://127.0.0.1:{jarvis.settings.port}/", f"http://localhost:{jarvis.settings.port}/api/approvals"):
        with pytest.raises(SecurityError):
            jarvis.browser.check_url(bad)
    assert jarvis.browser.check_url("example.org/a") == "https://example.org/a"
    assert jarvis.browser.check_url("http://localhost:3000/") == "http://localhost:3000/"  # the user's own dev servers are fine


PAGE = b"""<html><head><title>Shop</title></head><body><main><h1>Order form</h1>
<form onsubmit="document.getElementById('out').textContent='Ordered: '+document.getElementById('item').value;return false">
<label for=item>Item name</label><input id=item name=item placeholder="What do you want?"><button type=submit>Place order</button></form>
<p id=out>No order yet</p><a href="/about">About us</a></main></body></html>"""


@pytest.fixture
def site():
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200); self.send_header("content-type", "text/html"); self.end_headers(); self.wfile.write(PAGE)

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/"
    srv.shutdown()


@needs_chrome
def test_browser_automation_read_fill_click_with_approval(jarvis, site, monkeypatch):
    monkeypatch.setenv("JARVIS_BROWSER_PATH", CHROME)
    monkeypatch.setenv("JARVIS_BROWSER_HEADLESS", "1")
    monkeypatch.setenv("JARVIS_BROWSER_NO_SANDBOX", "1")
    enable(jarvis, "browser_control")
    ctx = ToolContext(jarvis, agent="computer", actor="test")

    async def scenario():
        snap = (await jarvis.executor.call("browser_open", {"url": site}, ctx)).data
        assert snap["title"] == "Shop" and "Order form" in snap["text"]
        kinds = {e["label"]: e["kind"] for e in snap["elements"]}
        assert kinds["Place order"] == "button" and kinds["About us"] == "link"
        field = next(e["index"] for e in snap["elements"] if e["kind"] == "text")
        res = await jarvis.executor.call("browser_fill", {"text": "blue teapot", "index": field}, ctx)
        assert res.pending and "blue teapot" in res.preview
        assert "No order yet" in (await jarvis.executor.call("browser_read", {}, ctx)).data["text"]  # nothing typed before approval
        await jarvis.executor.decide(res.approval_id, True)
        res = await jarvis.executor.call("browser_click", {"target": "Place order"}, ctx)
        done = await jarvis.executor.decide(res.approval_id, True)
        assert done["status"] == "executed"
        assert "Ordered: blue teapot" in (await jarvis.executor.call("browser_read", {}, ctx)).data["text"]
        await jarvis.browser.close()

    run(scenario())


@needs_gui
def test_real_screen_read_click_by_label_and_typing(jarvis):
    """Against a real window on a real (virtual) display: OCR the screen, click a button by its words, type into a field."""
    from concurrent.futures import ThreadPoolExecutor

    from playwright.sync_api import sync_playwright

    pool = ThreadPoolExecutor(1)  # sync Playwright owns this thread's event loop, so JARVIS runs in another
    run = lambda coro: pool.submit(__import__("asyncio").run, coro).result()  # noqa: E731
    enable(jarvis, "screen_access", "desktop_control")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, executable_path=CHROME, args=["--no-sandbox", "--window-position=0,0", "--window-size=1000,700"])
        page = browser.new_page(no_viewport=True)
        page.set_content("<body style='font:28px sans-serif;padding:60px'><h1>Quarterly Report</h1>"
                         "<button onclick=\"document.title='submitted';document.getElementById('f').focus()\" style='font-size:28px;padding:14px 30px'>Submit Order</button>"
                         "<p>Total revenue 4821 dollars</p><input id=f style='font-size:28px'></body>")
        page.wait_for_timeout(1200)

        r = run(jarvis.chat("what's on my screen", "c1"))
        assert "Quarterly Report" in r["text"] and "4821" in r["text"]

        r = run(jarvis.chat("click Submit Order", "c1"))
        assert r["pending_approvals"] and page.title() != "submitted"  # nothing clicked yet
        ap = jarvis.db.one("SELECT preview FROM approvals WHERE status='pending'")
        assert 'on-screen text "Submit Order"' in ap["preview"]
        assert "done" in run(jarvis.chat("approve", "c1"))["text"].lower()
        page.wait_for_timeout(400)
        assert page.title() == "submitted"

        run(jarvis.chat("type hello from jarvis", "c1"))
        run(jarvis.chat("approve", "c1"))
        page.wait_for_timeout(400)
        assert page.input_value("#f") == "hello from jarvis"

        ctx = ToolContext(jarvis, agent="computer", conversation_id="c1")
        assert run(jarvis.executor.call("wait_for_text", {"text": "total revenue", "timeout_s": 5}, ctx)).data["appeared"]
        gone = run(jarvis.executor.call("wait_for_text", {"text": "Payment complete", "timeout_s": 1}, ctx)).data
        assert not gone["appeared"] and "Quarterly Report" in gone["visible_text"]

        r = run(jarvis.chat("click Nonexistent Button", "c1"))
        out = run(jarvis.chat("approve", "c1"))
        assert "not visible on screen" in out["text"]
        browser.close()


def test_desktop_launcher_pieces(jarvis, monkeypatch, tmp_path):
    import urllib.request

    from jarvis import desktop_app

    cmd = desktop_app.window_command(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe", "http://127.0.0.1:8765/x", tmp_path / "ui")
    assert cmd[1] == "--app=http://127.0.0.1:8765/x" and cmd[2].startswith("--user-data-dir=")
    assert "/api/auth?token=" in desktop_app.app_url(jarvis, "voice") and desktop_app.app_url(jarvis, "voice").endswith("next=/%23/voice")
    script = desktop_app.autostart_script(r"C:\jarvis\backend\.venv\Scripts\python.exe", r"C:\jarvis")
    assert r"C:\jarvis\backend\.venv\Scripts\pythonw.exe" in script and "-m jarvis.desktop_app" in script and 'CurrentDirectory = "C:\\jarvis"' in script

    jarvis.settings.port = 8791
    server, thread = desktop_app.start_server(jarvis)  # the embedded server really serves
    try:
        assert urllib.request.urlopen("http://127.0.0.1:8791/api/health", timeout=5).status == 200
    finally:
        server.should_exit = True
        thread.join(timeout=10)
