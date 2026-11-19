"""The floating JARVIS icon: a small always-on-top circle on the screen edge.

    python -m jarvis.overlay [--parent PID]      (the desktop app starts it; it exits when that process ends)

Its colour shows the state (cyan idle, green listening, amber thinking, orange working, blue speaking, red error,
grey when JARVIS is not reachable). Hover over it - or click it to keep it open - for a panel with what JARVIS is
doing right now, what it heard and answered, actions waiting for approval (Approve / Deny), recent activity, a box to
type a command, Talk (listen now, no wake word), Window (the full JARVIS window) and Self-test (create a file with code,
run it in the browser, delete it). Drag the circle to move it; the position is remembered.

A separate process with tkinter (part of Python): it only talks to the local API, polling /api/overlay twice a second.
"""
from __future__ import annotations

import argparse
import json
import queue
import threading
import time
import tkinter as tk
import urllib.error
import urllib.request
from pathlib import Path

from .config import load_settings

ORB = 46
PANEL_W = 380
KEY = "#010203"  # transparent colour around the circle
BG, CARD, FG, MUTED, LINE = "#0b1220", "#111a2e", "#e2e8f0", "#8b98ad", "#1f2a44"
COLORS = {"idle": "#22d3ee", "waiting": "#22d3ee", "listening": "#22c55e", "recording": "#22c55e", "transcribing": "#a3e635",
          "thinking": "#f59e0b", "executing": "#fb923c", "speaking": "#60a5fa", "error": "#ef4444", "offline": "#64748b", "loading": "#94a3b8"}
LABELS = {"idle": "Ready", "waiting": "Listening for “Hey Jarvis”", "recording": "Listening…", "transcribing": "Understanding what you said…",
          "thinking": "Thinking…", "executing": "Working…", "speaking": "Speaking", "error": "Something went wrong", "offline": "JARVIS is not running",
          "loading": "Loading the speech model…", "listening": "Listening…"}
FONT = ("Segoe UI", 9)
FONT_B = ("Segoe UI Semibold", 10)


class Api:
    def __init__(self, port: int, token: str):
        self.base, self.token = f"http://127.0.0.1:{port}", token

    def call(self, method: str, path: str, body: dict | None = None, timeout: float = 5):
        data = json.dumps(body).encode() if body is not None else (b"" if method == "POST" else None)
        req = urllib.request.Request(self.base + path, data=data, method=method,
                                     headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read() or b"null")


def work_area() -> tuple[int, int, int, int] | None:
    try:
        from .desktop_app import work_area as wa

        return wa()
    except Exception:  # noqa: BLE001
        return None


class Overlay:
    def __init__(self, api: Api, settings, parent_pid: int | None = None):
        self.api, self.settings, self.parent_pid = api, settings, parent_pid
        self.data: dict | None = None
        self.updates: queue.Queue = queue.Queue()
        self.pinned = False
        self.panel_open = False
        self.outside_since: float | None = None
        self.conversation: str | None = None
        self.phase = 0.0
        self.pos_file = settings.data_dir / "overlay.json"

        self.root = tk.Tk()
        self.root.title("JARVIS icon")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.configure(bg=KEY)
        try:
            self.root.attributes("-transparentcolor", KEY)
        except tk.TclError:
            pass
        self.canvas = tk.Canvas(self.root, width=ORB, height=ORB, bg=KEY, highlightthickness=0, cursor="hand2")
        self.canvas.pack()
        x, y = self._start_position()
        self.root.geometry(f"{ORB}x{ORB}+{x}+{y}")
        self.canvas.bind("<ButtonPress-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._drag)
        self.canvas.bind("<ButtonRelease-1>", self._release)
        self.canvas.bind("<Enter>", lambda _e: self.root.after(200, self._hover_open))
        self.canvas.bind("<Button-3>", lambda _e: self._talk())
        self._build_panel()
        threading.Thread(target=self._poll, daemon=True, name="overlay-poll").start()
        self.root.after(100, self._tick)

    # -- position -----------------------------------------------------------------------------------------------------
    def _start_position(self) -> tuple[int, int]:
        try:
            p = json.loads(self.pos_file.read_text())
            return int(p["x"]), int(p["y"])
        except (OSError, ValueError, KeyError):
            pass
        area = work_area() or (0, 0, self.root.winfo_screenwidth(), self.root.winfo_screenheight() - 40)
        x, y, w, h = area
        return x + w - ORB - 6, y + h - ORB - 110

    def _press(self, e):
        self._drag_start = (e.x_root, e.y_root, self.root.winfo_x(), self.root.winfo_y())
        self._moved = False

    def _drag(self, e):
        sx, sy, wx, wy = self._drag_start
        dx, dy = e.x_root - sx, e.y_root - sy
        if abs(dx) + abs(dy) > 3:
            self._moved = True
            self.root.geometry(f"+{wx + dx}+{wy + dy}")
            if self.panel_open:
                self._place_panel()

    def _release(self, _e):
        if self._moved:
            try:
                self.pos_file.write_text(json.dumps({"x": self.root.winfo_x(), "y": self.root.winfo_y()}))
            except OSError:
                pass
        elif self.panel_open and self.pinned:  # a click: keep the panel open, or close a kept-open one
            self.pinned = False
            self._hide_panel()
        else:
            self.pinned = True
            self._show_panel()

    # -- panel ----------------------------------------------------------------------------------------------------------
    def _build_panel(self):
        p = self.panel = tk.Toplevel(self.root)
        p.overrideredirect(True)
        p.attributes("-topmost", True)
        p.configure(bg=BG, highlightthickness=1, highlightbackground=LINE)
        p.withdraw()
        pad = {"padx": 12}
        head = tk.Frame(p, bg=BG)
        head.pack(fill="x", pady=(10, 2), **pad)
        self.title = tk.Label(head, text="JARVIS", font=("Segoe UI Semibold", 12), fg=FG, bg=BG, anchor="w")
        self.title.pack(side="left")
        self.pin_btn = tk.Label(head, text="📌", font=FONT, fg=MUTED, bg=BG, cursor="hand2")
        self.pin_btn.pack(side="right")
        self.pin_btn.bind("<Button-1>", lambda _e: self._toggle_pin())
        self.sub = tk.Label(p, text="", font=FONT, fg=MUTED, bg=BG, anchor="w", justify="left", wraplength=PANEL_W - 24)
        self.sub.pack(fill="x", **pad)

        self.now = self._card(p, "Now")
        self.now_text = self._text_label(self.now)
        self.exchange = self._card(p, "Last request")
        self.req_text = self._text_label(self.exchange)
        self.reply_text = self._text_label(self.exchange, fg=FG)
        self.approvals = tk.Frame(p, bg=BG)
        self.approvals.pack(fill="x", **pad)
        self.activity = self._card(p, "Activity")
        self.activity_text = self._text_label(self.activity, fg=MUTED)
        self.test = self._card(p, "Self-test")
        self.test_text = self._text_label(self.test)
        self.test_buttons = tk.Frame(self.test, bg=CARD)
        self.test_buttons.pack(fill="x", pady=(4, 0))

        box = tk.Frame(p, bg=BG)
        box.pack(fill="x", pady=(8, 4), **pad)
        self.entry = tk.Entry(box, font=("Segoe UI", 10), bg=CARD, fg=FG, insertbackground=FG, relief="flat", highlightthickness=1,
                              highlightbackground=LINE, highlightcolor="#22d3ee")
        self.entry.pack(side="left", fill="x", expand=True, ipady=5)
        self.entry.bind("<Return>", lambda _e: self._send())
        self._button(box, "Send", self._send).pack(side="left", padx=(6, 0))
        row = tk.Frame(p, bg=BG)
        row.pack(fill="x", pady=(2, 10), **pad)
        self._button(row, "🎤 Talk", self._talk).pack(side="left")
        self._button(row, "Window", self._open_window).pack(side="left", padx=6)
        self._button(row, "Self-test", self._selftest).pack(side="left")
        self.note = tk.Label(row, text="", font=FONT, fg=MUTED, bg=BG)
        self.note.pack(side="left", padx=8)

    def _card(self, parent, title: str) -> tk.Frame:
        f = tk.Frame(parent, bg=CARD, padx=10, pady=6, highlightthickness=0)
        f.pack(fill="x", padx=12, pady=(6, 0))
        tk.Label(f, text=title.upper(), font=("Segoe UI", 7, "bold"), fg=MUTED, bg=CARD, anchor="w").pack(fill="x")
        return f

    def _text_label(self, parent, fg: str = FG) -> tk.Label:
        lbl = tk.Label(parent, text="", font=FONT, fg=fg, bg=CARD, anchor="w", justify="left", wraplength=PANEL_W - 48)
        lbl.pack(fill="x")
        return lbl

    def _button(self, parent, text: str, cmd, color: str = "#1e293b") -> tk.Button:
        return tk.Button(parent, text=text, command=cmd, font=FONT, bg=color, fg=FG, activebackground="#334155", activeforeground=FG,
                         relief="flat", padx=10, pady=3, cursor="hand2", bd=0)

    def _toggle_pin(self):
        self.pinned = not self.pinned
        self.pin_btn.configure(fg="#22d3ee" if self.pinned else MUTED)

    def _hover_open(self):
        if self._pointer_in(self.root):
            self._show_panel()

    def _show_panel(self):
        self.panel_open = True
        self.outside_since = None
        self._render()
        self.panel.deiconify()
        self._place_panel()
        self.pin_btn.configure(fg="#22d3ee" if self.pinned else MUTED)

    def _hide_panel(self):
        self.panel_open = False
        self.panel.withdraw()

    def _place_panel(self):
        self.panel.update_idletasks()
        h = self.panel.winfo_reqheight()
        ox, oy = self.root.winfo_x(), self.root.winfo_y()
        area = work_area() or (0, 0, self.root.winfo_screenwidth(), self.root.winfo_screenheight())
        ax, ay, aw, ah = area
        x = ox - PANEL_W - 8 if ox - PANEL_W - 8 >= ax else ox + ORB + 8
        y = max(ay + 4, min(oy + ORB - h, ay + ah - h - 4))
        self.panel.geometry(f"{PANEL_W}x{h}+{x}+{y}")

    def _pointer_in(self, w, margin: int = 12) -> bool:
        try:
            px, py = w.winfo_pointerxy()
            x, y, ww, hh = w.winfo_rootx(), w.winfo_rooty(), w.winfo_width(), w.winfo_height()
        except tk.TclError:
            return False
        return x - margin <= px <= x + ww + margin and y - margin <= py <= y + hh + margin

    # -- data -----------------------------------------------------------------------------------------------------------
    def _poll(self):
        misses = 0
        while True:
            try:
                self.updates.put(self.api.call("GET", "/api/overlay", timeout=4))
                misses = 0
            except (urllib.error.URLError, OSError, ValueError):
                misses += 1
                self.updates.put(None)
            if self.parent_pid and misses and not _alive(self.parent_pid):
                self.updates.put("quit")
                return
            time.sleep(0.5 if self.panel_open else 0.8)

    def _state(self) -> str:
        d = self.data
        if not d:
            return "offline"
        v = d.get("voice") or {}
        if v.get("state") in ("recording", "transcribing"):
            return v["state"]
        if d.get("state") not in (None, "idle"):
            return d["state"]
        if v.get("state") in ("loading", "error"):
            return v["state"]
        return "waiting" if v.get("running") else "idle"

    def _tick(self):
        try:
            while True:
                item = self.updates.get_nowait()
                if item == "quit":
                    self.root.destroy()
                    return
                self.data = item
                if self.panel_open:
                    self._render()
        except queue.Empty:
            pass
        self._draw()
        if self.panel_open and not self.pinned:
            inside = self._pointer_in(self.root) or self._pointer_in(self.panel)
            typing = self.root.focus_get() is self.entry and self.entry.get().strip()
            if inside or typing:
                self.outside_since = None
            elif self.outside_since is None:
                self.outside_since = time.time()
            elif time.time() - self.outside_since > 0.8:
                self._hide_panel()
        self.root.after(50, self._tick)

    def _draw(self):
        state = self._state()
        color = COLORS.get(state, COLORS["idle"])
        c = self.canvas
        c.delete("all")
        self.phase += 0.12
        import math

        cx = cy = ORB / 2
        r = ORB / 2 - 4
        if state in ("recording", "listening", "speaking"):
            grow = 2.5 + 2.5 * math.sin(self.phase * 1.6)
            c.create_oval(cx - r - grow + 2, cy - r - grow + 2, cx + r + grow - 2, cy + r + grow - 2, outline=color, width=1)
        c.create_oval(cx - r, cy - r, cx + r, cy + r, fill="#0b1220", outline=color, width=3)
        if state in ("thinking", "executing", "transcribing", "loading"):
            start = (self.phase * 120) % 360
            c.create_arc(cx - r + 5, cy - r + 5, cx + r - 5, cy + r - 5, start=start, extent=100, style="arc", outline=color, width=3)
        inner = 7 if state not in ("recording", "speaking") else 7 + 2 * abs(math.sin(self.phase * 2.2))
        c.create_oval(cx - inner, cy - inner, cx + inner, cy + inner, fill=color, outline="")
        if self.data and self.data.get("approvals"):
            c.create_oval(ORB - 14, 2, ORB - 2, 14, fill="#ef4444", outline=KEY)

    def _render(self):
        d = self.data
        state = self._state()
        self.title.configure(text="JARVIS · " + LABELS.get(state, state), fg=COLORS.get(state, FG))
        if not d:
            self.sub.configure(text="Start it from the desktop shortcut. This icon reconnects by itself.")
            return
        v = d.get("voice") or {}
        voice = ("Voice: offline listener, say “Hey Jarvis”" if v.get("running") else
                 "Voice: " + (v.get("unavailable") or ("off" if not v.get("enabled") else v.get("error") or "starting")))
        if v.get("running") and not v.get("speech_model_ready"):
            voice += " (loading speech model…)"
        if v.get("error"):
            voice += f" - {v['error']}"
        self.sub.configure(text=f"Model: {d.get('model')}   ·   {voice}")
        task = d.get("task") or {}
        now = []
        if d.get("detail"):
            now.append("Running: " + d["detail"])
        if task.get("title"):
            now.append(f"Task: {task['title'][:140]}  ({int(time.time() - task.get('started', time.time()))} s)")
        last = v.get("last") or {}
        if last.get("heard"):
            now.append(f"Heard ({last.get('language')}, {last.get('seconds')} s): {last['heard'][:160]}")
        elif last.get("note"):
            now.append("Heard: " + last["note"])
        self.now_text.configure(text="\n".join(now) or "Nothing running.")
        ex = d.get("last") or {}
        self.req_text.configure(text="You: " + (ex.get("request") or "-")[:200], fg=MUTED)
        self.reply_text.configure(text="JARVIS: " + (ex.get("reply") or "-")[:500])
        self._render_approvals(d.get("approvals") or [])
        acts = d.get("activity") or []
        self.activity_text.configure(text="\n".join(time.strftime("%H:%M:%S ", time.localtime(a["ts"])) + a["text"] for a in acts[-7:]) or "-")
        st = d.get("selftest") or {}
        marks = {"passed": "✓", "failed": "✗", "skipped": "–", "running": "…", "pending": "·"}
        lines = [f"{marks.get(s['status'], '?')} {s['title']}" + (f"  ({s['seconds']} s)" if s.get("seconds") is not None else "") +
                 (f"\n    {s['detail'][:160]}" if s.get("detail") else "") for s in st.get("steps") or []]
        head = "Running…" if st.get("running") else (time.strftime("Last run %H:%M", time.localtime(st["finished"])) if st.get("finished") else "Not run yet.")
        self.test_text.configure(text=head + ("\n" + "\n".join(lines) if lines else ""))
        need_browser = not (d.get("capabilities") or {}).get("browser_control")
        if need_browser != bool(self.test_buttons.winfo_children()):
            for w in self.test_buttons.winfo_children():
                w.destroy()
            if need_browser:
                self._button(self.test_buttons, "Allow browser control (for the browser step)", self._allow_browser).pack(side="left")
        if self.panel_open:
            self._place_panel()

    def _render_approvals(self, approvals: list[dict]) -> None:
        """Rebuilt only when the pending set changes, so a button is never replaced under the mouse."""
        key = tuple(a["id"] for a in approvals)
        if key == getattr(self, "_approval_key", None):
            return
        self._approval_key = key
        for w in self.approvals.winfo_children():
            w.destroy()
        for ap in approvals:
            f = tk.Frame(self.approvals, bg="#2a1d0a", padx=10, pady=6)
            f.pack(fill="x", pady=(6, 0))
            tk.Label(f, text=f"Approve {ap['tool']}?", font=FONT_B, fg="#fbbf24", bg="#2a1d0a", anchor="w").pack(fill="x")
            tk.Label(f, text=ap["preview"][:240], font=FONT, fg=FG, bg="#2a1d0a", anchor="w", justify="left", wraplength=PANEL_W - 48).pack(fill="x")
            b = tk.Frame(f, bg="#2a1d0a")
            b.pack(fill="x", pady=(4, 0))
            self._button(b, "Approve", lambda i=ap["id"]: self._decide(i, True), "#166534").pack(side="left")
            self._button(b, "Deny", lambda i=ap["id"]: self._decide(i, False), "#7f1d1d").pack(side="left", padx=6)

    # -- actions --------------------------------------------------------------------------------------------------------
    def _bg(self, fn, done_note: str = ""):
        def run():
            try:
                fn()
                if done_note:
                    self.root.after(0, lambda: self.note.configure(text=done_note))
            except Exception as e:  # noqa: BLE001
                msg = str(e)
                if isinstance(e, urllib.error.HTTPError):
                    try:
                        msg = json.loads(e.read()).get("detail", msg)
                    except ValueError:
                        pass
                self.root.after(0, lambda: self.note.configure(text=msg[:60]))
        threading.Thread(target=run, daemon=True).start()

    def _send(self):
        text = self.entry.get().strip()
        if not text:
            return
        self.entry.delete(0, "end")

        def go():
            r = self.api.call("POST", "/api/chat", {"text": text, "conversation_id": self.conversation, "source": "text"})
            self.conversation = r.get("conversation_id", self.conversation)
        self._bg(go, "sent")

    def _talk(self):
        self._bg(lambda: self.api.call("POST", "/api/ui/listen"), "listening…")

    def _decide(self, aid: str, approve: bool):
        self._bg(lambda: self.api.call("POST", f"/api/approvals/{aid}/decide", {"approve": approve, "scope": "once"}, timeout=120),
                 "approved" if approve else "denied")

    def _selftest(self):
        self._bg(lambda: self.api.call("POST", "/api/selftest"), "self-test started")

    def _allow_browser(self):
        self._bg(lambda: self.api.call("PUT", "/api/settings", {"capabilities": {"browser_control": True}}), "browser control on")

    def _open_window(self):
        def go():
            from types import SimpleNamespace

            from . import desktop_app

            shim = SimpleNamespace(settings=self.settings)
            desktop_app.bring_up(shim)
        self._bg(go)

    def run(self):
        self.root.mainloop()


def _alive(pid: int) -> bool:
    try:
        import psutil

        return psutil.pid_exists(pid)
    except Exception:  # noqa: BLE001
        return True


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="jarvis.overlay")
    ap.add_argument("--parent", type=int, default=None, help="exit when this process (the JARVIS desktop app) ends")
    args = ap.parse_args(argv)
    s = load_settings()
    Overlay(Api(s.port, s.api_token()), s, args.parent).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
