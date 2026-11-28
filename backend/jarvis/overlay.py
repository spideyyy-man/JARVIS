"""The floating JARVIS icon: a small always-on-top circle on the screen edge.

    python -m jarvis.overlay [--parent PID]      (the desktop app starts it; it exits when that process ends)

The puck's bezel shows the state by its motion; JARVIS is always cyan (orb.py), red on an error, grey when not
reachable, amber when an action waits for approval. Other colours are the projects'.
- Hover (or click to keep it open): a compact panel that fades in - one status line, the projects AI agents are working
  on (one dot per AI: filled working, hollow idle), the last request and reply, a "Last 5" drop-down, a command box,
  and Talk / Window.
- An action waiting for approval shows by itself, without hovering: a small bar under the circle with what it is,
  a moving progress line, and Allow / Deny.
Drag the circle to move it; the position is remembered.

A separate process with tkinter (part of Python): it only talks to the local API, polling /api/overlay twice a second.
Widgets are built once and only their text changes, so nothing jumps or flickers while it is open.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import queue
import threading
import time
import tkinter as tk
import urllib.error
import urllib.request

from .config import load_settings
from .orb import COLORS as ORB_COLORS
from .orb import SIZE, LayeredWindow, OrbState, ease_color, render

ORB = SIZE  # the icon window (the orb itself is smaller; the rest is room for its glow)
PANEL_W = 272
HOVER_OPEN_S, HOVER_CLOSE_S = 0.35, 0.45  # a deliberate hover opens the card; passing by does not
BAR_W = 250
KEY = "#010203"  # transparent colour around the circle
BG, CARD, FG, MUTED, LINE, ACCENT, AMBER = "#0b1220", "#131c30", "#e2e8f0", "#8b98ad", "#1f2a44", "#22d3ee", "#f59e0b"
COLORS = {k: "#%02x%02x%02x" % v for k, v in ORB_COLORS.items()}  # JARVIS is cyan; red error, grey off (see orb.COLORS)
LABELS = {"idle": "Ready", "waiting": "Say “Jarvis”", "recording": "Listening…", "transcribing": "Understanding…",
          "thinking": "Thinking…", "executing": "Working…", "speaking": "Speaking", "error": "Something went wrong", "offline": "Not running",
          "loading": "Loading speech model…", "listening": "Listening…", "following": "Listening — just keep talking"}
PROJECT_ROWS = 5  # projects listed in the card; the rest are counted ("2 more")
PIP_W = 60  # room for the per-AI dots
FONT = ("Segoe UI", 9)
ICON_FONT = ("Segoe UI Symbol", 11)  # the card's four control buttons
SMALL = ("Segoe UI", 8)
BOLD = ("Segoe UI Semibold", 9)
FADE_STEP, FADE_MS, PANEL_ALPHA = 0.09, 12, 0.97  # ~0.13 s fade


def elide(text: str, n: int) -> str:
    t = " ".join(str(text or "").split())
    return t if len(t) <= n else t[: n - 1].rstrip() + "…"


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


class Fader:
    """Fades a Toplevel in and out with its window alpha (Windows supports -alpha on toplevels)."""

    def __init__(self, win: tk.Toplevel, top: float = PANEL_ALPHA, on_step=None):
        self.win, self.top, self.alpha, self.target, self._job = win, top, 0.0, 0.0, None
        self.on_step = on_step  # called with 0..1 as it fades, to slide the window along with the fade
        try:
            win.attributes("-alpha", 0.0)
        except tk.TclError:
            pass

    @property
    def shown(self) -> bool:
        return self.target > 0

    def show(self) -> None:
        if self.target == self.top:
            return
        self.target = self.top
        self.win.deiconify()
        self._step()

    def hide(self) -> None:
        if self.target == 0:
            return
        self.target = 0.0
        self._step()

    def _step(self) -> None:
        if self._job:
            self.win.after_cancel(self._job)
            self._job = None
        diff = self.target - self.alpha
        if abs(diff) <= FADE_STEP:
            self.alpha = self.target
        else:
            self.alpha += FADE_STEP if diff > 0 else -FADE_STEP
        try:
            self.win.attributes("-alpha", self.alpha)
        except tk.TclError:
            return
        if self.on_step:
            self.on_step(self.alpha / self.top)
        if self.alpha != self.target:
            self._job = self.win.after(FADE_MS, self._step)
        elif self.alpha == 0:
            self.win.withdraw()


def rounded_image(w: int, h: int, r: int, fill: str, bg: str):
    """An anti-aliased rounded rectangle (Tk's own canvas shapes have jagged corners), drawn at 3x and scaled down."""
    from PIL import Image, ImageDraw, ImageTk

    s = 3
    img = Image.new("RGB", (max(1, w) * s, max(1, h) * s), bg)
    ImageDraw.Draw(img).rounded_rectangle((0, 0, w * s - 1, h * s - 1), radius=r * s, fill=fill)
    return ImageTk.PhotoImage(img.resize((max(1, w), max(1, h)), Image.LANCZOS))


class Pill(tk.Canvas):
    """A rounded button: text on an anti-aliased pill, lighter while hovered."""

    def __init__(self, parent, text: str, cmd, bg: str = "#1e293b", fg: str = FG, font=SMALL, padx: int = 10, pady: int = 4):
        import tkinter.font as tkfont

        f = tkfont.Font(font=font)
        w, h = f.measure(text) + 2 * padx, f.metrics("linespace") + 2 * pady
        parent_bg = parent.cget("bg")
        super().__init__(parent, width=w, height=h, bg=parent_bg, highlightthickness=0, bd=0, cursor="hand2")
        self._imgs = (rounded_image(w, h, h // 2, bg, parent_bg), rounded_image(w, h, h // 2, _lighter(bg), parent_bg))
        self._bgitem = self.create_image(0, 0, image=self._imgs[0], anchor="nw")
        self._text = self.create_text(w // 2, h // 2, text=text, fill=fg, font=font)
        self.bind("<Button-1>", lambda _e: cmd())
        self.bind("<Enter>", lambda _e: self.itemconfigure(self._bgitem, image=self._imgs[1]))
        self.bind("<Leave>", lambda _e: self.itemconfigure(self._bgitem, image=self._imgs[0]))

    def set_text(self, text: str) -> None:
        if self.itemcget(self._text, "text") != text:
            self.itemconfigure(self._text, text=text)

    def set_fg(self, color: str) -> None:
        if self.itemcget(self._text, "fill") != color:
            self.itemconfigure(self._text, fill=color)


class RoundBox(tk.Canvas):
    """A rounded card holding other widgets (in `.inner`); it follows the height of what it holds."""

    def __init__(self, parent, width: int, fill: str = CARD, radius: int = 10, pad: int = 8):
        parent_bg = parent.cget("bg")
        super().__init__(parent, width=width, height=2 * pad, bg=parent_bg, highlightthickness=0, bd=0)
        self.w, self.fill, self.radius, self.pad, self.parent_bg, self._size = width, fill, radius, pad, parent_bg, None
        self._bg = self.create_image(0, 0, anchor="nw")
        self.inner = tk.Frame(self, bg=fill)
        self.create_window(pad, pad, window=self.inner, anchor="nw", width=width - 2 * pad)
        self.inner.bind("<Configure>", lambda _e: self._fit())

    def _fit(self):
        h = self.inner.winfo_reqheight() + 2 * self.pad
        if self._size != h:
            self._size = h
            self._img = rounded_image(self.w, h, self.radius, self.fill, self.parent_bg)
            self.itemconfigure(self._bg, image=self._img)
            self.configure(height=h)


def round_window(win: tk.Toplevel, border: str) -> None:
    """Windows 11 rounds the window's corners and draws a thin coloured border (no-op elsewhere)."""
    if os.name != "nt":
        return
    try:
        import ctypes

        win.update_idletasks()
        hwnd = int(win.wm_frame(), 16)
        dwm = ctypes.windll.dwmapi
        pref = ctypes.c_int(2)  # DWMWCP_ROUND
        dwm.DwmSetWindowAttribute(ctypes.c_void_p(hwnd), 33, ctypes.byref(pref), 4)  # DWMWA_WINDOW_CORNER_PREFERENCE
        r, g, b = (int(border[i:i + 2], 16) for i in (1, 3, 5))
        color = ctypes.c_uint(r | (g << 8) | (b << 16))  # COLORREF
        dwm.DwmSetWindowAttribute(ctypes.c_void_p(hwnd), 34, ctypes.byref(color), 4)  # DWMWA_BORDER_COLOR
    except Exception:  # noqa: BLE001 - square corners are fine
        pass


class Overlay:
    def __init__(self, api: Api, settings, parent_pid: int | None = None):
        self.api, self.settings, self.parent_pid = api, settings, parent_pid
        self.data: dict | None = None
        self.updates: queue.Queue = queue.Queue()
        self.pinned = False
        self.history_open = False
        self.outside_since: float | None = None
        self.hover_since: float | None = None
        self.conversation: str | None = None
        self.phase = 0.0
        self.pos_file = settings.data_dir / "overlay.json"
        self._texts: dict[str, str] = {}

        self.root = tk.Tk()
        self.root.title("JARVIS icon")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.configure(bg=KEY)
        self.canvas = tk.Canvas(self.root, width=ORB, height=ORB, bg=KEY, highlightthickness=0, cursor="hand2")
        self.canvas.pack()
        x, y = self._start_position()
        self.root.geometry(f"{ORB}x{ORB}+{x}+{y}")
        self.root.update_idletasks()
        # The smooth orb (orb.py) on a per-pixel-alpha window; the old flat canvas drawing is the fallback.
        self.layered = None
        self.orb = OrbState()
        try:
            if os.name == "nt":
                self.layered = LayeredWindow(int(self.root.wm_frame(), 16))
        except Exception:  # noqa: BLE001
            self.layered = None
        if not self.layered:
            try:
                self.root.attributes("-transparentcolor", KEY)
            except tk.TclError:
                pass
        self._last = time.time()
        self._frame = 0
        self.canvas.bind("<ButtonPress-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._drag)
        self.canvas.bind("<ButtonRelease-1>", self._release)
        self.canvas.bind("<Button-3>", lambda _e: self._talk())
        self._build_panel()
        self._build_approval_bar()
        threading.Thread(target=self._poll, daemon=True, name="overlay-poll").start()
        self.root.after(100, self._tick)

    # -- position -----------------------------------------------------------------------------------------------------
    def _start_position(self) -> tuple[int, int]:
        try:
            p = json.loads(self.pos_file.read_text())
            return int(p["x"]), int(p["y"])
        except (OSError, ValueError, KeyError):
            pass
        x, y, w, h = work_area() or (0, 0, self.root.winfo_screenwidth(), self.root.winfo_screenheight() - 40)
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
            self._place_panel()
            self._place_bar()

    def _release(self, _e):
        if self._moved:
            try:
                self.pos_file.write_text(json.dumps({"x": self.root.winfo_x(), "y": self.root.winfo_y()}))
            except OSError:
                pass
        else:  # a click goes into the window (the card itself opens on hover)
            self._hide_panel()
            self._open_window()

    def _area(self) -> tuple[int, int, int, int]:
        return work_area() or (0, 0, self.root.winfo_screenwidth(), self.root.winfo_screenheight())

    def _geometry(self, win: tk.Toplevel, geo: str) -> None:
        """Move/resize only when something changed (setting it every poll makes Windows redraw the frame)."""
        if self._texts.get(f"geo{id(win)}") != geo:
            self._texts[f"geo{id(win)}"] = geo
            win.geometry(geo)

    # -- widgets helpers --------------------------------------------------------------------------------------------------
    def _set_text(self, key: str, widget: tk.Widget, text: str, **extra) -> None:
        """Change a label only when its text changed: no flicker, no relayout every poll."""
        if self._texts.get(key) != text or extra:
            self._texts[key] = text
            widget.configure(text=text, **extra)

    def _button(self, parent, text: str, cmd, bg: str = "#1e293b", fg: str = FG, font=SMALL) -> Pill:
        return Pill(parent, text, cmd, bg=bg, fg=fg, font=font)

    # -- the hover card ------------------------------------------------------------------------------------------------------
    # Compact on purpose (2026-11-27; the owner compared it with ChatGPT's companion: the old panel was big, wordy and
    # popped up when the pointer merely passed by). It holds: the projects AIs are working on (each in its own colour -
    # the same colour as its arc on the icon; click to jump to the window its AI runs in), a small chat box, the last
    # reply folded under "˅", and four controls - talk, pause speaking, sound on/off, open the window.
    def _build_panel(self):
        p = self.panel = tk.Toplevel(self.root)
        p.overrideredirect(True)
        p.attributes("-topmost", True)
        p.configure(bg=BG, highlightthickness=0)
        p.withdraw()
        round_window(p, LINE)
        self.panel_fade = Fader(p, on_step=lambda f: self._place_panel(slide=(1 - f) ** 2 * 14))  # eased slide

        self.proj = tk.Frame(p, bg=BG)
        self.proj.pack(fill="x", padx=8, pady=(8, 0))
        self.proj_rows = []
        for _ in range(PROJECT_ROWS):
            row = tk.Frame(self.proj, bg=BG, cursor="hand2")
            # the AI dots are packed first: pack gives space in packing order, so long names can no longer push them out
            pips = tk.Canvas(row, width=PIP_W, height=16, bg=BG, highlightthickness=0)
            pips.pack(side="right", padx=(0, 4))
            dot = tk.Canvas(row, width=10, height=16, bg=BG, highlightthickness=0)
            dot.pack(side="left", padx=(4, 6))
            name = tk.Label(row, text="", font=BOLD, fg=FG, bg=BG, anchor="w")
            name.pack(side="left")
            kinds = tk.Label(row, text="", font=SMALL, fg=MUTED, bg=BG, anchor="w")
            kinds.pack(side="left", padx=(6, 0))
            widgets = (row, dot, name, kinds, pips)
            for w in widgets:
                w.bind("<Button-1>", lambda _e, r=row: self._open_project(r))
                w.bind("<Enter>", lambda _e, ws=widgets: self._row_hover(ws, True))
                w.bind("<Leave>", lambda _e, ws=widgets: self._row_hover(ws, False))
            self.proj_rows.append(widgets)
        self.proj_empty = tk.Label(self.proj, text="No AI agents running", font=SMALL, fg=MUTED, bg=BG, anchor="w")
        self.proj_more = tk.Label(self.proj, text="", font=SMALL, fg=MUTED, bg=BG, anchor="w")
        tk.Frame(p, bg=LINE, height=1).pack(fill="x", padx=10, pady=(6, 0))

        self.box = tk.Frame(p, bg=BG)
        self.box.pack(fill="x", padx=8, pady=(8, 0))
        field = RoundBox(self.box, PANEL_W - 16 - 40, fill=CARD, radius=14, pad=6)
        field.pack(side="left")
        self.entry = tk.Entry(field.inner, font=FONT, bg=CARD, fg=FG, insertbackground=FG, relief="flat", highlightthickness=0, bd=0)
        self.entry.pack(fill="x", ipady=2, padx=4)
        self.entry.bind("<Return>", lambda _e: self._send())
        self._button(self.box, "➤", self._send, bg="#0e7490").pack(side="right")

        self.last_toggle = tk.Label(p, text="˅  Last reply", font=SMALL, fg=MUTED, bg=BG, anchor="w", cursor="hand2")
        self.last_toggle.pack(fill="x", padx=12, pady=(6, 0))
        self.last_toggle.bind("<Button-1>", lambda _e: self._toggle_last())
        self.last_card = RoundBox(p, PANEL_W - 16, fill=CARD, radius=10, pad=8)
        self.you = tk.Label(self.last_card.inner, text="", font=SMALL, fg=MUTED, bg=CARD, anchor="w", justify="left", wraplength=PANEL_W - 40)
        self.you.pack(fill="x")
        self.reply = tk.Label(self.last_card.inner, text="", font=FONT, fg=FG, bg=CARD, anchor="w", justify="left", wraplength=PANEL_W - 40)
        self.reply.pack(fill="x", pady=(2, 0))

        row = tk.Frame(p, bg=BG)
        row.pack(fill="x", padx=8, pady=(6, 8))
        self.talk_btn = self._button(row, "🎤", self._talk, font=ICON_FONT)
        self.talk_btn.pack(side="left")
        self.pause_btn = self._button(row, "⏸", self._pause, font=ICON_FONT)
        self.pause_btn.pack(side="left", padx=(4, 0))
        self.sound_btn = self._button(row, "🔊", self._toggle_sound, font=ICON_FONT)
        self.sound_btn.pack(side="left", padx=(4, 0))
        self._button(row, "↗", self._open_window, font=ICON_FONT).pack(side="left", padx=(4, 0))
        self.note = tk.Label(row, text="", font=SMALL, fg=MUTED, bg=BG, anchor="e")
        self.note.pack(side="right")
        self.timer_row = self.note  # countdowns share the status spot
        self.meta = self.note
        p.bind("<Enter>", lambda _e: setattr(self, "outside_since", None))

    def _row_hover(self, widgets, on: bool):
        bg = CARD if on else BG
        for w in widgets:
            w.configure(bg=bg)

    def _toggle_last(self):
        self.last_open = not getattr(self, "last_open", False)
        self.last_toggle.configure(text=("˄  Last reply" if self.last_open else "˅  Last reply"))
        if self.last_open:
            self.last_card.pack(padx=8, pady=(4, 0), after=self.last_toggle)
        else:
            self.last_card.pack_forget()
        self._place_panel()

    def _on_orb(self) -> bool:
        """The pointer is over the visible puck (a circle), not just the square window around its shadow."""
        try:
            px, py = self.root.winfo_pointerxy()
            cx, cy = self.root.winfo_rootx() + ORB / 2, self.root.winfo_rooty() + ORB / 2
        except tk.TclError:
            return False
        return (px - cx) ** 2 + (py - cy) ** 2 <= 24 ** 2

    def _show_panel(self):
        self.outside_since = None
        self._render()
        self._place_panel()
        self.panel_fade.show()

    def _hide_panel(self):
        self.panel_fade.hide()

    def _place_panel(self, slide: float = 0.0):
        """Beside the orb (left of it, or right near the screen's left edge); `slide` shifts it while it fades in."""
        if not self.panel_fade.shown:
            return
        self.panel.update_idletasks()
        h = self.panel.winfo_reqheight()
        ox, oy = self.root.winfo_x(), self.root.winfo_y()
        ax, ay, aw, ah = self._area()
        pad = ORB // 2 - 24  # the transparent margin around the visible orb
        left = ox + pad - PANEL_W - 6
        x = int(left + slide) if left >= ax else int(ox + ORB - pad + 6 - slide)
        y = max(ay + 4, min(oy + ORB - pad - h, ay + ah - h - 4))
        self._geometry(self.panel, f"{PANEL_W}x{h}+{x}+{y}")

    def _pointer_in(self, w, margin: int = 10) -> bool:
        try:
            if not w.winfo_viewable():
                return False
            px, py = w.winfo_pointerxy()
            x, y, ww, hh = w.winfo_rootx(), w.winfo_rooty(), w.winfo_width(), w.winfo_height()
        except tk.TclError:
            return False
        return x - margin <= px <= x + ww + margin and y - margin <= py <= y + hh + margin

    # -- the approval bar (no hover needed) ----------------------------------------------------------------------------------
    def _build_approval_bar(self):
        b = self.bar = tk.Toplevel(self.root)
        b.overrideredirect(True)
        b.attributes("-topmost", True)
        b.configure(bg=BG, highlightthickness=0)
        b.withdraw()
        round_window(b, AMBER)
        self.bar_fade = Fader(b, top=0.98, on_step=lambda f: self._place_bar(slide=(1 - f) ** 2 * -10))
        self.bar_title = tk.Label(b, text="", font=BOLD, fg=AMBER, bg=BG, anchor="w")
        self.bar_title.pack(fill="x", padx=8, pady=(6, 0))
        self.bar_text = tk.Label(b, text="", font=SMALL, fg=FG, bg=BG, anchor="w", justify="left", wraplength=BAR_W - 16)
        self.bar_text.pack(fill="x", padx=8)
        self.bar_line = tk.Canvas(b, width=BAR_W - 16, height=3, bg=LINE, highlightthickness=0)
        self.bar_line.pack(padx=8, pady=(5, 5))
        row = tk.Frame(b, bg=BG)
        row.pack(fill="x", padx=8, pady=(0, 7))
        self._button(row, "✓ Allow", lambda: self._decide_current(True), bg="#166534").pack(side="left")
        self._button(row, "✕ Deny", lambda: self._decide_current(False), bg="#7f1d1d").pack(side="left", padx=6)
        self.bar_count = tk.Label(row, text="", font=SMALL, fg=MUTED, bg=BG)
        self.bar_count.pack(side="right")
        self.current_approval: str | None = None
        self.deciding: set[str] = set()

    def _place_bar(self, slide: float = 0.0):
        if not self.bar_fade.shown:
            return
        self.bar.update_idletasks()
        h = self.bar.winfo_reqheight()
        ox, oy = self.root.winfo_x(), self.root.winfo_y()
        ax, ay, aw, ah = self._area()
        pad = ORB // 2 - 24
        x = max(ax + 4, min(ox + ORB // 2 - BAR_W // 2, ax + aw - BAR_W - 4))
        below = oy + ORB - pad + 4
        y = int(below + slide) if below + h <= ay + ah else int(oy + pad - h - 4 - slide)  # under the orb, or above it near the bottom
        self._geometry(self.bar, f"{BAR_W}x{h}+{x}+{y}")

    def _render_bar(self, approvals: list[dict]):
        waiting = [a for a in approvals if a["id"] not in self.deciding]
        if not waiting:
            self.current_approval = None
            self.bar_fade.hide()
            return
        ap = waiting[0]
        self.current_approval = ap["id"]
        self._set_text("bar_title", self.bar_title, "Allow " + ap["tool"].replace("_", " ") + "?")
        self._set_text("bar_text", self.bar_text, elide(ap["preview"].splitlines()[0] if ap["preview"] else "", 110))
        self._set_text("bar_count", self.bar_count, f"1 of {len(waiting)}" if len(waiting) > 1 else "")
        if not self.bar_fade.shown:
            self.bar_fade.show()
        self._place_bar()

    def _draw_bar_line(self):
        """An indeterminate progress line: a soft segment sliding back and forth while the action waits."""
        if not self.bar_fade.shown:
            return
        c, w = self.bar_line, BAR_W - 16
        c.delete("all")
        seg = w * 0.32
        x = (math.sin(self.phase * 0.9) * 0.5 + 0.5) * (w - seg)
        c.create_rectangle(x, 0, x + seg, 3, fill=AMBER, outline="")

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
            time.sleep(0.5)

    def _state(self) -> str:
        d = self.data
        if not d:
            return "offline"
        v = d.get("voice") or {}
        if v.get("state") in ("recording", "transcribing", "following"):
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
                if self.panel_fade.shown:
                    self._render()
                self._render_bar((item or {}).get("approvals") or [])
        except queue.Empty:
            pass
        self.phase += 0.12
        self._draw()
        self._draw_bar_line()
        self._hover()
        self.root.after(16 if self.layered else 40, self._tick)

    def _hover(self):
        """Open after a short, deliberate hover; close shortly after the pointer leaves (not while typing)."""
        on_orb = self._on_orb()
        if not self.panel_fade.shown:
            if on_orb:
                self.hover_since = self.hover_since or time.time()
                if time.time() - self.hover_since > HOVER_OPEN_S:
                    self._show_panel()
            else:
                self.hover_since = None
            return
        if self.pinned:
            return
        inside = on_orb or self._pointer_in(self.panel, margin=4) or self._pointer_in(self.bar, margin=4)
        # half-typed text keeps it open only while the pointer is still near it
        typing = self.root.focus_get() is self.entry and self.entry.get().strip() and self._pointer_in(self.panel, margin=80)
        if inside or typing:
            self.outside_since = None
        elif self.outside_since is None:
            self.outside_since = time.time()
        elif time.time() - self.outside_since > HOVER_CLOSE_S:
            self.hover_since = None
            self._hide_panel()

    def _draw(self):
        if self.layered:
            return self._draw_orb()
        state = self._state()
        color = COLORS.get(state, COLORS["idle"])
        c = self.canvas
        c.delete("all")
        cx = cy = ORB / 2
        r = ORB / 2 - 5
        waiting = bool(self.data and self.data.get("approvals"))
        if state in ("recording", "listening", "speaking"):
            grow = 2 + 2 * math.sin(self.phase * 1.6)
            c.create_oval(cx - r - grow, cy - r - grow, cx + r + grow, cy + r + grow, outline=color, width=1)
        c.create_oval(cx - r, cy - r, cx + r, cy + r, fill="#0b1220", outline=color, width=3)
        if state in ("thinking", "executing", "transcribing", "loading"):
            c.create_arc(cx - r + 5, cy - r + 5, cx + r - 5, cy + r - 5, start=(self.phase * 120) % 360, extent=100, style="arc", outline=color, width=3)
        if waiting:  # an amber arc sweeping round the rim: something needs you
            c.create_arc(cx - r - 3, cy - r - 3, cx + r + 3, cy + r + 3, start=(-self.phase * 90) % 360, extent=70, style="arc", outline=AMBER, width=3)
        inner = 6 + (2 * abs(math.sin(self.phase * 2.2)) if state in ("recording", "speaking") else 0)
        c.create_oval(cx - inner, cy - inner, cx + inner, cy + inner, fill=color, outline="")

    def _timer_text(self) -> str:
        now, parts = time.time(), []
        for t in (self.data or {}).get("timers") or []:
            if t["kind"] == "timer":
                left = max(0, int(t["due"] - now))
                h, rem = divmod(left, 3600)
                clock = f"{h}:{rem // 60:02d}:{rem % 60:02d}" if h else f"{rem // 60}:{rem % 60:02d}"
                parts.append(f"⏱ {t['label'] + ' ' if t['label'] else ''}{clock}")
            else:
                parts.append("⏰ " + time.strftime("%I:%M %p", time.localtime(t["due"])).lstrip("0") + (f" {t['label']}" if t["label"] else ""))
        return "   ·   ".join(parts[:3])

    def _measure(self, font, text: str) -> int:
        import tkinter.font as tkfont

        fonts = self.__dict__.setdefault("_fonts", {})
        if font not in fonts:
            fonts[font] = tkfont.Font(font=font)
        return fonts[font].measure(text)

    def _fit(self, font, text: str, px: int) -> str:
        """The text, shortened with '…' until it fits in px pixels (empty when not even a few letters fit)."""
        t = " ".join(str(text or "").split())
        if self._measure(font, t) <= px:
            return t
        while len(t) > 3 and self._measure(font, t + "…") > px:
            t = t[:-1].rstrip(" ,")
        return t + "…" if len(t) > 3 else ""

    def _render_projects(self, snap: dict):
        """One row per project with AIs on it, in the project's own colour: a dot, the name, which AIs, and one small dot
        per AI (filled = working, hollow = idle). Clicking a row opens the window its AI runs in."""
        projects = snap.get("projects") or []
        self._projects = projects
        for i, (row, dot, name, kinds, pips) in enumerate(self.proj_rows):
            g = projects[i] if i < len(projects) else None
            if g is None:
                if row.winfo_manager():
                    row.pack_forget()
                continue
            if not row.winfo_manager():
                row.pack(fill="x", pady=(1, 0), before=self.proj_more) if self.proj_more.winfo_manager() else row.pack(fill="x", pady=(1, 0))
            row.project_key = g.get("key", "")
            color = g.get("color") or ACCENT
            tally: dict[str, int] = {}
            for a in g["agents"]:
                tally[a["kind"]] = tally.get(a["kind"], 0) + 1
            who = ", ".join(f"{n} {k}" if n > 1 else k for k, n in tally.items())
            shown = elide(g["name"], 18)
            self._set_text(f"pname{i}", name, shown, fg=FG if g["working"] else MUTED)
            # name + AIs share one line: the AIs get what is left between the name and the dots (measured in pixels)
            room = PANEL_W - 16 - 20 - (PIP_W + 4) - 6 - 6 - self._measure(BOLD, shown)
            self._set_text(f"pkind{i}", kinds, self._fit(SMALL, who, room))
            sig = color + "".join("1" if a["busy"] else "0" for a in g["agents"])
            if self._texts.get(f"ppips{i}") != sig:
                self._texts[f"ppips{i}"] = sig
                dot.delete("all")
                dot.create_oval(1, 4, 9, 12, fill=color, outline="")
                pips.delete("all")
                for k, busy in enumerate(sig[len(color):][:6]):
                    x = PIP_W - 6 - k * 10
                    if busy == "1":
                        pips.create_oval(x - 3.5, 4.5, x + 3.5, 11.5, fill=color, outline="")
                    else:
                        pips.create_oval(x - 3, 5, x + 3, 11, outline=color, width=1.3)
        empty = not projects
        if empty and not self.proj_empty.winfo_manager():
            self.proj_empty.pack(fill="x", padx=4, pady=(0, 2))
        elif not empty and self.proj_empty.winfo_manager():
            self.proj_empty.pack_forget()
        more = len(projects) - len(self.proj_rows)
        if more > 0:
            self._set_text("pmore", self.proj_more, f"{more} more project{'s' if more > 1 else ''}")
            if not self.proj_more.winfo_manager():
                self.proj_more.pack(fill="x", padx=4, pady=(1, 0))
        elif self.proj_more.winfo_manager():
            self.proj_more.pack_forget()

    def _project_colors(self) -> tuple:
        """((r, g, b), total, working) per project, for the coloured arcs on the resting icon."""
        out = []
        for g in ((self.data or {}).get("projects") or {}).get("projects") or []:
            h = g.get("color") or "#22d3ee"
            out.append(((int(h[1:3], 16), int(h[3:5], 16), int(h[5:7], 16)), g.get("total", 1), g.get("working", 0)))
        return tuple(out)

    def _nearest_timer(self) -> float | None:
        """Fraction of the soonest countdown still to go (for the ring round the orb), or None."""
        running = [t for t in (self.data or {}).get("timers") or [] if t["kind"] == "timer" and t.get("seconds")]
        if not running:
            return None
        soonest = min(running, key=lambda t: t["due"])
        return max(0.0, min(1.0, (soonest["due"] - time.time()) / soonest["seconds"]))

    def _draw_orb(self):
        """Smooth orb frames: ~30 fps while something is happening, ~15 fps at rest; time-based, so a late frame
        never makes the motion stutter."""
        now = time.time()
        state = self._state()
        approval = bool(self.data and self.data.get("approvals"))
        resting = state in ("waiting", "idle", "offline") and not approval and not (self.data or {}).get("timers")
        if now - self._last < (1 / 15 if resting else 1 / 30):
            return
        self.orb.t += min(0.2, now - self._last)
        self._last = now
        self.orb.state, self.orb.approval = state, approval
        self.orb.timer = self._nearest_timer()
        self.orb.projects = self._project_colors()
        if self.panel_fade.shown and self.orb.timer is not None:
            self._render_timers()  # the countdown ticks every second, not only when new data arrives
        self.orb.color = ease_color(self.orb.color, ORB_COLORS.get(state, ORB_COLORS["idle"]), 0.16)
        try:
            self.layered.show(render(self.orb))
        except Exception:  # noqa: BLE001 - fall back to the flat drawing rather than lose the icon
            self.layered = None
            try:
                self.root.attributes("-transparentcolor", KEY)
            except tk.TclError:
                pass

    def _render(self):
        d = self.data
        state = self._state()
        if not d:
            self._set_text("note", self.note, "JARVIS isn't running")
            return
        self._render_projects(d.get("projects") or {})
        ex = d.get("last") or {}
        self._set_text("you", self.you, "You: " + elide(ex.get("request") or "-", 80))
        self._set_text("reply", self.reply, elide(ex.get("reply") or "-", 360))
        talking = state == "speaking" or bool((d.get("voice") or {}).get("talking"))
        self.pause_btn.set_fg(FG if talking else "#475569")  # lit only while there is something to pause
        self.sound_btn.set_text("🔊" if d.get("sound", True) else "🔇")
        if not self._texts.get("note_override"):
            timer = self._timer_text()
            self._set_text("note", self.note, timer or LABELS.get(state, state), fg=ACCENT if timer else MUTED)
        self._place_panel()

    def _render_timers(self):  # countdowns tick every second in the status spot
        if not self._texts.get("note_override"):
            timer = self._timer_text()
            if timer:
                self._set_text("note", self.note, timer, fg=ACCENT)

    # -- actions --------------------------------------------------------------------------------------------------------
    def _flash(self, text: str):
        self._texts["note_override"] = "1"
        self.note.configure(text=text)
        self.root.after(2500, lambda: self._texts.pop("note_override", None))

    def _bg(self, fn, done_note: str = ""):
        def run():
            try:
                fn()
                if done_note:
                    self.root.after(0, lambda: self._flash(done_note))
            except Exception as e:  # noqa: BLE001
                msg = str(e)
                if isinstance(e, urllib.error.HTTPError):
                    try:
                        msg = json.loads(e.read()).get("detail", msg)
                    except ValueError:
                        pass
                self.root.after(0, lambda: self._flash(elide(msg, 40)))
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

    def _decide_current(self, approve: bool):
        aid = self.current_approval
        if not aid:
            return
        self.deciding.add(aid)  # hide it at once; the next poll shows the next one, if any
        self._render_bar((self.data or {}).get("approvals") or [])

        def go():
            try:
                self.api.call("POST", f"/api/approvals/{aid}/decide", {"approve": approve, "scope": "once"}, timeout=120)
            finally:
                self.root.after(3000, lambda: self.deciding.discard(aid))
        self._bg(go, "allowed" if approve else "denied")

    def _pause(self):
        """Stop JARVIS talking now (the rest of the current reply stays silent; the text still arrives)."""
        self._bg(lambda: self.api.call("POST", "/api/voice/stop"), "paused")

    def _toggle_sound(self):
        """Spoken replies on/off (the voice setting speak_replies - the server and the window both follow it)."""
        on = not (self.data or {}).get("sound", True)
        self.sound_btn.set_text("🔊" if on else "🔇")
        if self.data is not None:
            self.data["sound"] = on

        def go():
            self.api.call("PUT", "/api/settings", {"voice": {"speak_replies": on}})
            if not on:
                self.api.call("POST", "/api/voice/stop")  # muting also silences what is being said now
        self._bg(go, "sound on" if on else "sound off")

    def _open_project(self, row):
        """Bring forward the window the project's AI runs in (or open its folder when no window is found)."""
        key = getattr(row, "project_key", "")
        if not key:
            return
        import urllib.parse

        def go():
            r = self.api.call("GET", "/api/projects/window?key=" + urllib.parse.quote(key))
            if r.get("hwnd") and focus_window(int(r["hwnd"])):
                return
            if r.get("folder") and os.path.isdir(r["folder"]):
                os.startfile(r["folder"])  # noqa: S606 - the owner's own project folder
            else:
                raise RuntimeError("no window found")
        self._bg(go)

    def _open_window(self):
        def go():
            from types import SimpleNamespace

            from . import desktop_app

            desktop_app.bring_up(SimpleNamespace(settings=self.settings))
        self._bg(go)

    def run(self):
        self.root.mainloop()


def focus_window(hwnd: int) -> bool:
    """Bring a top-level window forward (restored if minimised). Called by the icon process, which the owner just
    clicked - Windows only lets that process take the foreground."""
    if os.name != "nt":
        return False
    import ctypes

    u = ctypes.windll.user32
    if not u.IsWindow(hwnd):
        return False
    if u.IsIconic(hwnd):
        u.ShowWindow(hwnd, 9)  # SW_RESTORE
    u.SetForegroundWindow(hwnd)
    return True


def _lighter(color: str) -> str:
    r, g, b = (int(color[i:i + 2], 16) for i in (1, 3, 5))
    return "#{:02x}{:02x}{:02x}".format(*(min(255, int(v + (255 - v) * 0.18)) for v in (r, g, b)))


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
